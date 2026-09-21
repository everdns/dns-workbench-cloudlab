#!/usr/bin/env python3
"""Grid search over BIND options, scored by max sustainable QPS.

For each point in the grid:

  1. Propose a configuration (the next combination of parameter values).
  2. Attempt the configuration update on the name server.
  3a. On success, run max_sustainable_qps.py and record the result.
  3b. On failure, record why and return to step 1.

Parameters and their ranges come from a grid config YAML; see grid_search.yaml.

Run from the repository root:

    python3 -m optimization.grid_search --output-dir results
"""
import argparse
import csv
import itertools
import json
import logging
import os
import re
import subprocess
import sys
import time

import yaml

from load_testing_benchmark.benchmark.config import load_config
from load_testing_benchmark.benchmark.results import ResultStore
from optimization.bind_config import (
    BindConfigError,
    install_options,
    load_base_options,
    render_options,
    restore_base,
)

log = logging.getLogger("grid_search")

SCRIPT_NAME = "grid_search"
OPTIMIZATION_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(OPTIMIZATION_DIR)
BENCHMARK_DIR = os.path.join(REPO_ROOT, "load_testing_benchmark")
MAX_SUSTAINABLE_QPS = os.path.join(BENCHMARK_DIR, "scripts", "max_sustainable_qps.py")
DEFAULT_GRID = os.path.join(OPTIMIZATION_DIR, "grid_search.yaml")

EXIT_RESTORE_FAILED = 3


def load_grid(path):
    with open(path) as f:
        grid = yaml.safe_load(f)

    params = grid.get("parameters")
    if not params:
        raise ValueError(f"{path}: no 'parameters' list configured")
    for p in params:
        if "name" not in p:
            raise ValueError(f"{path}: a parameter entry is missing 'name'")
        if not p.get("values"):
            raise ValueError(f"{path}: parameter '{p['name']}' has no values")
    return grid


def propose_configurations(parameters):
    """Yield every combination of parameter values as an ordered dict."""
    names = [p["name"] for p in parameters]
    for combo in itertools.product(*(p["values"] for p in parameters)):
        yield dict(zip(names, combo))


def point_id(overrides):
    parts = [f"{name}={value}" for name, value in overrides.items()]
    return re.sub(r"[^A-Za-z0-9=_.-]", "_", "__".join(parts))

def restore_or_exit(server, base_text):
    """Put the base config back, or stop the search.

    A failed restore leaves the name server holding an unknown config, so every
    later point would be measuring something other than what it proposed. There
    is nothing useful to do but stop and say so.
    """
    try:
        restore_base(server, base_text)
    except BindConfigError as e:
        log.error("Failed to restore the base config on %s: %s", server, e)
        log.error("The name server is left in a modified state. Reinstall it "
                  "with ns_software/bind/install.sh before running again.")
        raise SystemExit(EXIT_RESTORE_FAILED)


