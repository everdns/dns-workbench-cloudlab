#!/usr/bin/env python3
"""Max sustainable QPS evaluation.

Determines the highest QPS at which a DNS server still satisfies a configurable
answer-rate threshold, using a two-phase search:

  Phase 1: exponentially raise the QPS until a level fails (upper bound).
  Phase 2: binary search between the last passing and first failing level.

All QPS values are integer multiples of ``min_qps_step``; the search operates on
QPS indices (qps = qps_idx * min_qps_step) so the resolution is exact.

A single DNS service is evaluated with a single load-generation tool per
invocation. Outputs trial_results.csv, level_tests.csv and search_summary.csv.
"""
import argparse
import logging
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from benchmark.config import (
    add_common_args,
    add_max_sustainable_qps_args,
    apply_cli_overrides,
    apply_max_sustainable_qps_overrides,
    load_config,
)
from benchmark.collectl import (
    collect_collectl_file,
    parse_collectl_file,
    run_collectl_session,
    wait_collectl,
)
from benchmark.dns_servers import (
    clear_dns_cache,
    ensure_dns_running,
    start_dns_service,
    stop_dns_service,
    wait_for_dns_ready,
    warmup_dns_cache,
)
from benchmark.hosts import get_clients, host_token, split_qps
from benchmark.remote import extract_run_result, ssh_run_many
from benchmark.results import ResultStore, ToolResult, aggregate_tool_results
from benchmark.tools import get_tools

log = logging.getLogger(__name__)

SCRIPT_NAME = "max_sustainable_qps"

