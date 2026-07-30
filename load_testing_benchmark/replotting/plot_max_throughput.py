#!/usr/bin/env python3
"""Generate max_throughput charts from a CSV file or raw output directory.

Usage:
    # From CSV:
    python examples/plot_max_throughput.py --csv /path/to/results.csv

    # From raw data directory:
    python examples/plot_max_throughput.py --raw-dir /path/to/max_throughput/

    # Custom output directory:
    python examples/plot_max_throughput.py --csv results.csv --output-dir my_charts/

The raw data directory should contain raw/ and timestamps/ subdirectories.
"""
import argparse
import csv
import glob
import json
import logging
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from benchmark.charts import plot_legend, plot_max_throughput
from benchmark.results import (
    aggregate_tool_results,
    parse_dns_responder_output,
    read_first_last_timestamp,
)
from benchmark.tools import get_tools

log = logging.getLogger(__name__)

# Matches "dnspyre_200000qps_tool.txt", "dnspyre_100000qps_trial1_tool.txt" or
# multi-host "dnspyre_100000qps_trial1_host-a_tool.txt" (host group optional).
TOOL_FILE_RE = re.compile(
    r"^(?P<tool>.+)_(?P<qps>\d+)qps(?:_trial(?P<trial>\d+))?(?:_(?P<host>.+))?_tool\.txt$"
)


def parse_tool_stdout(raw_text):
    """Extract stdout from a raw tool output file."""
    # Format: "=== STDOUT ===\n{stdout}\n=== STDERR ===\n{stderr}"
    parts = raw_text.split("=== STDERR ===")
    stdout_part = parts[0]
    if stdout_part.startswith("=== STDOUT ===\n"):
        stdout_part = stdout_part[len("=== STDOUT ===\n"):]
    return stdout_part.rstrip("\n")


def load_from_csv(csv_path):
    """Load results from a CSV file."""
    results = []
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Plot only aggregate rows; per-host rows would double-count.
            if row.get("host", "ALL") != "ALL":
                continue
            for key in ("requested_qps", "trial", "rx_total", "tx_total", "drops",
                        "tool_queries_sent", "tool_queries_completed", "tool_queries_lost"):
                if key in row and row[key]:
                    row[key] = int(float(row[key]))
            for key in ("achieved_qps_responder", "actual_duration_secs",
                        "tool_reported_qps", "avg_latency_s"):
                if key in row and row[key]:
                    row[key] = float(row[key])
                elif key in row and not row[key]:
                    row[key] = None
            if "timed_out" in row:
                row["timed_out"] = row["timed_out"].lower() in ("true", "1", "yes")
            results.append(row)
    return results


