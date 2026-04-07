import csv
import json
import os
import re
from dataclasses import asdict, dataclass, field


@dataclass
class ToolResult:
    queries_sent: int = 0
    queries_completed: int = 0
    queries_lost: int = 0
    achieved_qps: float = 0.0
    run_time: float = 0.0
    avg_latency: float | None = None
    min_latency: float | None = None
    max_latency: float | None = None
    latency_stddev: float | None = None
    percentiles: dict[str, float] = field(default_factory=dict)
    response_codes: dict[str, int] = field(default_factory=dict)
    raw_output: str = ""


@dataclass
class DnsResponderResult:
    rx_total: int = 0
    tx_total: int = 0
    parse_errors: int = 0
    drops: int = 0
    avg_rx_pps: float = 0.0
    avg_tx_pps: float = 0.0
    rx_qps: float = 0.0
    actual_duration_secs: float = 0.0
    raw_output: str = ""


@dataclass
class AccuracyMetrics:
    interval_ms: int = 0
    mean_qps: float = 0.0
    stddev: float = 0.0
    max_deviation: float = 0.0
    expected_pps: float = 0.0
    mean_pps: float = 0.0
    pps_stddev: float = 0.0
    pps_max_deviation: float = 0.0


def parse_dns_responder_output(text):
    """Parse dns_responder stdout into a DnsResponderResult."""
    result = DnsResponderResult(raw_output=text)

    def parse_int(pattern):
        m = re.search(pattern, text)
        if m:
            return int(m.group(1).replace(",", ""))
        return 0

    def parse_float(pattern):
        m = re.search(pattern, text)
        if m:
            return float(m.group(1).replace(",", ""))
        return 0.0

    result.rx_total = parse_int(r"RX total:\s+([\d,]+)") or parse_int(r"Total packets:\s+([\d,]+)")
    result.tx_total = parse_int(r"TX total:\s+([\d,]+)")
    result.parse_errors = parse_int(r"Parse errors:\s+([\d,]+)")
    result.drops = parse_int(r"Drops:\s+([\d,]+)")
    result.avg_rx_pps = parse_float(r"Avg RX:\s+([\d,.]+)\s+pps") or parse_float(r"Avg throughput:\s+([\d,.]+)\s+pps")
    result.avg_tx_pps = parse_float(r"Avg TX:\s+([\d,.]+)\s+pps")
    result.rx_qps = parse_float(r"RX QPS:\s+([\d,.]+)\s+qps") or parse_float(r"Actual throughput:\s+([\d,.]+)\s+pps")
    result.actual_duration_secs = parse_float(r"Actual traffic window:\s+([\d,.]+)s")

    return result


def compute_accuracy_metrics(timestamps_ns, target_qps, runtime_s, crop_s=0):
    """Compute QPS accuracy metrics from dns_responder timestamps.

    Args:
        timestamps_ns: list of nanosecond timestamps (ints)
        target_qps: target queries per second
        runtime_s: test runtime in seconds
        crop_s: seconds to trim from both the start and end of the timestamp
                 range before computing metrics (default: 0, no cropping)

    Returns:
        dict mapping interval label to AccuracyMetrics
    """
    if not timestamps_ns:
        return {}

    if crop_s > 0:
        crop_ns = int(crop_s * 1_000_000_000)
        t_min = timestamps_ns[0] + crop_ns
        t_max = timestamps_ns[-1] - crop_ns
        if t_min >= t_max:
            return {}
        timestamps_ns = [ts for ts in timestamps_ns if t_min <= ts <= t_max]
        if not timestamps_ns:
            return {}

    intervals = {
        1000: ("1s", 1_000_000_000),
        100: ("100ms", 100_000_000),
        10: ("10ms", 10_000_000),
    }

    results = {}
    for interval_ms, (label, interval_ns) in intervals.items():
        bins = {}
        for ts in timestamps_ns:
            bin_idx = ts // interval_ns
            bins[bin_idx] = bins.get(bin_idx, 0) + 1

        if len(bins) < 3:
            results[label] = AccuracyMetrics(interval_ms=interval_ms)
            continue

        # Trim first and last bin (partial intervals)
        sorted_keys = sorted(bins.keys())
        trimmed = [bins[k] for k in sorted_keys[1:-1]]

        if not trimmed:
            results[label] = AccuracyMetrics(interval_ms=interval_ms)
            continue

        # Convert counts to QPS equivalent
        seconds_per_interval = interval_ns / 1_000_000_000
        qps_values = [count / seconds_per_interval for count in trimmed]

        n = len(qps_values)
        mean = sum(qps_values) / n
        variance = sum((v - mean) ** 2 for v in qps_values) / n
        stddev = variance ** 0.5
        max_dev = max(abs(v - target_qps) for v in qps_values)

        # PPS: raw packet counts per interval
        expected_pps = target_qps * seconds_per_interval
        pps_values = trimmed  # raw counts per interval
        pps_mean = sum(pps_values) / n
        pps_variance = sum((v - pps_mean) ** 2 for v in pps_values) / n
        pps_stddev = pps_variance ** 0.5
        pps_max_dev = max(abs(v - expected_pps) for v in pps_values)

        results[label] = AccuracyMetrics(
            interval_ms=interval_ms,
            mean_qps=mean,
            stddev=stddev,
            max_deviation=max_dev,
            expected_pps=expected_pps,
            mean_pps=pps_mean,
            pps_stddev=pps_stddev,
            pps_max_deviation=pps_max_dev,
        )

    return results


