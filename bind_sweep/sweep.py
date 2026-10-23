#!/usr/bin/env python3
"""Benchmark default-config BIND releases from the last N years.

For every selected version: download the source on the name server, build it,
verify it answers for workbench.lan with the repo's default named.conf.local /
named.conf.options, then run optimization/max_sustainable_qps.py's search with
the parameters in optimization/agent_search.yaml.

Run from a host that can ssh to the server and clients in the config (normally
test_host_0), from the repo root:

    python3 bind_sweep/sweep.py --list-only
    python3 bind_sweep/sweep.py --versions 9.20.30
    python3 bind_sweep/sweep.py            # full quarterly sweep
"""
import argparse
import csv
import datetime as dt
import logging
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO))

from optimization.max_sustainable_qps import (  # noqa: E402
    run_max_sustainable_qps, scp_to, ssh_run,
)

log = logging.getLogger("bind_sweep")

BASE_URL = "https://downloads.isc.org/isc/bind9"
REMOTE_DIR = "/opt/bind-sweep"
SRC_DIR = "/opt/bind-src"
MIN_VERSION = (9, 16, 20)
SUMMARY_FIELDS = [
    "version", "branch", "release_date", "status", "max_qps_passed",
    "max_qps_tested", "hit_max_qps_ceiling", "output_dir", "detail",
]
# One row of the Apache index: version directory name + its "Last modified" date.
LISTING_ROW = re.compile(
    r'<a href="(9\.\d+\.\d+)/">.*?class="indexcollastmod">(\d{4}-\d{2}-\d{2})')


# ---------------------------------------------------------------------------
# Version discovery and selection
# ---------------------------------------------------------------------------

def vkey(v):
    return tuple(int(x) for x in v.split("."))


def branch_of(v):
    return ".".join(v.split(".")[:2])


def list_versions():
    """{version: release date}, both read from the downloads index page."""
    with urllib.request.urlopen(f"{BASE_URL}/", timeout=60) as r:
        html = r.read().decode()
    return {v: dt.date.fromisoformat(d) for v, d in LISTING_ROW.findall(html)
            if vkey(v) >= MIN_VERSION}


def minus_months(d, n):
    y, m = divmod(d.year * 12 + d.month - 1 - n, 12)
    return dt.date(y, m + 1, min(d.day, 28))


def select_versions(dates, today, years, stride_months):
    """Newest release per branch on/before each stride point, deduplicated."""
    picked = {}
    for k in range(years * 12 // stride_months + 1):
        point = minus_months(today, k * stride_months)
        by_branch = {}
        for v, d in dates.items():
            if d <= point and (branch_of(v) not in by_branch
                               or vkey(v) > vkey(by_branch[branch_of(v)])):
                by_branch[branch_of(v)] = v
        for v in by_branch.values():
            picked.setdefault(v, []).append(point)
    return sorted(picked, key=vkey), picked


# ---------------------------------------------------------------------------
# Remote helpers
# ---------------------------------------------------------------------------

def remote(server, cmd, timeout=None, check=True):
    res = ssh_run(server, cmd, timeout=timeout)
    if check and res.returncode != 0:
        raise RuntimeError(f"`{cmd}` failed on {server} (rc={res.returncode}):\n"
                           f"{res.stdout[-2000:]}\n{res.stderr[-2000:]}")
    return res


def push(server, local, remote_path):
    """Copy a local file to a root-owned remote path."""
    tmp = f"/tmp/sweep-{Path(local).name}"
    scp_to(server, str(local), tmp)
    remote(server, f"sudo mkdir -p $(dirname {remote_path}) && "
                   f"sudo cp {tmp} {remote_path} && rm -f {tmp}")


def setup_server_scripts(server):
    remote(server, f"sudo mkdir -p {REMOTE_DIR} {SRC_DIR}")
    for name in ("build_bind.sh", "configure_bind.sh",
                 "start_version.sh", "stop_version.sh"):
        push(server, HERE / name, f"{REMOTE_DIR}/{name}")
    remote(server, f"sudo chmod +x {REMOTE_DIR}/*.sh")
    for name in ("named.conf.local", "named.conf.options"):
        push(server, REPO / "ns_software" / "bind" / name, f"{REMOTE_DIR}/{name}")
    push(server, REPO / "zone_file_defaults" / "db.dns64perf.test",
         f"{REMOTE_DIR}/db.dns64perf.test")


def generate_and_install_zone(config, work_dir):
    """Generate the /16 workbench.lan zone + query file; install on server and clients."""
    out = work_dir / "zone"
    out.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, "-I", str(REPO / "zone_generation" / "zone_generator.py"),
         "--config", str(REPO / "zone_generation" / "config.json"),
         "--sld", "workbench.lan", "--base-subnet", "10.0.0.0",
         "--num-records", "65536", "--num-ips", "65536",
         "--max-records-per-file", "65536", "--out-dir", str(out)],
        check=True)
    zones = sorted(out.glob("db.workbench.lan*"))
    queries = sorted(out.glob("dnsperf_input_*"))
    if len(zones) != 1 or len(queries) != 1:
        raise RuntimeError(f"expected one zone and one query file in {out}, "
                           f"got {zones} / {queries}")
    server = config["hosts"]["server"]
    push(server, zones[0], "/etc/bind/db.workbench.lan")
    for client in config["hosts"]["clients"]:
        scp_to(client, str(queries[0]), config["input_file"])
    return queries[0]