def load_from_raw_dir(results_dir):
    """Load results by re-parsing raw tool output files."""
    raw_dir = os.path.join(results_dir, "raw")

    tool_files = glob.glob(os.path.join(raw_dir, "*_tool.txt"))
    if not tool_files:
        log.error("No raw tool output files found in %s", raw_dir)
        sys.exit(1)

    tool_cache = {}
    # Group per-host tool files that share one (per-test) responder file, then
    # aggregate them just like the live driver does.
    groups = {}  # resp_file -> {"tool", "qps", "results": [ToolResult]}

    for tool_file in sorted(tool_files):
        filename = os.path.basename(tool_file)
        m = TOOL_FILE_RE.match(filename)
        if not m:
            log.warning("Skipping unrecognized file: %s", filename)
            continue

        tool_name = m.group("tool")
        qps = int(m.group("qps"))
        host = m.group("host")

        if tool_name not in tool_cache:
            try:
                tool_cache[tool_name] = get_tools([tool_name])[0]
            except ValueError:
                log.warning("Unknown tool '%s', skipping %s", tool_name, filename)
                continue

        tool = tool_cache[tool_name]

        with open(tool_file) as f:
            raw_text = f.read()
        stdout = parse_tool_stdout(raw_text)
        tool_result = tool.parse_output(stdout)

        # The responder file is per-test (no host token). Strip the host segment
        # to recover its name; old single-host files have no host segment.
        if host:
            resp_file = tool_file.replace(f"_{host}_tool.txt", "_responder.txt")
        else:
            resp_file = tool_file.replace("_tool.txt", "_responder.txt")

        g = groups.setdefault(resp_file, {"tool": tool, "qps": qps, "results": []})
        g["results"].append(tool_result)

    results = []
    for resp_file, g in groups.items():
        if not os.path.exists(resp_file):
            log.warning("Missing responder file %s, skipping", os.path.basename(resp_file))
            continue
        with open(resp_file) as f:
            resp_text = f.read()
        resp_result = parse_dns_responder_output(resp_text)

        agg = aggregate_tool_results(g["results"])
        log.info("Achieved QPS according to dns_responder: %.2f (traffic window: %.3fs)",
                 resp_result.rx_qps, resp_result.actual_duration_secs)

        results.append({
            "tool": g["tool"].name,
            "host": "ALL",
            "requested_qps": g["qps"],
            "achieved_qps_responder": resp_result.rx_qps,
            "actual_duration_secs": resp_result.actual_duration_secs,
            "rx_total": resp_result.rx_total,
            "tx_total": resp_result.tx_total,
            "drops": resp_result.drops,
            "queries_not_received_dns_responder": agg.queries_sent - resp_result.rx_total,
            "queries_not_received_tool": resp_result.tx_total - agg.queries_completed,
        })

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Generate max_throughput charts from CSV or raw data"
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--csv", help="Path to results CSV file")
    source.add_argument("--raw-dir", help="Directory containing raw/ and timestamps/ subdirectories")
    parser.add_argument("--output-dir", default="charts", help="Directory to save charts (default: charts/)")
    parser.add_argument("--max-qps", type=int, default=None,
                        help="Maximum requested QPS to include in charts")
    parser.add_argument("--legend", action="store_true",
                        help="Draw the legend inside the chart (default: off, the "
                             "legend is written to a separate file instead)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    log.info("=== plot_max_throughput ===")
    log.info("  Source: %s", args.csv if args.csv else args.raw_dir)
    log.info("  Output dir: %s", args.output_dir)

    if args.csv:
        results = load_from_csv(args.csv)
        log.info("Loaded %d rows from %s", len(results), args.csv)
    else:
        results = load_from_raw_dir(args.raw_dir)
        log.info("Parsed %d result rows from raw output files", len(results))
        if results:
            os.makedirs(args.output_dir, exist_ok=True)
            csv_path = os.path.join(args.output_dir, "results.csv")
            all_fields = list(dict.fromkeys(k for row in results for k in row.keys()))
            with open(csv_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=all_fields, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(results)
            log.info("Saved CSV to %s", csv_path)
            json_path = os.path.join(args.output_dir, "results.json")
            with open(json_path, "w") as f:
                json.dump(results, f, indent=2, default=str)
            log.info("Saved JSON to %s", json_path)

    if not results:
        log.error("No results to plot")
        sys.exit(1)

    if args.max_qps is not None:
        results = [r for r in results if r["requested_qps"] <= args.max_qps]
        log.info("Filtered to %d rows with requested_qps <= %d", len(results), args.max_qps)
        if not results:
            log.error("No results remaining after filtering")
            sys.exit(1)

    tools_found = sorted(set(r["tool"] for r in results))
    log.info("Tools: %s", tools_found)

    results.sort(key=lambda r: (r["tool"], r["requested_qps"]))
    plot_max_throughput(results, args.output_dir, legend=args.legend)
    log.info("Chart saved to %s", os.path.join(args.output_dir, "requested_vs_achieved.pdf"))
    if not args.legend:
        plot_legend(results, args.output_dir)
        log.info("Legend saved to %s",
                 os.path.join(args.output_dir, "requested_vs_achieved_legend.pdf"))


if __name__ == "__main__":
    main()
