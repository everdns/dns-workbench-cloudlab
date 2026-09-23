#!/usr/bin/env python3
"""Grid search over BIND options and host tuning, scored by max sustainable QPS.

For each point proposed:

  1. Propose a configuration (the next combination of parameter values).
  2. Attempt the configuration update on the name server.
  3a. On success, run the max-sustainable-QPS search and record the result.
  3b. On failure, record why and return to step 1.

--mode grid (default) proposes every combination. --mode coordinate-descent
proposes points by coordinate descent (maximizing QPS): starting from each
parameter's first value, it sweeps one parameter at a time with the others held
at the current best, over up to --max-passes passes.

grid_search.yaml is the only configuration file this needs: it holds both the
grid parameters and the base search config (hosts, tool, dns service, etc.)
passed to optimization.max_sustainable_qps.

    python3 grid_search.py --output-dir results
    python3 grid_search.py --mode coordinate-descent --max-passes 3
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

from bind_config import (
    BindConfigError,
    install_options,
    load_base_options,
    render_options,
    restore_base,
)
from max_sustainable_qps import ResultStore, run_max_sustainable_qps
import system_config
from system_config import SystemConfigError

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


def split_overrides(overrides):
    """Split a point into (named.conf.options overrides, host-level overrides)."""
    bind, system = {}, {}
    for name, value in overrides.items():
        (system if system_config.is_system_parameter(name) else bind)[name] = value
    return bind, system


def restore_or_exit(server, base_text, system_baseline=None):
    """Put the base config (and host baseline, if any) back, or stop the search.

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
    if system_baseline is None:
        return
    try:
        system_config.restore(server, system_baseline)
    except SystemConfigError as e:
        log.error("Failed to restore the system baseline on %s: %s", server, e)
        log.error("Host tuning (sysctls, ufw, iptables raw rules, named "
                  "LimitNOFILE drop-in) is left modified. Reboot the name "
                  "server before running again.")
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


def previous_rows(output_dir, filename):
    """Rows recorded by a previous run, for --resume."""
    path = os.path.join(output_dir, SCRIPT_NAME, filename)
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [row for row in csv.DictReader(f) if row.get("point_id")]


def row_qps(row):
    """max_qps_passed for a successful row, else None."""
    if row.get("status") != "ok":
        return None
    try:
        return int(float(row["max_qps_passed"]))
    except (KeyError, TypeError, ValueError):
        return None