def download(server, v):
    url = f"{BASE_URL}/{v}/bind-{v}.tar.xz"
    dest = f"{SRC_DIR}/bind-{v}.tar.xz"
    remote(server, f"[ -s {dest} ] || sudo curl -fsSL -o {dest} {url}", timeout=600)
    sig = remote(server, f"curl -fsSL {url}.sha256.asc", check=False)
    want = re.search(r"\b[0-9a-f]{64}\b", sig.stdout or "")
    if want:
        got = remote(server, f"sha256sum {dest}").stdout.split()[0]
        if got != want.group(0):
            remote(server, f"sudo rm -f {dest}")
            raise RuntimeError(f"sha256 mismatch for {v}: {got} != {want.group(0)}")
    else:
        log.warning("no sha256 published for %s; checksum not verified", v)


def verify(config, v, sample_query):
    """Start this version with the default config and query workbench.lan."""
    server = config["hosts"]["server"]
    remote(server, f"echo {v} | sudo tee {REMOTE_DIR}/current >/dev/null")
    remote(server, f"{REMOTE_DIR}/configure_bind.sh {v} {REMOTE_DIR}/named.conf.options "
                   f"{REMOTE_DIR}/named.conf.local {REMOTE_DIR}/db.dns64perf.test",
           timeout=120)
    ver = remote(server, f"/opt/bind-{v}/sbin/named -v").stdout
    if v not in ver:
        raise RuntimeError(f"named -v reports {ver.strip()!r}, expected {v}")
    try:
        remote(server, f"{REMOTE_DIR}/start_version.sh", timeout=60)
        qname, qtype = sample_query
        for name, rtype in [("workbench.lan", "SOA"), (qname, qtype)]:
            for _ in range(20):
                out = remote(server, f"dig @{server} {name} {rtype} +norecurse "
                                     f"+time=2 +tries=1", check=False).stdout
                if "status: NOERROR" in out:
                    break
                remote(server, "sleep 1")
            else:
                raise RuntimeError(f"no NOERROR for {name} {rtype}:\n{out[-800:]}")
            if name == "workbench.lan" and not re.search(r"flags:[^;]*\baa\b", out):
                raise RuntimeError(f"answer for {name} not authoritative:\n{out[-800:]}")
    finally:
        remote(server, f"{REMOTE_DIR}/stop_version.sh", check=False)


