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
from benchmark.hosts import get_clients, host_token, split_qps
from benchmark.remote import extract_run_result, ssh_run_many
from benchmark.results import (
    ResultStore,
    ToolResult,
    aggregate_tool_results,
    parse_dns_responder_output,
)
from benchmark.tools import get_tools

log = logging.getLogger(__name__)


def run_single_test(config, tool, qps, store, script_name, trial=1):
    """Run a single throughput test for one tool at one QPS level.

    Returns a result dict or None on failure.
    """
    clients = get_clients(config)
    shares = split_qps(qps, len(clients))
    dry_run = config.get("dry_run", False)

    host_cmds = {}
    for host, share in zip(clients, shares):
        tool.validate_params(config, share)
        host_cmds[host] = tool.build_command(config, share)

    log.info("Testing %s at %d QPS across %d host(s): %s",
             tool.name, qps, len(clients), dict(zip(clients, shares)))
    for host, cmd in host_cmds.items():
        log.info("  %s (%d QPS): %s", host, dict(zip(clients, shares))[host], cmd)

    if dry_run:
        for host, cmd in host_cmds.items():
            log.info("[DRY RUN] Would run on %s: %s", host, cmd)
        return None

    # Start dns_responder on server
    session = run_dns_responder_session(config, timestamps=True, recieve_only=config.get("dns_responder_recieve_only", False))

    try:
        # Run the load tool concurrently on every client
        tool_timeout = config["runtime"] + 120
        run_results = ssh_run_many(host_cmds, timeout=tool_timeout)

        # Collect and save per-host output
        per_host = []  # (host, share, ToolResult, host_timed_out)
        for host, share in zip(clients, shares):
            stdout, stderr, rc, host_timed_out = extract_run_result(run_results[host])
            if host_timed_out:
                log.warning("%s timed out on %s at %d QPS (killed by ssh_run)",
                            tool.name, host, share)
            elif rc not in (0, None):
                log.warning("%s returned exit code %d on %s at %d QPS",
                            tool.name, rc, host, share)
                log.warning("stderr: %s", stderr[:500])

            store.save_raw_output(
                script_name,
                f"{tool.name}_{qps}qps_trial{trial}_{host_token(host)}_tool.txt",
                f"=== STDOUT ===\n{stdout}\n=== STDERR ===\n{stderr}"
                + ("\n=== TIMED OUT ===" if host_timed_out else ""),
            )

            try:
                tr = tool.parse_output(stdout)
            except Exception:
                if not host_timed_out:
                    log.warning("Could not parse %s output on %s", tool.name, host)
                tr = ToolResult()
            per_host.append((host, share, tr, host_timed_out))

        tool_timed_out = any(t for _, _, _, t in per_host)

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

        # Parse outputs — RX QPS (aggregate) is computed by dns_responder via -T flag
        resp_result = parse_dns_responder_output(resp_text)
        log.info("Achieved QPS according to dns_responder: %.2f (traffic window: %.3fs)",
                 resp_result.rx_qps, resp_result.actual_duration_secs)

        # Aggregate per-host tool counters for the ALL row
        agg = aggregate_tool_results([tr for _, _, tr, _ in per_host])

        all_row = {
            "tool": tool.name,
            "host": "ALL",
            "requested_qps": qps,
            "trial": trial,
            "achieved_qps_responder": resp_result.rx_qps,
            "actual_duration_secs": resp_result.actual_duration_secs,
            "rx_total": resp_result.rx_total,
            "tx_total": resp_result.tx_total,
            "drops": resp_result.drops,
            "timed_out": tool_timed_out,
            "tool_reported_qps": agg.achieved_qps,
            "tool_queries_sent": agg.queries_sent,
            "tool_queries_completed": agg.queries_completed,
            "tool_queries_lost": agg.queries_lost,
            "avg_latency_s": agg.avg_latency,
            "queries_not_received_dns_responder": agg.queries_sent - resp_result.rx_total,
            "queries_not_received_tool": resp_result.tx_total - agg.queries_completed,
        }
        store.add_result(all_row)

        # Per-host rows (responder metrics are server-side aggregate -> left blank)
        for host, share, tr, host_timed_out in per_host:
            store.add_result({
                "tool": tool.name,
                "host": host,
                "requested_qps": share,
                "trial": trial,
                "achieved_qps_responder": None,
                "actual_duration_secs": None,
                "rx_total": None,
                "tx_total": None,
                "drops": None,
                "timed_out": host_timed_out,
                "tool_reported_qps": tr.achieved_qps,
                "tool_queries_sent": tr.queries_sent,
                "tool_queries_completed": tr.queries_completed,
                "tool_queries_lost": tr.queries_lost,
                "avg_latency_s": tr.avg_latency,
            })

        return all_row

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

    qps = start_qps
    while qps <= max_qps:
        for trial in range(1, num_trials + 1):
            for tool in tools:
                log.info("Trial %d/%d for %s at %d QPS", trial, num_trials, tool.name, qps)
                try:
                    run_single_test(config, tool, qps, store, script_name, trial=trial)
                except Exception as e:
                    log.error("Unhandled error for %s at %d QPS trial %d: %s", tool.name, qps, trial, e)

                if not config.get("dry_run"):
                    log.info("Pausing %ds before next trial...", config["pause_between_runs"])
                    time.sleep(config["pause_between_runs"])

        qps += qps_step

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
