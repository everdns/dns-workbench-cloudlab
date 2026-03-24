#!/usr/bin/env python3
"""Generate max_throughput charts by re-parsing raw output files.

Usage:
    python examples/plot_max_throughput.py /path/to/dir/
    python examples/plot_max_throughput.py /path/to/dir/ --output-dir my_charts/

The input directory should contain raw/ and timestamps/ subdirectories.
"""
import argparse
import glob
import logging
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from benchmark.charts import plot_max_throughput
from benchmark.results import parse_dns_responder_output, read_first_last_timestamp
from benchmark.tools import get_tools

log = logging.getLogger(__name__)

# Matches filenames like "dnspyre_200000qps_tool.txt" or "dnspyre-workbench_100000qps_tool.txt"
TOOL_FILE_RE = re.compile(r"^(.+)_(\d+)qps_tool\.txt$")


def parse_tool_stdout(raw_text):
    """Extract stdout from a raw tool output file."""
    # Format: "=== STDOUT ===\n{stdout}\n=== STDERR ===\n{stderr}"
    parts = raw_text.split("=== STDERR ===")
    stdout_part = parts[0]
    if stdout_part.startswith("=== STDOUT ===\n"):
        stdout_part = stdout_part[len("=== STDOUT ===\n"):]
    return stdout_part.rstrip("\n")


def main():
    parser = argparse.ArgumentParser(
        description="Generate max_throughput charts from raw output files"
    )
    parser.add_argument("results_dir", help="Directory containing raw/ and timestamps/ subdirectories")
    parser.add_argument("--output-dir", default="charts", help="Directory to save charts (default: charts/)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    raw_dir = os.path.join(args.results_dir, "raw")
    ts_dir = os.path.join(args.results_dir, "timestamps")

    tool_files = glob.glob(os.path.join(raw_dir, "*_tool.txt"))
    if not tool_files:
        log.error("No raw tool output files found in %s", raw_dir)
        sys.exit(1)

    # Cache tool instances to avoid re-creating for each file
    tool_cache = {}
    results = []

    for tool_file in sorted(tool_files):
        filename = os.path.basename(tool_file)
        m = TOOL_FILE_RE.match(filename)
        if not m:
            log.warning("Skipping unrecognized file: %s", filename)
            continue

        tool_name = m.group(1)
        qps = int(m.group(2))

        # Get or create tool instance
        if tool_name not in tool_cache:
            try:
                tool_cache[tool_name] = get_tools([tool_name])[0]
            except ValueError:
                log.warning("Unknown tool '%s', skipping %s", tool_name, filename)
                continue

        tool = tool_cache[tool_name]

        # Read and parse tool output
        with open(tool_file) as f:
            raw_text = f.read()
        stdout = parse_tool_stdout(raw_text)
        tool_result = tool.parse_output(stdout)

        # Read and parse dns_responder output
        resp_file = os.path.join(raw_dir, f"{tool_name}_{qps}qps_responder.txt")
        if not os.path.exists(resp_file):
            log.warning("Missing responder file for %s at %d QPS, skipping", tool_name, qps)
            continue
        with open(resp_file) as f:
            resp_text = f.read()
        resp_result = parse_dns_responder_output(resp_text)

        # Read timestamps and compute actual runtime
        ts_file = os.path.join(ts_dir, f"{tool_name}_{qps}qps_timestamps.txt")
        actual_runtime_ns = 0
        if os.path.exists(ts_file):
            actual_runtime_ns = read_first_last_timestamp(ts_file)

        actual_qps = (resp_result.rx_total / actual_runtime_ns * 1e9) if actual_runtime_ns else 0.0

        row = {
            "tool": tool_name,
            "requested_qps": qps,
            "achieved_qps_responder": actual_qps,
            "actual_runtime_ns": actual_runtime_ns,
            "rx_total": resp_result.rx_total,
            "tx_total": resp_result.tx_total,
            "drops": resp_result.drops,
            "tool_reported_qps": tool_result.achieved_qps,
            "tool_queries_sent": tool_result.queries_sent,
            "tool_queries_completed": tool_result.queries_completed,
            "tool_queries_lost": tool_result.queries_lost,
        }

        if tool.reports_latency and tool_result.avg_latency is not None:
            row["avg_latency_s"] = tool_result.avg_latency

        results.append(row)
        log.info("Parsed %s at %d QPS: achieved %.0f QPS", tool_name, qps, actual_qps)

    if not results:
        log.error("No results parsed from raw files")
        sys.exit(1)

    tools_found = sorted(set(r["tool"] for r in results))
    log.info("Parsed %d results for tools: %s", len(results), tools_found)

    results.sort(key=lambda r: (r["tool"], r["requested_qps"]))
    plot_max_throughput(results, args.output_dir)
    log.info("Chart saved to %s", os.path.join(args.output_dir, "requested_vs_achieved.png"))


if __name__ == "__main__":
    main()