class Evaluator:
    """Installs a point on the name server, measures it, restores the base, and
    records the row. Each point id is measured at most once per run, and rows
    from a resumed run count as already measured."""

    def __init__(self, config, server, base_text, system_baseline,
                 output_dir, results_name, timeout, resumed_rows=()):
        self.config = config
        self.server = server
        self.base_text = base_text
        self.system_baseline = system_baseline
        self.output_dir = output_dir
        self.results_name = results_name
        self.timeout = timeout
        self.store = ResultStore(output_dir)
        self.rows = {}
        for row in resumed_rows:
            self.store.add_result(row)
            self.rows[row["point_id"]] = row

    def export(self):
        path = self.store.export_csv(SCRIPT_NAME, f"{self.results_name}.csv")
        self.store.export_json(SCRIPT_NAME, f"{self.results_name}.json")
        return path

    def evaluate(self, overrides, label, extra=None):
        """Return the row for ``overrides``, measuring it only if needed."""
        pid = point_id(overrides)
        if pid in self.rows:
            log.info("%s Reusing %s (already recorded)", label, pid)
            return self.rows[pid]

        log.info("%s Proposing %s", label, pid)
        row = {
            "point_id": pid,
            "index": len(self.store.results) + 1,
            **(extra or {}),
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
        self.rows[pid] = row

        try:
            bind_overrides, system_overrides = split_overrides(overrides)
            rendered = render_options(self.base_text, bind_overrides)
            install_options(self.server, rendered)
            system_config.apply(self.server, system_overrides)
        except (BindConfigError, SystemConfigError) as e:
            log.error("Configuration update failed: %s", e)
            row["status"] = "config_failed"
            row["error"] = str(e)
            self.store.add_result(row)
            self.export()
            restore_or_exit(self.server, self.base_text, self.system_baseline)
            return row

        run_dir = os.path.join(self.output_dir, SCRIPT_NAME, "runs", pid)
        row["run_dir"] = run_dir
        try:
            status, summary, trial_rows = run_evaluation(
                self.config, run_dir, timeout=self.timeout,
            )
        finally:
            restore_or_exit(self.server, self.base_text, self.system_baseline)

        row["status"] = status
        if status == "ok":
            row["max_qps_passed"] = summary["max_qps_passed"]
            row["max_qps_tested"] = summary["max_qps_tested"]
            row["hit_max_qps_ceiling"] = summary["hit_max_qps_ceiling"]
            row["total_trials_run"] = summary["total_trials_run"]
            row["search_duration_s"] = summary["search_duration_s"]
            fidelity = min_fidelity(trial_rows)
            row["min_qps_fidelity_pct"] = "" if fidelity is None else fidelity
            log.info("%s %s -> %d QPS", label, pid, summary["max_qps_passed"])

        self.store.add_result(row)
        self.export()
        return row


def run_grid(evaluator, parameters):
    """Measure every combination. Returns (point_id, qps) of the best, or None."""
    points = list(propose_configurations(parameters))
    best = None
    for index, overrides in enumerate(points, start=1):
        row = evaluator.evaluate(overrides, f"[{index}/{len(points)}]",
                                 {"index": index})
        qps = row_qps(row)
        if qps is not None and (best is None or qps > best[1]):
            best = (row["point_id"], qps)
    return best


def run_coordinate_descent(evaluator, parameters, max_passes, min_improvement_pct):
    """Coordinate descent (maximizing QPS): starting from each parameter's first value, sweep one
    parameter at a time with the rest held at the current best, and move to a
    new value only if it beats the current point by more than
    ``min_improvement_pct``. Repeat passes until one changes nothing or
    ``max_passes`` is reached. Returns (point_id, qps) of the final point, or
    None if no point produced a usable result."""
    current = {p["name"]: p["values"][0] for p in parameters}
    current_row = evaluator.evaluate(current, "[start]",
                                     {"pass": 0, "coordinate": ""})
    current_qps = row_qps(current_row)

    for pass_no in range(1, max_passes + 1):
        log.info("=== Coordinate pass %d/%d from %s (%s QPS) ===", pass_no,
                 max_passes, point_id(current), current_qps)
        changed = False
        for p in parameters:
            name = p["name"]
            best_value, best_qps = current[name], current_qps
            for value in p["values"]:
                if value == current[name]:
                    continue
                candidate = {**current, name: value}
                row = evaluator.evaluate(
                    candidate, f"[pass {pass_no} {name}]",
                    {"pass": pass_no, "coordinate": name},
                )
                qps = row_qps(row)
                if qps is None:
                    continue
                if current_qps is None:
                    beats = best_qps is None or qps > best_qps
                else:
                    threshold = current_qps * (1 + min_improvement_pct / 100)
                    beats = qps > threshold and qps > best_qps
                if beats:
                    best_value, best_qps = value, qps

            if best_value != current[name]:
                log.info("%s: %s -> %s (%s -> %s QPS)", name, current[name],
                         best_value, current_qps, best_qps)
                current[name] = best_value
                current_qps = best_qps
                changed = True
            else:
                log.info("%s: keeping %s", name, current[name])

        if not changed:
            log.info("Converged after pass %d: no parameter changed", pass_no)
            break
    else:
        log.warning("Stopped after --max-passes=%d without converging", max_passes)

    return None if current_qps is None else (point_id(current), current_qps)


def main():
    parser = argparse.ArgumentParser(
        description="Grid search or coordinate descent over BIND options and "
                    "host tuning, scored by max sustainable QPS"
    )
    parser.add_argument("--grid", default=DEFAULT_GRID,
                        help="Grid config YAML defining parameters and the search config")
    parser.add_argument("--mode", choices=["grid", "coordinate-descent"],
                        default="grid",
                        help="grid: measure every combination. "
                             "coordinate-descent: optimize one parameter at a "
                             "time, starting from each parameter's first value")
    parser.add_argument("--max-passes", type=int, default=3,
                        help="coordinate-descent mode: most passes over all "
                             "parameters")
    parser.add_argument("--min-improvement-pct", type=float, default=1.0,
                        help="coordinate-descent mode: a value must beat the "
                             "current point's QPS by more than this percent "
                             "to replace it")
    parser.add_argument("--dns-service",
                        help="DNS service to evaluate (overrides the grid config)")
    parser.add_argument("--server", help="Name server host (overrides the grid config)")
    parser.add_argument("--output-dir", default="results",
                        help="Output directory for results")
    parser.add_argument("--point-timeout", type=int, default=21600,
                        help="Seconds to allow one evaluation before moving on")
    parser.add_argument("--resume", action="store_true",
                        help="Keep and skip points already recorded by this mode")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print each proposed configuration and stop")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.max_passes < 1:
        parser.error("--max-passes must be >= 1")
    if args.min_improvement_pct < 0:
        parser.error("--min-improvement-pct must be >= 0")

    config = load_grid(args.grid)
    parameters = config["parameters"]
    if args.dns_service:
        config["dns_service"] = args.dns_service
    if args.server:
        config.setdefault("hosts", {})["server"] = args.server

    dns_service = config.get("dns_service", "ns_bind")
    search_output_dir = args.output_dir if os.path.isabs(args.output_dir) else os.path.join(OPTIMIZATION_DIR, args.output_dir)
    results_name = "grid_results" if args.mode == "grid" else "coordinate_descent_results"

    server = config.get("hosts", {}).get("server")
    if not server:
        log.error("No name server host configured")
        return 2

    base_text = load_base_options()
    uses_system = any(system_config.is_system_parameter(p["name"])
                      for p in parameters)

    if args.mode == "grid":
        points = list(propose_configurations(parameters))
        log.info("=== Grid search over %d parameter(s), %d point(s) ===",
                 len(parameters), len(points))
    else:
        points = [{p["name"]: p["values"][0] for p in parameters}]
        per_pass = sum(len(p["values"]) - 1 for p in parameters)
        log.info("=== Coordinate descent over %d parameter(s): at most %d "
                 "point(s) per pass, %d pass(es), min improvement %.2f%% ===",
                 len(parameters), per_pass, args.max_passes,
                 args.min_improvement_pct)
    for p in parameters:
        log.info("  %s: %s", p["name"], p["values"])
    log.info("Name server: %s, service: %s", server, dns_service)

    if args.dry_run:
        if args.mode == "coordinate-descent":
            log.info("Coordinate descent depends on measurements; showing only "
                     "the starting point")
        for overrides in points:
            print(f"--- {point_id(overrides)} ---")
            bind_overrides, system_overrides = split_overrides(overrides)
            install_options(server, render_options(base_text, bind_overrides),
                            dry_run=True)
            system_config.apply(server, system_overrides, dry_run=True)
        return 0

    resumed = previous_rows(search_output_dir, f"{results_name}.csv") if args.resume else []
    if resumed:
        log.info("Resuming: %d point(s) already recorded", len(resumed))

    system_baseline = None
    if uses_system:
        try:
            system_baseline = system_config.snapshot(server)
        except SystemConfigError as e:
            log.error("Could not snapshot host settings on %s: %s", server, e)
            return 2

    evaluator = Evaluator(config, server, base_text, system_baseline,
                          search_output_dir, results_name, args.point_timeout,
                          resumed)
    started = time.time()

    try:
        if args.mode == "grid":
            best = run_grid(evaluator, parameters)
        else:
            best = run_coordinate_descent(evaluator, parameters, args.max_passes,
                                  args.min_improvement_pct)
    finally:
        path = evaluator.export()
        if path:
            log.info("Results written to %s", path)
        restore_or_exit(server, base_text, system_baseline)

    log.info("=== %s finished in %.1fs ===",
             "Grid search" if args.mode == "grid" else "Coordinate descent",
             time.time() - started)
    if best:
        log.info("Best: %s at %d QPS", best[0], best[1])
    else:
        log.warning("No point produced a usable result")
    return 0


if __name__ == "__main__":
    sys.exit(main())
