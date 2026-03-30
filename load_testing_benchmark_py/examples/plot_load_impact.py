#!/usr/bin/env python3
"""Generate load_impact charts from a CSV file or raw output directory.

Usage:
    # From CSV:
    python examples/plot_load_impact.py --csv /path/to/results.csv

    # From raw data directory:
    python examples/plot_load_impact.py --raw-dir /path/to/load_impact/

    # Custom output directory:
    python examples/plot_load_impact.py --csv results.csv --output-dir my_charts/

The raw data directory should contain a raw/ subdirectory with files like:
    bind-ns_dnsperf_10000qps_trial0.txt
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

from benchmark.charts import plot_load_impact
from benchmark.tools import get_tools

log = logging.getLogger(__name__)

# Matches: bind-ns_dnsperf_10000qps_trial0.txt
RAW_FILE_RE = re.compile(r"^(.+?)_(.+)_(\d+)qps_trial(\d+)\.txt$")


def parse_tool_stdout(raw_text):
    """Extract stdout from a raw tool output file."""
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
            # Convert numeric fields
            for key in ("target_qps", "trial"):
                if key in row:
                    row[key] = int(row[key])
            for key in ("achieved_qps", "queries_sent", "queries_completed",
                        "queries_lost", "answer_rate_pct",
                        "avg_latency_s", "min_latency_s", "max_latency_s",
                        "latency_stddev_s"):
                if key in row and row[key]:
                    row[key] = float(row[key])
                elif key in row and not row[key]:
                    row[key] = None
            # Convert any latency percentile columns
            for key in list(row.keys()):
                if key.startswith("latency_") and key.endswith("_s") and key not in (
                    "avg_latency_s", "min_latency_s", "max_latency_s", "latency_stddev_s"
                ):
                    row[key] = float(row[key]) if row[key] else None
            results.append(row)
    return results


def load_from_raw_dir(raw_dir):
    """Load results by re-parsing raw tool output files."""
    if os.path.isdir(os.path.join(raw_dir, "raw")):
        search_dir = os.path.join(raw_dir, "raw")
    else:
        search_dir = raw_dir

    raw_files = glob.glob(os.path.join(search_dir, "*.txt"))
    if not raw_files:
        log.error("No raw output files found in %s", search_dir)
        sys.exit(1)

    tool_cache = {}
    results = []

    for raw_file in sorted(raw_files):
        filename = os.path.basename(raw_file)
        m = RAW_FILE_RE.match(filename)
        if not m:
            log.debug("Skipping unrecognized file: %s", filename)
            continue

        dns_service = m.group(1)
        tool_name = m.group(2)
        target_qps = int(m.group(3))
        trial = int(m.group(4))

        if tool_name not in tool_cache:
            try:
                tool_cache[tool_name] = get_tools([tool_name])[0]
            except ValueError:
                log.warning("Unknown tool '%s', skipping %s", tool_name, filename)
                continue

        tool = tool_cache[tool_name]

        with open(raw_file) as f:
            raw_text = f.read()
        stdout = parse_tool_stdout(raw_text)
        tool_result = tool.parse_output(stdout)

        answer_rate = 0.0
        if tool_result.queries_sent > 0:
            answer_rate = tool_result.queries_completed / tool_result.queries_sent * 100.0

        row = {
            "dns_service": dns_service,
            "tool": tool.name,
            "target_qps": target_qps,
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

        results.append(row)
        log.info("Parsed %s vs %s at %d QPS trial %d: sent=%d completed=%d rate=%.2f%%",
                 tool.name, dns_service, target_qps, trial + 1,
                 tool_result.queries_sent, tool_result.queries_completed, answer_rate)

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Generate load impact charts from CSV or raw data"
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--csv", help="Path to results CSV file")
    source.add_argument("--raw-dir", help="Path to directory with raw/ subdirectory")
    parser.add_argument("--output-dir", default="charts",
                        help="Directory to save charts (default: charts/)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    log.info("=== plot_load_impact ===")
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

    services_found = sorted(set(r["dns_service"] for r in results))
    tools_found = sorted(set(r["tool"] for r in results))
    log.info("DNS services: %s", services_found)
    log.info("Tools: %s", tools_found)

    results.sort(key=lambda r: (r["dns_service"], r["tool"], r["target_qps"], r["trial"]))
    plot_load_impact(results, args.output_dir)
    log.info("Charts saved to %s", args.output_dir)


if __name__ == "__main__":
    main()
