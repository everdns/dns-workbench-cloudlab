import logging
import os
import statistics
import time

from benchmark.remote import scp_from, ssh_run_background

log = logging.getLogger(__name__)


# Metric id -> (collectl column name, conversion fn, output key prefix)
# For columns that are computed from multiple collectl columns, we handle
# those in _extract_series directly.
_SINGLE_COLUMN_METRICS = [
    ("cpu_totl",   "[CPU]Totl%",   lambda x: x,        "pct"),
    ("cpu_user",   "[CPU]User%",   lambda x: x,        "pct"),
    ("cpu_sys",    "[CPU]Sys%",    lambda x: x,        "pct"),
    ("mem_used",   "[MEM]Used",    lambda x: x / 1024, "mb"),
    ("mem_tot",    "[MEM]Tot",     lambda x: x / 1024, "mb"),
    ("mem_free",   "[MEM]Free",    lambda x: x / 1024, "mb"),
    ("mem_cached", "[MEM]Cached",  lambda x: x / 1024, "mb"),
    ("net_rx_pkt", "[NET]RxPktTot", lambda x: x,       ""),
    ("net_tx_pkt", "[NET]TxPktTot", lambda x: x,       ""),
]


def start_collectl(config, duration_s, output_file="/tmp/collectl_trail.txt"):
    """Start collectl on the DNS server host in the background.

    Runs `nohup collectl -scndm --plot -c {duration_s} > {output_file}` over SSH.
    Returns subprocess.Popen handle, or None on dry-run.
    """
    server = config["hosts"]["server"]
    cmd = (
        f"nohup collectl -scndm --plot -c {duration_s} > {output_file} 2>/dev/null"
    )
    log.info("Starting collectl on %s: %s", server, cmd)

    if config.get("dry_run"):
        log.info("[DRY RUN] Would execute: ssh %s '%s'", server, cmd)
        return None

    return ssh_run_background(server, cmd)


def wait_collectl(proc, timeout=None):
    """Wait for collectl to finish. Tolerant of None proc."""
    if proc is None:
        return "", ""
    stdout, stderr = proc.communicate(timeout=timeout)
    return stdout, stderr


def collect_collectl_file(config, remote_file, local_path):
    """SCP the collectl trail file back from the server to local_path."""
    server = config["hosts"]["server"]
    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    scp_from(server, remote_file, local_path)
    return local_path


def run_collectl_session(config, runtime_s, remote_output):
    """Start collectl and wait the margin so sampling is warm before the tool.

    duration = runtime_s + 2 * margin (cover pre-tool and post-tool windows).
    Returns {proc, output_file, duration, margin}.
    """
    s3 = config.get("script3", {}) or {}
    margin = int(s3.get("collectl_margin", 5))
    duration = int(runtime_s) + 2 * margin

    proc = start_collectl(config, duration, remote_output)

    if not config.get("dry_run"):
        time.sleep(margin)

    return {
        "proc": proc,
        "output_file": remote_output,
        "duration": duration,
        "margin": margin,
    }


def _find_header_and_rows(path):
    """Find the `#Date Time ...` header and return (columns, data_rows).

    data_rows is a list of list[str] with fields already split.
    """
    columns = None
    rows = []
    with open(path) as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            if line.startswith("#"):
                if columns is None and line.startswith("#Date Time"):
                    columns = line.lstrip("#").split()
                continue
            if columns is None:
                continue
            parts = line.split()
            if len(parts) != len(columns):
                continue
            rows.append(parts)
    return columns, rows


def _extract_series(columns, rows):
    """Return dict of metric_id -> list[float] time series.

    Handles both direct single-column metrics and the composite `net_kb` metric
    (RxKBTot + TxKBTot). Missing columns result in missing keys — logged as
    warnings.
    """
    col_index = {name: i for i, name in enumerate(columns)}
    series = {}

    for metric_id, col_name, conv, _suffix in _SINGLE_COLUMN_METRICS:
        idx = col_index.get(col_name)
        if idx is None:
            log.warning("collectl column '%s' not found for metric '%s'",
                        col_name, metric_id)
            continue
        values = []
        for row in rows:
            try:
                values.append(conv(float(row[idx])))
            except (ValueError, IndexError):
                continue
        series[metric_id] = values

    # cpu_totl fallback: 100 - [CPU]Idle%
    if "cpu_totl" not in series:
        idx_idle = col_index.get("[CPU]Idle%")
        if idx_idle is not None:
            values = []
            for row in rows:
                try:
                    values.append(100.0 - float(row[idx_idle]))
                except (ValueError, IndexError):
                    continue
            series["cpu_totl"] = values
            log.info("Using 100 - [CPU]Idle%% as fallback for cpu_totl")

    # net_kb = RxKBTot + TxKBTot per row
    rx_idx = col_index.get("[NET]RxKBTot")
    tx_idx = col_index.get("[NET]TxKBTot")
    if rx_idx is not None and tx_idx is not None:
        values = []
        for row in rows:
            try:
                values.append(float(row[rx_idx]) + float(row[tx_idx]))
            except (ValueError, IndexError):
                continue
        series["net_kb"] = values
    else:
        log.warning("collectl columns [NET]RxKBTot / [NET]TxKBTot not found "
                    "for metric 'net_kb'")

    return series


def _median_peak_keys(metric_id):
    """Return (median_key, peak_key) for a metric id."""
    if metric_id.startswith("cpu_"):
        return f"{metric_id}_median_pct", f"{metric_id}_peak_pct"
    if metric_id.startswith("mem_"):
        return f"{metric_id}_median_mb", f"{metric_id}_peak_mb"
    if metric_id == "net_kb":
        return "net_kb_median_kbps", "net_kb_peak_kbps"
    if metric_id in ("net_rx_pkt", "net_tx_pkt"):
        return f"{metric_id}_median", f"{metric_id}_peak"
    return f"{metric_id}_median", f"{metric_id}_peak"


def parse_collectl_file(path, margin_s):
    """Parse a collectl --plot trail file and aggregate median + peak.

    Drops the first `margin_s` and last `margin_s` rows (1 sample/sec) before
    aggregating. Returns a dict of up to 20 scalar metrics (see plan) or an
    empty dict on failure. Never raises.
    """
    try:
        columns, rows = _find_header_and_rows(path)
        if columns is None:
            log.warning("collectl file %s has no '#Date Time' header", path)
            return {}
        if not rows:
            log.warning("collectl file %s has no data rows", path)
            return {}

        margin = max(0, int(margin_s))
        if margin > 0 and len(rows) > 2 * margin:
            cropped = rows[margin:-margin]
        else:
            cropped = rows

        if not cropped:
            log.warning("collectl file %s has no rows after cropping margin=%d",
                        path, margin)
            return {}

        series = _extract_series(columns, cropped)
        out = {}
        for metric_id, values in series.items():
            if not values:
                continue
            median_key, peak_key = _median_peak_keys(metric_id)
            out[median_key] = float(statistics.median(values))
            out[peak_key] = float(max(values))
        return out
    except Exception as e:
        log.warning("Failed to parse collectl file %s: %s", path, e)
        return {}


# Flat list of all 20 output keys — used by charts.py to guard empty figures.
COLLECTL_KEYS = []
for _mid, _col, _conv, _suffix in _SINGLE_COLUMN_METRICS:
    _mk, _pk = _median_peak_keys(_mid)
    COLLECTL_KEYS.extend([_mk, _pk])
COLLECTL_KEYS.extend(["net_kb_median_kbps", "net_kb_peak_kbps"])
