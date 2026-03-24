#!/usr/bin/env python3
"""Script 2: QPS Accuracy Evaluation.

Measures how accurately each tool achieves a specified QPS using
round-robin scheduling and dns_responder timestamp analysis.
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
    add_script2_args,
    apply_cli_overrides,
    apply_script2_overrides,
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
    compute_accuracy_metrics,
    compute_actual_runtime,
    parse_dns_responder_output,
    read_timestamps_file,
)
from benchmark.tools import get_tools

log = logging.getLogger(__name__)


def run_accuracy_test(config, tool, qps, trial, store, script_name):
    """Run a single accuracy test: tool at target QPS with timestamp capture.

    Returns list of result rows (one per interval) or empty list on failure.
    """
    client = config["hosts"]["client"]
    dry_run = config.get("dry_run", False)

    tool.validate_params(config, qps)
    cmd = tool.build_command(config, qps)

    log.info("Accuracy test: %s at %d QPS, trial %d", tool.name, qps, trial + 1)

    if dry_run:
        log.info("[DRY RUN] Would run: %s", cmd)
        return []

    # Start dns_responder with timestamps
    session = run_dns_responder_session(config, timestamps=True)

    try:
        tool_timeout = config["runtime"] + 60
        result = ssh_run(client, cmd, timeout=tool_timeout)

        tool_stdout = result.stdout
        tool_stderr = result.stderr

        if result.returncode != 0:
            log.warning("%s returned exit code %d", tool.name, result.returncode)

        store.save_raw_output(
            script_name,
            f"{tool.name}_{qps}qps_trial{trial}_tool.txt",
            f"=== STDOUT ===\n{tool_stdout}\n=== STDERR ===\n{tool_stderr}",
        )

        # Wait for dns_responder
        wait_dns_responder(
            session["proc"], timeout=session["duration"] + 30
        )

        # Collect dns_responder output + timestamps
        local_raw_dir = os.path.join(store.output_dir, script_name, "raw")
        os.makedirs(local_raw_dir, exist_ok=True)

        output_path, ts_path = collect_dns_responder_output(
            config, session["output_file"], local_raw_dir,
            session["timestamps_file"],
        )
        with open(output_path) as f:
            resp_text = f.read()

        if not ts_path:
            raise RuntimeError("Failed to retrieve timestamps file from server")

        store.save_raw_output(
            script_name, f"{tool.name}_{qps}qps_trial{trial}_responder.txt", resp_text,
        )

        # Parse dns_responder output
        resp_result = parse_dns_responder_output(resp_text)

        # Save timestamps and compute accuracy
        ts_dest = os.path.join(
            store.output_dir, script_name, "timestamps",
            f"{tool.name}_{qps}qps_trial{trial}_timestamps.txt",
        )
        os.makedirs(os.path.dirname(ts_dest), exist_ok=True)
        os.rename(ts_path, ts_dest)

        timestamps = read_timestamps_file(ts_dest)
        actual_runtime_ns = compute_actual_runtime(timestamps)
        log.info("Actual runtime from timestamps: %.3fs", actual_runtime_ns / 1e9)
        accuracy = compute_accuracy_metrics(timestamps, qps, config["runtime"])

        rows = []
        for label, metrics in accuracy.items():
            row = {
                "tool": tool.name,
                "target_qps": qps,
                "trial": trial + 1,
                "interval": label,
                "actual_runtime_ns": actual_runtime_ns,
                "mean_qps": round(metrics.mean_qps, 2),
                "stddev": round(metrics.stddev, 2),
                "max_deviation": round(metrics.max_deviation, 2),
                "responder_avg_rx_pps": resp_result.avg_rx_pps,
                "responder_rx_total": resp_result.rx_total,
                "responder_drops": resp_result.drops,
            }
            store.add_result(row)
            rows.append(row)

        # Parse tool output for logging
        tool_result = tool.parse_output(tool_stdout)
        log.info("  Tool reported QPS: %.1f, Responder avg: %.1f pps",
                 tool_result.achieved_qps, resp_result.avg_rx_pps)

        return rows

    except subprocess.TimeoutExpired:
        log.error("%s timed out at %d QPS trial %d", tool.name, qps, trial + 1)
        return []
    except Exception as e:
        log.error("Error running %s at %d QPS trial %d: %s",
                  tool.name, qps, trial + 1, e)
        return []


def main():
    parser = argparse.ArgumentParser(description="Script 2: QPS Accuracy Evaluation")
    add_common_args(parser)
    add_script2_args(parser)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = load_config(args.config)
    config = apply_cli_overrides(config, args)
    config = apply_script2_overrides(config, args)
    config["dry_run"] = args.dry_run

    s2 = config["script2"]
    min_qps = s2["accuracy_min_qps"]
    max_qps = s2["accuracy_max_qps"]
    step = s2["accuracy_step"]
    trials = s2["trials"]

    tools = get_tools(config.get("tools"))
    output_dir = args.output_dir
    script_name = "qps_accuracy"
    store = ResultStore(output_dir)

    log.info("=== QPS Accuracy Evaluation ===")
    log.info("Tools: %s", [t.name for t in tools])
    log.info("QPS range: %d -> %d (step %d), %d trials",
             min_qps, max_qps, step, trials)

    # Round-robin: for each QPS, rotate through all tools per trial
    qps = min_qps
    while qps <= max_qps:
        log.info("--- QPS target: %d ---", qps)
        for trial in range(trials):
            for tool in tools:
                try:
                    run_accuracy_test(config, tool, qps, trial, store, script_name)
                except Exception as e:
                    log.error("Unhandled error: %s at %d QPS trial %d: %s",
                              tool.name, qps, trial + 1, e)

                if not config.get("dry_run"):
                    log.info("Pausing %ds...", config["pause_between_runs"])
                    time.sleep(config["pause_between_runs"])

        qps += step

    # Export results
    csv_path = store.export_csv(script_name)
    json_path = store.export_json(script_name)
    log.info("Results exported to %s and %s", csv_path, json_path)

    # Generate charts
    try:
        from benchmark.charts import plot_qps_accuracy
        charts_dir = os.path.join(output_dir, script_name, "charts")
        plot_qps_accuracy(store.results, charts_dir)
        log.info("Charts saved to %s", charts_dir)
    except ImportError:
        log.warning("matplotlib not available, skipping chart generation")
    except Exception as e:
        log.error("Chart generation failed: %s", e)

    log.info("=== QPS Accuracy Evaluation Complete ===")


if __name__ == "__main__":
    main()