def first_query(path):
    with open(path) as f:
        name, qtype = f.readline().split()[:2]
    return name, qtype


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def done_versions(summary_csv):
    if not summary_csv.exists():
        return set()
    with open(summary_csv) as f:
        return {r["version"] for r in csv.DictReader(f) if r["status"] == "ok"}


def append_row(summary_csv, row):
    new = not summary_csv.exists()
    with open(summary_csv, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SUMMARY_FIELDS)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in SUMMARY_FIELDS})


def benchmark_config(base_cfg):
    """Harness config: agent_search.yaml minus agent/parameters, with our start/stop."""
    cfg = {k: base_cfg[k] for k in (
        "hosts", "input_file", "dns_service", "tool", "threads",
        "ports_per_thread", "timeout", "pause_between_runs", "max_sustainable_qps")}
    for key in ("client_interface", "client_interfaces"):
        if key in base_cfg:
            cfg[key] = base_cfg[key]
    cfg["dns_services"] = {"start_script": f"{REMOTE_DIR}/start_version.sh",
                           "stop_script": f"{REMOTE_DIR}/stop_version.sh"}
    return cfg


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(REPO / "optimization" / "agent_search.yaml"))
    ap.add_argument("--output-dir", default=str(REPO / "results" / "bind-sweep"))
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--stride-months", type=int, default=3)
    ap.add_argument("--versions", help="comma-separated versions; skips date-based selection")
    ap.add_argument("--list-only", action="store_true",
                    help="print the selected versions and exit")
    ap.add_argument("--skip-zone", action="store_true",
                    help="do not regenerate/install the zone and query file")
    ap.add_argument("--force", action="store_true",
                    help="re-run versions already recorded as ok")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    out_root = Path(args.output_dir)
    out_root.mkdir(parents=True, exist_ok=True)
    dates = list_versions()
    if args.versions:
        versions = sorted(args.versions.split(","), key=vkey)
        picked = {}
    else:
        versions, picked = select_versions(dates, dt.date.today(), args.years,
                                           args.stride_months)
    print(f"{'version':10} {'released':10}  oldest stride point")
    for v in versions:
        pts = picked.get(v)
        print(f"{v:10} {str(dates.get(v, '?')):10}  {pts[-1] if pts else '-'}")
    print(f"{len(versions)} versions")
    if args.list_only:
        return 0

    with open(args.config) as f:
        base_cfg = yaml.safe_load(f)
    server = base_cfg["hosts"]["server"]
    summary_csv = out_root / "summary.csv"
    skip = set() if args.force else done_versions(summary_csv)

    cfg = benchmark_config(base_cfg)
    setup_server_scripts(server)
    if args.skip_zone:
        sample = first_query(base_cfg["input_file"])
    else:
        sample = first_query(generate_and_install_zone(base_cfg, out_root))

    for v in versions:
        row = {"version": v, "branch": branch_of(v),
               "release_date": dates.get(v, "")}
        if v in skip:
            log.info("skipping %s (already ok)", v)
            continue
        stage = "download"
        try:
            log.info("=== BIND %s ===", v)
            download(server, v)
            stage = "build"
            remote(server, f"{REMOTE_DIR}/build_bind.sh {v}", timeout=3 * 3600)
            stage = "verify"
            verify(base_cfg, v, sample)
            stage = "benchmark"
            v_out = out_root / v
            summary, _ = run_max_sustainable_qps(dict(cfg), str(v_out))
            row.update(status="ok", output_dir=str(v_out),
                       max_qps_passed=summary["max_qps_passed"],
                       max_qps_tested=summary["max_qps_tested"],
                       hit_max_qps_ceiling=summary["hit_max_qps_ceiling"])
        except Exception as e:  # noqa: BLE001 - one bad version must not stop the sweep
            log.error("%s failed during %s: %s", v, stage, e)
            row.update(status=f"{stage}_failed", detail=str(e)[-500:].replace("\n", " "))
        append_row(summary_csv, row)
    return 0


if __name__ == "__main__":
    sys.exit(main())
