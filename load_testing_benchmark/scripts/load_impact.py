#!/usr/bin/env python3
"""Script 3: Load Generator Impact Analysis.

Evaluates how load generator choice affects DNS benchmarking results
by running all tools against multiple real DNS server implementations.
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
    add_script3_args,
    apply_cli_overrides,
    apply_script3_overrides,
    load_config,
)
from benchmark.dns_servers import (
    clear_dns_cache,
    ensure_dns_running,
    start_dns_service,
    stop_dns_service,
    wait_for_dns_ready,
)
from benchmark.remote import ssh_run
from benchmark.results import ResultStore
from benchmark.tools import get_tools

log = logging.getLogger(__name__)


def run_impact_test(config, tool, dns_service, qps, trial, store, script_name):
    """Run a single load impact test.

    Returns result dict or None on failure.
    """
    client = config["hosts"]["client"]
    dry_run = config.get("dry_run", False)

    tool.validate_params(config, qps)
    cmd = tool.build_command(config, qps)

    log.info("Impact test: %s vs %s at %d QPS, trial %d",
             tool.name, dns_service, qps, trial + 1)

    if dry_run:
        log.info("[DRY RUN] Would run: %s", cmd)
        return None

    try:
        tool_timeout = config["runtime"] + 120
        result = ssh_run(client, cmd, timeout=tool_timeout)

        tool_stdout = result.stdout
        tool_stderr = result.stderr

        if result.returncode != 0:
            log.warning("%s returned exit code %d", tool.name, result.returncode)

        store.save_raw_output(
            script_name,
            f"{dns_service}_{tool.name}_{qps}qps_trial{trial}.txt",
            f"=== STDOUT ===\n{tool_stdout}\n=== STDERR ===\n{tool_stderr}",
        )

        tool_result = tool.parse_output(tool_stdout)

        # Calculate answer rate
        answer_rate = 0.0
        if tool_result.queries_sent > 0:
            answer_rate = tool_result.queries_completed / tool_result.queries_sent * 100.0

        row = {
            "dns_service": dns_service,
            "tool": tool.name,
            "target_qps": qps,
            "trial": trial + 1,
            "achieved_qps": tool_result.achieved_qps,
            "queries_sent": tool_result.queries_sent,
            "queries_completed": tool_result.queries_completed,
            "queries_lost": tool_result.queries_lost,
            "answer_rate_pct": round(answer_rate, 4),
        }

        if tool.reports_latency:
            row["avg_latency_s"] = tool_result.avg_latency
            row["min_latency_s"] = tool_result.min_latency
            row["max_latency_s"] = tool_result.max_latency
            row["latency_stddev_s"] = tool_result.latency_stddev
            if tool_result.percentiles:
                for pct, val in tool_result.percentiles.items():
                    row[f"latency_{pct}_s"] = val

        store.add_result(row)
        return row

    except subprocess.TimeoutExpired:
        log.error("%s timed out at %d QPS trial %d", tool.name, qps, trial + 1)
        return None
    except Exception as e:
        log.error("Error running %s at %d QPS trial %d: %s",
                  tool.name, qps, trial + 1, e)
        return None


def main():
    parser = argparse.ArgumentParser(description="Script 3: Load Generator Impact Analysis")
    add_common_args(parser)
    add_script3_args(parser)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = load_config(args.config)
    config = apply_cli_overrides(config, args)
    config = apply_script3_overrides(config, args)
    config["dry_run"] = args.dry_run

    s3 = config["script3"]
    min_qps = s3["min_qps"]
    max_qps = s3["max_qps"]
    qps_step = s3["qps_step"]
    trials = s3["trials"]
    tool_max_qps = s3.get("tool_max_qps", {})
    clear_cache = bool(s3.get("clear_cache", False))

    services = config["dns_services"]["services"]
    if args.dns_services:
        services = args.dns_services

    tools = get_tools(config.get("tools"))
    output_dir = args.output_dir
    script_name = "load_impact"
    store = ResultStore(output_dir)

    log.info("=== Load Generator Impact Analysis ===")
    log.info("Tools: %s", [t.name for t in tools])
    log.info("DNS services: %s", services)
    log.info("QPS range: %d -> %d (step %d), %d trials",
             min_qps, max_qps, qps_step, trials)
    if tool_max_qps:
        log.info("Per-tool max QPS overrides: %s", tool_max_qps)
    if clear_cache:
        log.info("Cache clearing enabled: will clear before each tool run")

    for dns_service in services:
        log.info("=== Testing DNS service: %s ===", dns_service)

        try:
            # Stop any running services first
            stop_dns_service(config)
            time.sleep(2)

            # Start this DNS service
            start_dns_service(config, dns_service)
            wait_for_dns_ready(config, timeout=30)

        except Exception as e:
            log.error("Failed to start %s: %s. Skipping.", dns_service, e)
            continue

        try:
            qps = min_qps
            while qps <= max_qps:
                log.info("--- %s at %d QPS ---", dns_service, qps)

                for trial in range(trials):
                    for tool in tools:
                        tool_limit = tool_max_qps.get(tool.name, max_qps)
                        if qps > tool_limit:
                            log.debug("Skipping %s at %d QPS (max for tool: %d)",
                                      tool.name, qps, tool_limit)
                            continue

                        if clear_cache and not config.get("dry_run"):
                            try:
                                clear_dns_cache(config, dns_service)
                                ensure_dns_running(config, dns_service, timeout=30)
                            except Exception as e:
                                log.error(
                                    "Cache clear/ready failed for %s: %s. Skipping run.",
                                    dns_service, e,
                                )
                                continue

                        try:
                            run_impact_test(
                                config, tool, dns_service, qps, trial,
                                store, script_name,
                            )
                        except Exception as e:
                            log.error("Unhandled error: %s vs %s at %d QPS trial %d: %s",
                                      tool.name, dns_service, qps, trial + 1, e)

                        if not config.get("dry_run"):
                            log.info("Pausing %ds...", config["pause_between_runs"])
                            time.sleep(config["pause_between_runs"])

                qps += qps_step

        finally:
            # Always stop the DNS service when done
            try:
                stop_dns_service(config, dns_service)
            except Exception as e:
                log.warning("Failed to stop %s: %s", dns_service, e)

    # Export results
    csv_path = store.export_csv(script_name)
    json_path = store.export_json(script_name)
    log.info("Results exported to %s and %s", csv_path, json_path)

    # Generate charts
    try:
        from benchmark.charts import plot_load_impact
        charts_dir = os.path.join(output_dir, script_name, "charts")
        plot_load_impact(store.results, charts_dir)
        log.info("Charts saved to %s", charts_dir)
    except ImportError:
        log.warning("matplotlib not available, skipping chart generation")
    except Exception as e:
        log.error("Chart generation failed: %s", e)

    log.info("=== Load Generator Impact Analysis Complete ===")


if __name__ == "__main__":
    main()