def run_evaluation(search_config, dns_service, output_dir, timeout=None):
    """Run max_sustainable_qps.py for the installed config.

    Returns (exit_code, summary). max_sustainable_qps.py exits 0 on success,
    1 when the DNS service failed to start, and 2 on a bad config. exit_code is
    None if the run had to be killed for exceeding ``timeout``.
    """
    cmd = [
        sys.executable, MAX_SUSTAINABLE_QPS,
        "--config", search_config,
        "--dns-service", dns_service,
        "--output-dir", output_dir,
    ]

    log.info("Running: %s", " ".join(cmd))
    try:
        proc = subprocess.run(cmd, cwd=BENCHMARK_DIR, timeout=timeout,
                              capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        log.error("Evaluation timed out after %ss", timeout)
        return None, None

    os.makedirs(output_dir, exist_ok=True)
    with open(os.path.join(output_dir, "max_sustainable_qps.log"), "w") as f:
        f.write(proc.stdout or "")
        f.write(proc.stderr or "")

    if proc.returncode != 0:
        log.error("max_sustainable_qps.py exited %d", proc.returncode)
        return proc.returncode, None

    return proc.returncode, read_summary(output_dir)


def read_summary(output_dir):
    """Read search_summary.json, which ResultStore writes as a list of one row."""
    path = os.path.join(output_dir, "max_sustainable_qps", "search_summary.json")
    if not os.path.exists(path):
        log.error("No search summary at %s", path)
        return None
    with open(path) as f:
        rows = json.load(f)
    if not rows:
        return None
    return rows[0]


def min_fidelity(output_dir):
    """Lowest qps_fidelity_pct across the run's trials, or None.

    A run whose fidelity dipped below the threshold was limited by the load
    generator, not by the server, so its max QPS is not a server measurement.
    """
    path = os.path.join(output_dir, "max_sustainable_qps", "trial_results.csv")
    if not os.path.exists(path):
        return None
    values = []
    with open(path) as f:
        for row in csv.DictReader(f):
            try:
                values.append(float(row["qps_fidelity_pct"]))
            except (KeyError, TypeError, ValueError):
                continue
    return min(values) if values else None


def completed_points(output_dir):
    """Point ids already recorded in a previous run, for --resume."""
    path = os.path.join(output_dir, SCRIPT_NAME, "grid_results.csv")
    if not os.path.exists(path):
        return set()
    with open(path) as f:
        return {row["point_id"] for row in csv.DictReader(f) if row.get("point_id")}


def main():
    parser = argparse.ArgumentParser(
        description="Grid search over BIND options scored by max sustainable QPS"
    )
    parser.add_argument("--grid", default=DEFAULT_GRID,
                        help="Grid config YAML defining parameters and ranges")
    parser.add_argument("--search-config",
                        help="Config passed to max_sustainable_qps.py "
                             "(overrides the grid config's search_config)")
    parser.add_argument("--dns-service",
                        help="DNS service to evaluate (overrides the grid config)")
    parser.add_argument("--server", help="Name server host (overrides the search config)")
    parser.add_argument("--output-dir", default="results",
                        help="Output directory for results")
    parser.add_argument("--point-timeout", type=int, default=21600,
                        help="Seconds to allow one evaluation before killing it")
    parser.add_argument("--resume", action="store_true",
                        help="Skip points already present in grid_results.csv")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print each proposed configuration and stop")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    grid = load_grid(args.grid)
    parameters = grid["parameters"]
    search_config = args.search_config or grid.get("search_config")
    search_config = search_config if os.path.isabs(search_config) else os.path.join(BENCHMARK_DIR, search_config)
    dns_service = args.dns_service or grid.get("dns_service", "ns_bind")
    search_output_dir = args.output_dir if os.path.isabs(args.output_dir) else os.path.join(OPTIMIZATION_DIR, args.output_dir)
    if not search_config:
        log.error("No search_config set in %s and none given on the CLI", args.grid)
        return 2

    config = load_config(search_config)
    server = args.server or config.get("hosts", {}).get("server")
    if not server:
        log.error("No name server host configured")
        return 2

    base_text = load_base_options()
    points = list(propose_configurations(parameters))

    log.info("=== Grid search over %d parameter(s), %d point(s) ===",
             len(parameters), len(points))
    for p in parameters:
        log.info("  %s: %s", p["name"], p["values"])
    log.info("Name server: %s, service: %s, search config: %s",
             server, dns_service, search_config)

    if args.dry_run:
        for overrides in points:
            print(f"--- {point_id(overrides)} ---")
            install_options(server, render_options(base_text, overrides),
                            dry_run=True)
        return 0

    done = completed_points(search_output_dir) if args.resume else set()
    if done:
        log.info("Resuming: %d point(s) already recorded", len(done))

    store = ResultStore(search_output_dir)
    started = time.time()
    best = None

    try:
        for index, overrides in enumerate(points, start=1):
            pid = point_id(overrides)
            if pid in done:
                log.info("[%d/%d] Skipping %s (already recorded)",
                         index, len(points), pid)
                continue

            log.info("[%d/%d] Proposing %s", index, len(points), pid)
            row = {
                "point_id": pid,
                "index": index,
                **overrides,
                "status": "",
                "error": "",
                "max_qps_passed": "",
                "max_qps_tested": "",
                "hit_max_qps_ceiling": "",
                "total_trials_run": "",
                "search_duration_s": "",
                "min_qps_fidelity_pct": "",
                "eval_exit_code": "",
                "run_dir": "",
            }

            try:
                rendered = render_options(base_text, overrides)
                install_options(server, rendered)
            except BindConfigError as e:
                log.error("Configuration update failed: %s", e)
                row["status"] = "config_failed"
                row["error"] = str(e)
                store.add_result(row)
                store.export_csv(SCRIPT_NAME, "grid_results.csv")
                restore_or_exit(server, base_text)
                continue

            run_dir = os.path.join(search_output_dir, SCRIPT_NAME, "runs", pid)
            row["run_dir"] = run_dir
            try:
                exit_code, summary = run_evaluation(
                    search_config, dns_service, run_dir,
                    timeout=args.point_timeout,
                )
            finally:
                restore_or_exit(server, base_text)

            row["eval_exit_code"] = "" if exit_code is None else exit_code
            if exit_code is None:
                row["status"] = "timeout"
            elif summary is None:
                row["status"] = "eval_failed"
            else:
                row["status"] = "ok"
                row["max_qps_passed"] = summary["max_qps_passed"]
                row["max_qps_tested"] = summary["max_qps_tested"]
                row["hit_max_qps_ceiling"] = summary["hit_max_qps_ceiling"]
                row["total_trials_run"] = summary["total_trials_run"]
                row["search_duration_s"] = summary["search_duration_s"]
                fidelity = min_fidelity(run_dir)
                row["min_qps_fidelity_pct"] = "" if fidelity is None else fidelity

                log.info("[%d/%d] %s -> %d QPS", index, len(points), pid,
                         summary["max_qps_passed"])
                if best is None or summary["max_qps_passed"] > best[1]:
                    best = (pid, summary["max_qps_passed"])

            store.add_result(row)
            store.export_csv(SCRIPT_NAME, "grid_results.csv")
            store.export_json(SCRIPT_NAME, "grid_results.json")
    finally:
        # Export before restoring so results survive a restore failure.
        path = store.export_csv(SCRIPT_NAME, "grid_results.csv")
        store.export_json(SCRIPT_NAME, "grid_results.json")
        if path:
            log.info("Results written to %s", path)
        restore_or_exit(server, base_text)

    log.info("=== Grid search finished in %.1fs ===", time.time() - started)
    if best:
        log.info("Best: %s at %d QPS", best[0], best[1])
    else:
        log.warning("No point produced a usable result")
    return 0


if __name__ == "__main__":
    sys.exit(main())