def read_timestamps_file(path):
    """Read all nanosecond timestamps from a file, skipping comments and blanks.

    Returns:
        list of int (nanosecond timestamps)
    """
    timestamps = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                timestamps.append(int(line))
    return timestamps


def compute_actual_runtime(timestamps_ns):
    """Compute runtime in nanoseconds from a list of timestamps.

    Returns:
        int: last - first timestamp in nanoseconds, or 0 if insufficient data
    """
    if len(timestamps_ns) < 2:
        return 0
    return timestamps_ns[-1] - timestamps_ns[0]


def read_first_last_timestamp(path):
    """Read only the first and last timestamps from a file, returning runtime in ns.

    Uses subprocess head/tail to avoid reading the entire file.
    """
    import subprocess

    try:
        head_lines = subprocess.check_output(
            ["head", "-n", "5", path], text=True
        ).strip().splitlines()
        tail_lines = subprocess.check_output(
            ["tail", "-n", "2", path], text=True
        ).strip().splitlines()
    except (subprocess.CalledProcessError, OSError):
        return 0

    first_line = None
    for line in head_lines:
        line = line.strip()
        if line and not line.startswith("#"):
            first_line = line
            break

    last_line = None
    for line in reversed(tail_lines):
        line = line.strip()
        if line:
            last_line = line
            break

    if not first_line or not last_line:
        return 0

    first = int(first_line)
    last = int(last_line)
    if first == last:
        return 0
    return last - first


class ResultStore:
    """Stores and exports benchmark results."""

    def __init__(self, output_dir):
        self.output_dir = output_dir
        self.results = []

    def _ensure_dir(self, *parts):
        path = os.path.join(self.output_dir, *parts)
        os.makedirs(path, exist_ok=True)
        return path

    def save_raw_output(self, script, filename, content):
        """Save raw tool or dns_responder output."""
        raw_dir = self._ensure_dir(script, "raw")
        path = os.path.join(raw_dir, filename)
        with open(path, "w") as f:
            f.write(content)
        return path

    def save_timestamps(self, script, filename, content):
        """Save a timestamps file."""
        ts_dir = self._ensure_dir(script, "timestamps")
        path = os.path.join(ts_dir, filename)
        with open(path, "w") as f:
            f.write(content)
        return path

    def add_result(self, row):
        """Add a result row (dict)."""
        self.results.append(row)

    def export_csv(self, script, filename="results.csv"):
        """Export accumulated results to CSV."""
        if not self.results:
            return
        out_dir = self._ensure_dir(script)
        path = os.path.join(out_dir, filename)
        keys = list(dict.fromkeys(k for row in self.results for k in row))
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader()
            writer.writerows(self.results)
        return path

    def export_json(self, script, filename="results.json"):
        """Export accumulated results to JSON."""
        if not self.results:
            return
        out_dir = self._ensure_dir(script)
        path = os.path.join(out_dir, filename)
        with open(path, "w") as f:
            json.dump(self.results, f, indent=2, default=str)
        return path

    def clear(self):
        """Clear accumulated results for next script."""
        self.results = []
