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


def run_impact_test(config, tool, dns_service, qps, trial, store, script_name, s3):
    """Run a single load impact test.

    Returns result dict or None on failure.
    """
    clients = get_clients(config)
    shares = split_qps(qps, len(clients))
    dry_run = config.get("dry_run", False)

    host_cmds = {}
    for host, share in zip(clients, shares):
        tool.validate_params(config, share)
        host_cmds[host] = tool.build_command(config, share)

    log.info("Impact test: %s vs %s at %d QPS, trial %d across %d host(s): %s",
             tool.name, dns_service, qps, trial + 1, len(clients),
             dict(zip(clients, shares)))

    collectl_enabled = bool(s3.get("collectl", False))
    collectl_margin = int(s3.get("collectl_margin", 5))
    collectl_session = None
    collectl_local_path = None

    if dry_run:
        for host, cmd in host_cmds.items():
            log.info("[DRY RUN] Would run on %s: %s", host, cmd)
        if collectl_enabled:
            runtime = config["runtime"]
            log.info(
                "[DRY RUN] Would run collectl on server for %d seconds (runtime=%d, margin=%d)",
                runtime + 2 * collectl_margin, runtime, collectl_margin,
            )
        return None

    if collectl_enabled:
        try:
            runtime = config["runtime"]
            remote_trail = (
                f"/tmp/collectl_{dns_service}_{tool.name}_{qps}_{trial}.txt"
            )
            collectl_session = run_collectl_session(config, runtime, remote_trail)
            collectl_local_path = os.path.join(
                store._ensure_dir(script_name, "collectl"),
                f"{dns_service}_{tool.name}_{qps}qps_trial{trial}.collectl.txt",
            )
        except Exception as e:
            log.warning("Failed to start collectl: %s. Continuing without it.", e)
            collectl_session = None

    def latency_fields(tr, include_percentiles):
        """Build latency columns for a row from a ToolResult."""
        fields = {}
        if tool.reports_latency:
            fields["avg_latency_s"] = tr.avg_latency
            fields["min_latency_s"] = tr.min_latency
            fields["max_latency_s"] = tr.max_latency
            fields["latency_stddev_s"] = tr.latency_stddev
            if include_percentiles and tr.percentiles:
                for pct, val in tr.percentiles.items():
                    fields[f"latency_{pct}_s"] = val
        return fields

    def answer_rate(tr):
        if tr.queries_sent > 0:
            return round(tr.queries_completed / tr.queries_sent * 100.0, 4)
        return 0.0

    try:
        tool_timeout = config["runtime"] + 2 * collectl_margin + 120
        run_results = ssh_run_many(host_cmds, timeout=tool_timeout)

        # Save per-host output and parse each host's metrics
        per_host = []  # (host, share, ToolResult)
        for host, share in zip(clients, shares):
            stdout, stderr, rc, host_timed_out = extract_run_result(run_results[host])
            if host_timed_out:
                log.warning("%s timed out on %s at %d QPS", tool.name, host, share)
            elif rc not in (0, None):
                log.warning("%s returned exit code %d on %s", tool.name, rc, host)

            store.save_raw_output(
                script_name,
                f"{dns_service}_{tool.name}_{qps}qps_trial{trial}_{host_token(host)}.txt",
                f"=== STDOUT ===\n{stdout}\n=== STDERR ===\n{stderr}",
            )
            try:
                tr = tool.parse_output(stdout)
            except Exception:
                tr = ToolResult()
            per_host.append((host, share, tr))

        # Aggregate across hosts for the ALL row
        agg = aggregate_tool_results([tr for _, _, tr in per_host])

        all_row = {
            "dns_service": dns_service,
            "tool": tool.name,
            "host": "ALL",
            "target_qps": qps,
            "trial": trial + 1,
            "achieved_qps": agg.achieved_qps,
            "queries_sent": agg.queries_sent,
            "queries_completed": agg.queries_completed,
            "queries_lost": agg.queries_lost,
            "answer_rate_pct": answer_rate(agg),
        }
        # Percentiles cannot be combined across hosts -> omit on aggregate.
        all_row.update(latency_fields(agg, include_percentiles=False))

        if collectl_session is not None:
            try:
                wait_collectl(
                    collectl_session["proc"],
                    timeout=collectl_margin + 30,
                )
                collect_collectl_file(
                    config,
                    collectl_session["output_file"],
                    collectl_local_path,
                )
                metrics = parse_collectl_file(
                    collectl_local_path, collectl_margin,
                )
                all_row.update({k: v for k, v in metrics.items() if v is not None})
            except Exception as e:
                log.warning(
                    "collectl collection/parse failed for %s vs %s at %d QPS trial %d: %s",
                    tool.name, dns_service, qps, trial + 1, e,
                )

        store.add_result(all_row)

        # Per-host rows carry full latency (incl. percentiles/stddev).
        for host, share, tr in per_host:
            host_row = {
                "dns_service": dns_service,
                "tool": tool.name,
                "host": host,
                "target_qps": share,
                "trial": trial + 1,
                "achieved_qps": tr.achieved_qps,
                "queries_sent": tr.queries_sent,
                "queries_completed": tr.queries_completed,
                "queries_lost": tr.queries_lost,
                "answer_rate_pct": answer_rate(tr),
            }
            host_row.update(latency_fields(tr, include_percentiles=True))
            store.add_result(host_row)

        return all_row

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
    warmup_cache = bool(s3.get("warmup_cache", False)) and not clear_cache

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
    elif warmup_cache:
        log.info("Cache warmup enabled: will pre-populate cache per service via dnsperf")
    if s3.get("collectl"):
        log.info("collectl enabled: will sample DNS server with margin=%ds per tool run",
                 int(s3.get("collectl_margin", 5)))

    for dns_service in services:
        log.info("=== Testing DNS service: %s ===", dns_service)

        if not config.get("dry_run"):
            try:
                # Stop any running services first
                stop_dns_service(config)
                time.sleep(2)

                # Start this DNS service
                start_dns_service(config, dns_service)
                wait_for_dns_ready(config, timeout=300)

                if warmup_cache:
                    try:
                        warmup_dns_cache(config)
                    except Exception as e:
                        log.warning("Cache warmup failed for %s: %s", dns_service, e)

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
                                store, script_name, s3,
                            )
                        except Exception as e:
                            log.error("Unhandled error: %s vs %s at %d QPS trial %d: %s",
                                      tool.name, dns_service, qps, trial + 1, e)

                        if not config.get("dry_run"):
                            log.info("Pausing %ds...", config["pause_between_runs"])
                            time.sleep(config["pause_between_runs"])

                qps += qps_step

        finally:
            # Always stop the DNS service when done (skipped on dry runs)
            if not config.get("dry_run"):
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