def validate_search_config(config):
    """Validate the search parameters and resolve them into a params dict.

    Raises ValueError with an actionable message on any violation. Validation is
    done up front because a bad parameter here costs an entire run of wall clock
    time, and callers driving this script need a clean, early failure.
    """
    section = config.get("max_sustainable_qps")
    if not section:
        raise ValueError(
            "Config is missing the 'max_sustainable_qps' section (see "
            "configs/config_resolver.yaml for an example)"
        )

    def required_int(key):
        value = section.get(key)
        if value is None:
            raise ValueError(f"max_sustainable_qps.{key} is required")
        return int(value)

    initial_qps = required_int("initial_qps")
    min_qps_step = required_int("min_qps_step")
    max_qps = required_int("max_qps")
    num_trials = required_int("num_trials")
    min_passes = required_int("min_passes")

    trial_duration = int(section.get("trial_duration"))
    answer_rate_threshold = float(
        section.get("answer_rate_threshold")
    )
    min_qps_fidelity_pct = float(
        section.get("min_qps_fidelity_pct")
    )
    collectl_margin = int(section.get("collectl_margin"))

    if initial_qps <= 0:
        raise ValueError(
            f"max_sustainable_qps.initial_qps must be > 0, got {initial_qps}"
        )
    if min_qps_step <= 0:
        raise ValueError(
            f"max_sustainable_qps.min_qps_step must be > 0, got {min_qps_step}"
        )
    if initial_qps % min_qps_step != 0:
        raise ValueError(
            f"max_sustainable_qps.initial_qps ({initial_qps}) must be exactly "
            f"divisible by min_qps_step ({min_qps_step})"
        )
    if num_trials <= 0:
        raise ValueError(
            f"max_sustainable_qps.num_trials must be > 0, got {num_trials}"
        )
    if min_passes <= 0:
        raise ValueError(
            f"max_sustainable_qps.min_passes must be > 0, got {min_passes}"
        )
    if min_passes > num_trials:
        raise ValueError(
            f"max_sustainable_qps.min_passes ({min_passes}) must be <= "
            f"num_trials ({num_trials})"
        )
    if not 0 < answer_rate_threshold <= 100:
        raise ValueError(
            "max_sustainable_qps.answer_rate_threshold is a percentage and must "
            f"be in (0, 100], got {answer_rate_threshold}"
        )
    if trial_duration <= 0:
        raise ValueError(
            f"max_sustainable_qps.trial_duration must be > 0, got {trial_duration}"
        )
    if collectl_margin < 0:
        raise ValueError(
            f"max_sustainable_qps.collectl_margin must be >= 0, got {collectl_margin}"
        )
    if max_qps < min_qps_step:
        raise ValueError(
            f"max_sustainable_qps.max_qps ({max_qps}) must be >= "
            f"min_qps_step ({min_qps_step})"
        )
    if max_qps % min_qps_step != 0:
        adjusted = (max_qps // min_qps_step) * min_qps_step
        log.warning(
            "max_qps (%d) is not a multiple of min_qps_step (%d); flooring to %d",
            max_qps, min_qps_step, adjusted,
        )
        max_qps = adjusted

    tools = get_tools(config.get("tools"))
    if len(tools) != 1:
        raise ValueError(
            "This script evaluates exactly one load-generation tool; got "
            f"{[t.name for t in tools]}. Set a single tool via --tool or the "
            "'tools' config key."
        )
    tool = tools[0]

    services = (config.get("dns_services") or {}).get("services") or []
    if isinstance(services, str):
        services = [services]
    if len(services) != 1:
        raise ValueError(
            "This script evaluates exactly one DNS service; got "
            f"{list(services)}. Set a single service via --dns-service or the "
            "'dns_services.services' config key."
        )

    # The tool adapters and collectl both size their run from config["runtime"];
    # trial_duration is the user-facing name for the same quantity.
    config["runtime"] = trial_duration

    return {
        "initial_qps": initial_qps,
        "min_qps_step": min_qps_step,
        "max_qps": max_qps,
        "num_trials": num_trials,
        "min_passes": min_passes,
        "max_fails": num_trials - min_passes,
        "trial_duration": trial_duration,
        "answer_rate_threshold": answer_rate_threshold,
        "min_qps_fidelity_pct": min_qps_fidelity_pct,
        "clear_cache": bool(section.get("clear_cache", False)),
        "warmup_cache": bool(section.get("warmup_cache", False)),
        "collectl": bool(section.get("collectl", False)),
        "collectl_margin": collectl_margin,
        "tool": tool,
        "dns_service": services[0],
        "simulate_max_qps": None,
    }


def _new_trial_row(params, qps, trial, status):
    """Build a trial row with the specified column order and neutral values."""
    return {
        "dns_service": params["dns_service"],
        "tool": params["tool"].name,
        "target_qps": qps,
        "trial": trial + 1,
        "achieved_qps": 0.0,
        "queries_sent": 0,
        "queries_completed": 0,
        "queries_lost": 0,
        "answer_rate_pct": 0.0,
        "passed": False,
        "qps_fidelity_pct": 0.0,
        "status": status,
    }


def _simulated_trial_row(params, qps, trial):
    """Synthesise a trial result for --simulate-max-qps (no remote execution)."""
    row = _new_trial_row(params, qps, trial, "simulated")
    passed = qps <= params["simulate_max_qps"]
    answer_rate = 100.0 if passed else 50.0
    row.update({
        "achieved_qps": float(qps if passed else qps // 2),
        "queries_sent": qps * params["trial_duration"],
        "queries_completed": int(qps * params["trial_duration"] * answer_rate / 100.0),
        "queries_lost": qps * params["trial_duration"]
                        - int(qps * params["trial_duration"] * answer_rate / 100.0),
        "answer_rate_pct": answer_rate,
        "passed": passed,
        "qps_fidelity_pct": 100.0,
    })
    return row


def run_trial(config, params, qps, trial, trial_store):
    """Run one trial at ``qps`` and return its result row.

    Never raises: an infrastructure failure is recorded as a trial with a 0.0
    answer rate, which counts as a failure. A server that cannot be reached is
    not a server that is sustaining the load, and this guarantees the search
    terminates instead of hanging on a broken host.
    """
    if params["simulate_max_qps"] is not None:
        return _simulated_trial_row(params, qps, trial)

    tool = params["tool"]
    dns_service = params["dns_service"]
    trial_duration = params["trial_duration"]
    collectl_margin = params["collectl_margin"]
    dry_run = config.get("dry_run", False)

    row = _new_trial_row(params, qps, trial, "error")

    clients = get_clients(config)
    shares = split_qps(qps, len(clients))

    try:
        host_cmds = {}
        for host, share in zip(clients, shares):
            tool.validate_params(config, share)
            host_cmds[host] = tool.build_command(config, share)
    except Exception as e:
        log.error("Failed to build %s command at %d QPS trial %d: %s",
                  tool.name, qps, trial + 1, e)
        return row

    log.info("Trial %d at %d QPS: %s vs %s across %d host(s): %s",
             trial + 1, qps, tool.name, dns_service, len(clients),
             dict(zip(clients, shares)))

    if dry_run:
        for host, cmd in host_cmds.items():
            log.info("[DRY RUN] Would run on %s: %s", host, cmd)
        if params["collectl"]:
            log.info(
                "[DRY RUN] Would run collectl on server for %d seconds "
                "(trial_duration=%d, margin=%d)",
                trial_duration + 2 * collectl_margin, trial_duration, collectl_margin,
            )
        row["status"] = "dry_run"
        return row

    # 1/2. Put the cache into a deterministic state before every trial.
    try:
        if params["clear_cache"]:
            clear_dns_cache(config, dns_service)
            # Several clear-cache scripts restart the daemon; re-probe it.
            ensure_dns_running(config, dns_service, timeout=30)
        if params["warmup_cache"]:
            warmup_dns_cache(config)
    except Exception as e:
        log.error("Cache preparation failed at %d QPS trial %d: %s",
                  qps, trial + 1, e)
        row["status"] = "cache_error"
        return row

    # 3. Start collectl and let it warm up for one margin before load starts.
    collectl_session = None
    collectl_local_path = None
    if params["collectl"]:
        try:
            remote_trail = (
                f"/tmp/collectl_{dns_service}_{tool.name}_{qps}_{trial}.txt"
            )
            collectl_session = run_collectl_session(
                config, trial_duration, remote_trail, margin=collectl_margin,
            )
            collectl_local_path = os.path.join(
                trial_store._ensure_dir(SCRIPT_NAME, "collectl"),
                f"{dns_service}_{tool.name}_{qps}qps_trial{trial}.collectl.txt",
            )
        except Exception as e:
            log.warning("Failed to start collectl: %s. Continuing without it.", e)
            collectl_session = None

    try:
        # 4. Run the tool on every client host concurrently.
        tool_timeout = trial_duration + 2 * collectl_margin + 120
        run_results = ssh_run_many(host_cmds, timeout=tool_timeout)

        per_host = []
        for host, share in zip(clients, shares):
            stdout, stderr, rc, host_timed_out = extract_run_result(run_results[host])
            if host_timed_out:
                log.warning("%s timed out on %s at %d QPS", tool.name, host, share)
            elif rc not in (0, None):
                log.warning("%s returned exit code %d on %s", tool.name, rc, host)

            trial_store.save_raw_output(
                SCRIPT_NAME,
                f"{dns_service}_{tool.name}_{qps}qps_trial{trial}_{host_token(host)}.txt",
                f"=== STDOUT ===\n{stdout}\n=== STDERR ===\n{stderr}",
            )
            try:
                tr = tool.parse_output(stdout)
            except Exception:
                tr = ToolResult()
            per_host.append(tr)

        agg = aggregate_tool_results(per_host)

        # 5/6. Parse the metrics and derive the answer rate.
        answer_rate = 0.0
        if agg.queries_sent > 0:
            answer_rate = round(
                agg.queries_completed / agg.queries_sent * 100.0, 4
            )

        row.update({
            "achieved_qps": agg.achieved_qps,
            "queries_sent": agg.queries_sent,
            "queries_completed": agg.queries_completed,
            "queries_lost": agg.queries_lost,
            "answer_rate_pct": answer_rate,
            "passed": answer_rate >= params["answer_rate_threshold"],
            "status": "ok",
        })

        # The tool can fall short of the requested rate before the server does.
        # Advisory only: it does not affect pass/fail, but without it a
        # generator-side bottleneck is indistinguishable from server headroom.
        expected_queries = qps * trial_duration
        if expected_queries > 0:
            fidelity = round(agg.queries_sent / expected_queries * 100.0, 4)
            row["qps_fidelity_pct"] = fidelity
            if fidelity < params["min_qps_fidelity_pct"]:
                log.warning(
                    "%s sent only %.2f%% of the %d queries requested at %d QPS "
                    "(trial %d). The load generator may be the bottleneck, so "
                    "this level's answer rate may overstate server capacity.",
                    tool.name, fidelity, expected_queries, qps, trial + 1,
                )

        if tool.reports_latency:
            row["avg_latency_s"] = agg.avg_latency
            row["min_latency_s"] = agg.min_latency
            row["max_latency_s"] = agg.max_latency
            row["latency_stddev_s"] = agg.latency_stddev

    except subprocess.TimeoutExpired:
        log.error("%s timed out at %d QPS trial %d", tool.name, qps, trial + 1)
        row["status"] = "timeout"
    except Exception as e:
        log.error("Error running %s at %d QPS trial %d: %s",
                  tool.name, qps, trial + 1, e)
        row["status"] = "error"

    # 7. Collect collectl regardless of how the run went; it must never fail a trial.
    if collectl_session is not None:
        try:
            wait_collectl(collectl_session["proc"], timeout=collectl_margin + 30)
            collect_collectl_file(
                config, collectl_session["output_file"], collectl_local_path,
            )
            metrics = parse_collectl_file(collectl_local_path, collectl_margin)
            row.update({k: v for k, v in metrics.items() if v is not None})
        except Exception as e:
            log.warning(
                "collectl collection/parse failed at %d QPS trial %d: %s",
                qps, trial + 1, e,
            )

    return row


def level_test(config, params, qps, trial_store):
    """Determine whether ``qps`` passes the evaluation criteria.

    Runs up to num_trials trials and stops as soon as the outcome is
    mathematically determined: the level fails once num_fails exceeds max_fails,
    and passes once num_passes reaches min_passes.

    Returns (passed, level_row).
    """
    num_trials = params["num_trials"]
    min_passes = params["min_passes"]
    max_fails = params["max_fails"]
    threshold = params["answer_rate_threshold"]

    log.info("=== Level test at %d QPS (need %d/%d trials at >= %.4f%% answer "
             "rate; fails after %d) ===",
             qps, min_passes, num_trials, threshold, max_fails + 1)

    num_passes = 0
    num_fails = 0
    achieved_qps_values = []
    answer_rates = []
    passed = None

    for trial in range(num_trials):
        row = run_trial(config, params, qps, trial, trial_store)
        trial_store.add_result(row)
        achieved_qps_values.append(row["achieved_qps"])
        answer_rates.append(row["answer_rate_pct"])

        if row["passed"]:
            num_passes += 1
        else:
            num_fails += 1

        log.info("Trial %d/%d at %d QPS: answer rate %.4f%% -> %s "
                 "(passes=%d fails=%d, status=%s)",
                 trial + 1, num_trials, qps, row["answer_rate_pct"],
                 "PASS" if row["passed"] else "FAIL",
                 num_passes, num_fails, row["status"])

        if num_fails > max_fails:
            passed = False
            break
        if num_passes >= min_passes:
            passed = True
            break

        if not config.get("dry_run") and params["simulate_max_qps"] is None:
            pause = config.get("pause_between_runs", 0)
            if pause:
                log.info("Pausing %ds...", pause)
                time.sleep(pause)

    if passed is None:
        # Unreachable while min_passes <= num_trials; kept as a safety net.
        passed = num_passes >= min_passes

    trials_run = num_passes + num_fails
    level_row = {
        "target_qps": qps,
        "num_trials": trials_run,
        "num_passes": num_passes,
        "num_fails": num_fails,
        "average_achieved_qps": (
            round(sum(achieved_qps_values) / len(achieved_qps_values), 2)
            if achieved_qps_values else 0.0
        ),
        "average_answer_rate_pct": (
            round(sum(answer_rates) / len(answer_rates), 4)
            if answer_rates else 0.0
        ),
        "passed": passed,
        "max_trials": num_trials,
    }

    log.info("=== Level %d QPS: %s after %d trial(s) (%d pass / %d fail, "
             "avg answer rate %.4f%%) ===",
             qps, "PASS" if passed else "FAIL", trials_run, num_passes,
             num_fails, level_row["average_answer_rate_pct"])

    return passed, level_row


def run_search(config, params, trial_store, level_store):
    """Run the two-phase QPS search and return the summary row."""
    min_qps_step = params["min_qps_step"]
    initial_qps_idx = params["initial_qps"] // min_qps_step
    max_qps_idx = params["max_qps"] // min_qps_step

    low_qps_idx = 1
    high_qps_idx = min(initial_qps_idx, max_qps_idx)
    max_passing_qps = 0
    max_qps_tested = 0
    tested_levels = {}  # qps_idx -> passed
    started = time.time()

    def test(qps_idx):
        """Test one level, reusing a cached verdict rather than re-running it."""
        if qps_idx in tested_levels:
            log.info("Level %d QPS already tested -> %s; not repeating",
                     qps_idx * min_qps_step,
                     "PASS" if tested_levels[qps_idx] else "FAIL")
            return tested_levels[qps_idx]

        passed, level_row = level_test(
            config, params, qps_idx * min_qps_step, trial_store,
        )
        tested_levels[qps_idx] = passed
        level_store.add_result(level_row)
        # Export after every level so an interrupted run still leaves complete
        # data on disk.
        trial_store.export_csv(SCRIPT_NAME, "trial_results.csv")
        level_store.export_csv(SCRIPT_NAME, "level_tests.csv")
        return passed

    # --- Phase 1: exponentially raise the QPS until a level fails. ---
    log.info("--- Phase 1: searching for an upper bound from %d QPS ---",
             high_qps_idx * min_qps_step)
    hit_ceiling = False
    while True:
        at_ceiling = high_qps_idx >= max_qps_idx
        if at_ceiling and high_qps_idx != max_qps_idx:
            log.info("Clamping level to the max_qps ceiling of %d QPS",
                     max_qps_idx * min_qps_step)
            high_qps_idx = max_qps_idx

        if not test(high_qps_idx):
            break

        # Everything at or below this level is known to pass, so the binary
        # search never needs to revisit it.
        low_qps_idx = high_qps_idx + 1
        max_passing_qps = high_qps_idx * min_qps_step

        if at_ceiling:
            hit_ceiling = True
            log.warning(
                "The max_qps ceiling of %d QPS PASSED. Finishing the search "
                "early: the server's real limit was never bracketed, so "
                "max_qps_passed=%d is a LOWER BOUND. Raise max_qps to find the "
                "true maximum.",
                max_passing_qps, max_passing_qps,
            )
            break

        high_qps_idx *= 2

    #Exponential testing finds the maximum qps tested    
    max_qps_tested = high_qps_idx * min_qps_step

    # --- Phase 2: binary search between the last pass and the first fail. ---
    if not hit_ceiling:
        log.info("--- Phase 2: binary search between %d and %d QPS ---",
                 low_qps_idx * min_qps_step, high_qps_idx * min_qps_step)
        while low_qps_idx < high_qps_idx:
            mid_qps_idx = low_qps_idx + ((high_qps_idx - low_qps_idx) // 2)
            if test(mid_qps_idx):
                low_qps_idx = mid_qps_idx + 1
                max_passing_qps = max(max_passing_qps, mid_qps_idx * min_qps_step)
            else:
                high_qps_idx = mid_qps_idx

    max_qps_tested = (
        max(tested_levels) * min_qps_step if tested_levels else 0
    )

    return {
        "max_qps_passed": max_passing_qps,
        "max_qps_tested": max_qps_tested,
        "num_qps_values_tested": len(tested_levels),
        "dns_service": params["dns_service"],
        "tool": params["tool"].name,
        "initial_qps": params["initial_qps"],
        "min_qps_step": min_qps_step,
        "max_qps_ceiling": params["max_qps"],
        "num_trials": params["num_trials"],
        "min_passes": params["min_passes"],
        "answer_rate_threshold": params["answer_rate_threshold"],
        "trial_duration": params["trial_duration"],
        "hit_max_qps_ceiling": hit_ceiling,
        "total_trials_run": len(trial_store.results),
        "search_duration_s": round(time.time() - started, 1),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Find the maximum sustainable QPS of a DNS server"
    )
    add_common_args(parser)
    add_max_sustainable_qps_args(parser)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = load_config(args.config)
    config = apply_cli_overrides(config, args)
    config = apply_max_sustainable_qps_overrides(config, args)
    config["dry_run"] = args.dry_run

    try:
        params = validate_search_config(config)
    except ValueError as e:
        log.error("Invalid configuration: %s", e)
        return 2

    params["simulate_max_qps"] = args.simulate_max_qps
    simulating = params["simulate_max_qps"] is not None

    output_dir = args.output_dir
    trial_store = ResultStore(output_dir)
    level_store = ResultStore(output_dir)
    summary_store = ResultStore(output_dir)

    log.info("=== Max Sustainable QPS Evaluation ===")
    log.info("DNS service: %s", params["dns_service"])
    log.info("Tool: %s", params["tool"].name)
    log.info("Initial QPS: %d, min QPS step: %d, max QPS: %d",
             params["initial_qps"], params["min_qps_step"], params["max_qps"])
    log.info("Level criteria: %d/%d trials at >= %.4f%% answer rate "
             "(max %d failures), %ds per trial",
             params["min_passes"], params["num_trials"],
             params["answer_rate_threshold"], params["max_fails"],
             params["trial_duration"])
    if params["clear_cache"]:
        log.info("Cache clearing enabled: will clear before each trial")
    if params["warmup_cache"]:
        log.info("Cache warmup enabled: will pre-populate the cache before each trial")
    if params["collectl"]:
        log.info("collectl enabled: will sample the DNS server with margin=%ds per trial",
                 params["collectl_margin"])
    if simulating:
        log.warning("SIMULATION MODE: no remote commands will run; levels pass "
                    "iff QPS <= %d", params["simulate_max_qps"])

    manage_service = not simulating and not config.get("dry_run")

    if manage_service:
        try:
            stop_dns_service(config)
            time.sleep(2)
            start_dns_service(config, params["dns_service"])
            wait_for_dns_ready(config, timeout=300)
        except Exception as e:
            log.error("Failed to start %s: %s. Aborting.", params["dns_service"], e)
            return 1

    try:
        summary = run_search(config, params, trial_store, level_store)
    finally:
        if manage_service:
            try:
                stop_dns_service(config, params["dns_service"])
            except Exception as e:
                log.warning("Failed to stop %s: %s", params["dns_service"], e)

    summary_store.add_result(summary)

    trial_path = trial_store.export_csv(SCRIPT_NAME, "trial_results.csv")
    level_path = level_store.export_csv(SCRIPT_NAME, "level_tests.csv")
    summary_path = summary_store.export_csv(SCRIPT_NAME, "search_summary.csv")
    summary_store.export_json(SCRIPT_NAME, "search_summary.json")
    log.info("Results exported to %s, %s and %s",
             trial_path, level_path, summary_path)

    log.info("=== Max sustainable QPS: %d (highest QPS tested: %d, "
             "%d QPS values tested, %d trials, %.1fs) ===",
             summary["max_qps_passed"], summary["max_qps_tested"],
             summary["num_qps_values_tested"], summary["total_trials_run"],
             summary["search_duration_s"])
    if summary["hit_max_qps_ceiling"]:
        log.warning("Result is a LOWER BOUND: the max_qps ceiling passed.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
