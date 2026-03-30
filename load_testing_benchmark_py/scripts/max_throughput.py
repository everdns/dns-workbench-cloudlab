#!/usr/bin/env python3
"""Script 1: Maximum Throughput Discovery.

Determines the maximum sustainable QPS for each DNS load testing tool by
ramping up the target QPS and measuring achieved QPS via dns_responder.
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
    add_script1_args,
    apply_cli_overrides,
    apply_script1_overrides,
    load_config,
)
from benchmark.dns_responder import (
    collect_dns_responder_output,
    run_dns_responder_session,
    wait_dns_responder,
)
from benchmark.remote import ssh_run
from benchmark.results import (
    ResultStore,
    parse_dns_responder_output,
)
from benchmark.tools import get_tools

log = logging.getLogger(__name__)


def run_single_test(config, tool, qps, store, script_name, trial=1):
    """Run a single throughput test for one tool at one QPS level.

    Returns a result dict or None on failure.
    """
    client = config["hosts"]["client"]
    server = config["hosts"]["server"]
    dry_run = config.get("dry_run", False)

    tool.validate_params(config, qps)
    cmd = tool.build_command(config, qps)

    log.info("Testing %s at %d QPS", tool.name, qps)
    log.info("Command: %s", cmd)

    if dry_run:
        log.info("[DRY RUN] Would run on %s: %s", client, cmd)
        return None

    # Start dns_responder on server
    session = run_dns_responder_session(config, timestamps=True)

    tool_timed_out = False
    tool_stdout = ""
    tool_stderr = ""

    try:
        # Run the load tool on client
        tool_timeout = config["runtime"] + 120
        try:
            result = ssh_run(client, cmd, timeout=tool_timeout)
            tool_stdout = result.stdout
            tool_stderr = result.stderr

            if result.returncode != 0:
                log.warning("%s returned exit code %d at %d QPS",
                            tool.name, result.returncode, qps)
                log.warning("stderr: %s", tool_stderr[:500])
        except subprocess.TimeoutExpired as e:
            tool_timed_out = True
            tool_stdout = e.stdout or ""
            tool_stderr = e.stderr or ""
            log.warning("%s timed out at %d QPS (killed by ssh_run)", tool.name, qps)

        # Save raw tool output
        store.save_raw_output(
            script_name,
            f"{tool.name}_{qps}qps_trial{trial}_tool.txt",
            f"=== STDOUT ===\n{tool_stdout}\n=== STDERR ===\n{tool_stderr}"
            + ("\n=== TIMED OUT ===" if tool_timed_out else ""),
        )

        # Wait for dns_responder to finish
        wait_dns_responder(
            session["proc"], timeout=session["duration"] + 120
        )

        # Collect dns_responder output from server
        local_raw_dir = os.path.join(store.output_dir, script_name, "raw")
        os.makedirs(local_raw_dir, exist_ok=True)
        output_path, _ = collect_dns_responder_output(
            config, session["output_file"], local_raw_dir,
        )
        with open(output_path) as f:
            resp_text = f.read()

        store.save_raw_output(
            script_name, f"{tool.name}_{qps}qps_trial{trial}_responder.txt", resp_text,
        )

        # Parse outputs — RX QPS is computed by dns_responder via -T flag
        resp_result = parse_dns_responder_output(resp_text)
        actual_qps = resp_result.rx_qps
        log.info("Achieved QPS according to dns_responder: %.2f (traffic window: %.3fs)",
                 actual_qps, resp_result.actual_duration_secs)

        row = {
            "tool": tool.name,
            "requested_qps": qps,
            "trial": trial,
            "achieved_qps_responder": actual_qps,
            "actual_duration_secs": resp_result.actual_duration_secs,
            "rx_total": resp_result.rx_total,
            "tx_total": resp_result.tx_total,
            "drops": resp_result.drops,
            "timed_out": tool_timed_out,
        }

        # Parse tool output — best-effort if timed out (output may be incomplete)
        try:
            tool_result = tool.parse_output(tool_stdout)
            row["tool_reported_qps"] = tool_result.achieved_qps
            row["tool_queries_sent"] = tool_result.queries_sent
            row["tool_queries_completed"] = tool_result.queries_completed
            row["tool_queries_lost"] = tool_result.queries_lost
            row["avg_latency_s"] = tool_result.avg_latency
        except Exception:
            if not tool_timed_out:
                raise
            log.info("Could not parse %s output after timeout (expected)", tool.name)

        store.add_result(row)
        return row

    except Exception as e:
        log.error("Error running %s at %d QPS: %s", tool.name, qps, e)
        return None


def main():
    parser = argparse.ArgumentParser(description="Script 1: Maximum Throughput Discovery")
    add_common_args(parser)
    add_script1_args(parser)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = load_config(args.config)
    config = apply_cli_overrides(config, args)
    config = apply_script1_overrides(config, args)
    config["dry_run"] = args.dry_run

    s1 = config["script1"]
    start_qps = s1["start_qps"]
    qps_step = s1["qps_step"]
    max_qps = s1["max_qps"]
    num_trials = s1.get("trials", 1)

    tools = get_tools(config.get("tools"))
    output_dir = args.output_dir
    script_name = "max_throughput"
    store = ResultStore(output_dir)

    log.info("=== Maximum Throughput Discovery ===")
    log.info("Tools: %s", [t.name for t in tools])
    log.info("QPS range: %d -> %d (step %d)", start_qps, max_qps, qps_step)
    log.info("Trials per QPS: %d", num_trials)
    log.info("Runtime: %ds, Pause: %ds", config["runtime"], config["pause_between_runs"])

    for tool in tools:
        log.info("--- Starting throughput discovery for %s ---", tool.name)
        qps = start_qps
        while qps <= max_qps:
            for trial in range(1, num_trials + 1):
                if num_trials > 1:
                    log.info("Trial %d/%d for %s at %d QPS", trial, num_trials, tool.name, qps)
                try:
                    run_single_test(config, tool, qps, store, script_name, trial=trial)
                except Exception as e:
                    log.error("Unhandled error for %s at %d QPS trial %d: %s", tool.name, qps, trial, e)

                if trial < num_trials and not config.get("dry_run"):
                    log.info("Pausing %ds before next trial...", config["pause_between_runs"])
                    time.sleep(config["pause_between_runs"])

            qps += qps_step

            if qps <= max_qps and not config.get("dry_run"):
                log.info("Pausing %ds before next run...", config["pause_between_runs"])
                time.sleep(config["pause_between_runs"])

    # Export results
    csv_path = store.export_csv(script_name)
    json_path = store.export_json(script_name)
    log.info("Results exported to %s and %s", csv_path, json_path)

    # Generate charts
    try:
        from benchmark.charts import plot_max_throughput
        charts_dir = os.path.join(output_dir, script_name, "charts")
        plot_max_throughput(store.results, charts_dir)
        log.info("Charts saved to %s", charts_dir)
    except ImportError:
        log.warning("matplotlib not available, skipping chart generation")
    except Exception as e:
        log.error("Chart generation failed: %s", e)

    log.info("=== Maximum Throughput Discovery Complete ===")


if __name__ == "__main__":
    main()
