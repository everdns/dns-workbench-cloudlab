#!/usr/bin/env python3
"""Grid search over BIND options, scored by max sustainable QPS.

For each point in the grid:

  1. Propose a configuration (the next combination of parameter values).
  2. Attempt the configuration update on the name server.
  3a. On success, run the max-sustainable-QPS search and record the result.
  3b. On failure, record why and return to step 1.

grid_search.yaml is the only configuration file this needs: it holds both the
grid parameters and the base search config (hosts, tool, dns service, etc.)
passed to optimization.max_sustainable_qps.

Run from the repository root:

    python3 -m optimization.grid_search --output-dir results
"""
import argparse
import csv
import itertools
import logging
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError

import yaml

from optimization.bind_config import (
    BindConfigError,
    install_options,
    load_base_options,
    render_options,
    restore_base,
)
from optimization.max_sustainable_qps import ResultStore, run_max_sustainable_qps

log = logging.getLogger("grid_search")

SCRIPT_NAME = "grid_search"
OPTIMIZATION_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_GRID = os.path.join(OPTIMIZATION_DIR, "grid_search.yaml")

EXIT_RESTORE_FAILED = 3


def load_grid(path):
    """Load grid_search.yaml. The same dict doubles as the grid parameters and
    the base config passed to run_max_sustainable_qps for every point."""
    with open(path) as f:
        config = yaml.safe_load(f)

    params = config.get("parameters")
    if not params:
        raise ValueError(f"{path}: no 'parameters' list configured")
    for p in params:
        if "name" not in p:
            raise ValueError(f"{path}: a parameter entry is missing 'name'")
        if not p.get("values"):
            raise ValueError(f"{path}: parameter '{p['name']}' has no values")
    return config


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


def run_evaluation(config, output_dir, timeout=None):
    """Run the max-sustainable-QPS search in-process for the installed config.

    Returns (status, summary, trial_rows). status is "ok", "failed", or
    "timeout". Runs in a worker thread so a hung point does not block forever;
    on timeout the thread is abandoned rather than killed, and a fresh shallow
    copy of ``config`` is passed to each call so an abandoned, still-running
    thread from a prior timeout can't race the next point's mutation of shared
    config keys (e.g. "runtime").
    """
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(run_max_sustainable_qps, dict(config), output_dir)
    try:
        summary, trial_rows = future.result(timeout=timeout)
    except FutureTimeoutError:
        log.error("Evaluation timed out after %ss", timeout)
        executor.shutdown(wait=False)
        return "timeout", None, None
    except (ValueError, RuntimeError, TimeoutError) as e:
        log.error("max_sustainable_qps failed: %s", e)
        executor.shutdown(wait=False)
        return "failed", None, None
    executor.shutdown(wait=False)
    return "ok", summary, trial_rows


def min_fidelity(trial_rows):
    """Lowest qps_fidelity_pct across the run's trials, or None.

    A run whose fidelity dipped below the threshold was limited by the load
    generator, not by the server, so its max QPS is not a server measurement.
    """
    values = [row["qps_fidelity_pct"] for row in (trial_rows or [])
              if isinstance(row.get("qps_fidelity_pct"), (int, float))]
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
                        help="Grid config YAML defining parameters and the search config")
    parser.add_argument("--dns-service",
                        help="DNS service to evaluate (overrides the grid config)")
    parser.add_argument("--server", help="Name server host (overrides the grid config)")
    parser.add_argument("--output-dir", default="results",
                        help="Output directory for results")
    parser.add_argument("--point-timeout", type=int, default=21600,
                        help="Seconds to allow one evaluation before moving on")
    parser.add_argument("--resume", action="store_true",
                        help="Skip points already present in grid_results.csv")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print each proposed configuration and stop")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = load_grid(args.grid)
    parameters = config["parameters"]
    if args.dns_service:
        config["dns_service"] = args.dns_service
    if args.server:
        config.setdefault("hosts", {})["server"] = args.server

    dns_service = config.get("dns_service", "ns_bind")
    search_output_dir = args.output_dir if os.path.isabs(args.output_dir) else os.path.join(OPTIMIZATION_DIR, args.output_dir)

    server = config.get("hosts", {}).get("server")
    if not server:
        log.error("No name server host configured")
        return 2

    base_text = load_base_options()
    points = list(propose_configurations(parameters))

    log.info("=== Grid search over %d parameter(s), %d point(s) ===",
             len(parameters), len(points))
    for p in parameters:
        log.info("  %s: %s", p["name"], p["values"])
    log.info("Name server: %s, service: %s", server, dns_service)

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
                status, summary, trial_rows = run_evaluation(
                    config, run_dir, timeout=args.point_timeout,
                )
            finally:
                restore_or_exit(server, base_text)

            row["status"] = status
            if status == "ok":
                row["max_qps_passed"] = summary["max_qps_passed"]
                row["max_qps_tested"] = summary["max_qps_tested"]
                row["hit_max_qps_ceiling"] = summary["hit_max_qps_ceiling"]
                row["total_trials_run"] = summary["total_trials_run"]
                row["search_duration_s"] = summary["search_duration_s"]
                fidelity = min_fidelity(trial_rows)
                row["min_qps_fidelity_pct"] = "" if fidelity is None else fidelity

                log.info("[%d/%d] %s -> %d QPS", index, len(points), pid,
                         summary["max_qps_passed"])
                if best is None or summary["max_qps_passed"] > best[1]:
                    best = (pid, summary["max_qps_passed"])

            store.add_result(row)
            store.export_csv(SCRIPT_NAME, "grid_results.csv")
            store.export_json(SCRIPT_NAME, "grid_results.json")
    finally:
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
