#!/usr/bin/env python3
"""Generate qps_accuracy charts from a CSV file or raw output directory.

Usage:
    # From CSV:
    python examples/plot_qps_accuracy.py --csv /path/to/results.csv

    # From raw data directory:
    python examples/plot_qps_accuracy.py --raw-dir /path/to/qps_accuracy/

    # Custom output directory:
    python examples/plot_qps_accuracy.py --csv results.csv --output-dir my_charts/

The raw data directory should contain a timestamps/ subdirectory with files like:
    dnsperf_10000qps_trial0_timestamps.txt
"""
import argparse
import csv
import glob
import json
import logging
import os
import re
import sys
from multiprocessing import Pool, cpu_count


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from benchmark.charts import plot_pps_accuracy, plot_qps_accuracy
from benchmark.results import compute_accuracy_metrics, compute_actual_runtime, read_timestamps_file

log = logging.getLogger(__name__)

# Matches: tool_12345qps_trial0_timestamps.txt
TS_FILE_RE = re.compile(r"^(.+)_(\d+)qps_trial(\d+)_timestamps\.txt$")


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
            for key in ("mean_qps", "stddev", "max_deviation",
                        "expected_pps", "mean_pps", "pps_stddev",
                        "pps_max_deviation", "actual_runtime_ns",
                        "responder_avg_rx_pps", "responder_rx_total",
                        "responder_drops"):
                if key in row and row[key]:
                    row[key] = float(row[key])
            results.append(row)
    return results


# --- helper for one file ---
def _process_ts_file(args):
    ts_file, crop_s = args

    filename = os.path.basename(ts_file)
    m = TS_FILE_RE.match(filename)
    if not m:
        log.warning("Skipping unrecognized file: %s", filename)
        return []

    tool_name = m.group(1)
    target_qps = int(m.group(2))
    trial = int(m.group(3))

    timestamps = read_timestamps_file(ts_file)
    if len(timestamps) < 3:
        log.warning("Too few timestamps in %s, skipping", filename)
        return []

    actual_runtime_ns = compute_actual_runtime(timestamps)
    runtime_s = actual_runtime_ns / 1e9

    accuracy = compute_accuracy_metrics(
        timestamps, target_qps, runtime_s, crop_s=crop_s
    )

    rows = []
    for label, metrics in accuracy.items():
        rows.append({
            "tool": tool_name,
            "target_qps": target_qps,
            "trial": trial + 1,
            "interval": label,
            "actual_runtime_ns": actual_runtime_ns,
            "mean_qps": round(metrics.mean_qps, 2),
            "stddev": round(metrics.stddev, 2),
            "max_deviation": round(metrics.max_deviation, 2),
            "expected_pps": round(metrics.expected_pps, 2),
            "mean_pps": round(metrics.mean_pps, 2),
            "pps_stddev": round(metrics.pps_stddev, 2),
            "pps_max_deviation": round(metrics.pps_max_deviation, 2),
            "responder_avg_rx_pps": 0,
            "responder_rx_total": len(timestamps),
            "responder_drops": 0,
        })

    log.info(
        "Parsed %s at %d QPS trial %d: %d timestamps, %.1fs runtime",
        tool_name, target_qps, trial, len(timestamps), runtime_s
    )

    return rows


# --- main function ---
def load_from_raw_dir(raw_dir, crop_s, processes=None):
    ts_dir = os.path.join(raw_dir, "timestamps")
    if not os.path.isdir(ts_dir):
        if os.path.isdir(raw_dir) and glob.glob(os.path.join(raw_dir, "*_timestamps.txt")):
            ts_dir = raw_dir
        else:
            log.error("No timestamps/ subdirectory found in %s", raw_dir)
            sys.exit(1)

    ts_files = glob.glob(os.path.join(ts_dir, "*_timestamps.txt"))
    if not ts_files:
        log.error("No timestamp files found in %s", ts_dir)
        sys.exit(1)

    ts_files = sorted(ts_files)

    # Default to all CPUs
    processes = processes or cpu_count()

    log.info("Processing %d timestamp files with %d processes...", len(ts_files), processes)
    with Pool(processes) as pool:
        all_results = pool.map(_process_ts_file, [(f, crop_s) for f in ts_files])

    # Flatten results
    results = [row for sublist in all_results for row in sublist]

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Generate QPS/PPS accuracy charts from CSV or raw data"
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--csv", help="Path to results CSV file")
    source.add_argument("--raw-dir", help="Path to directory with timestamps/ subdirectory")
    parser.add_argument("--output-dir", default="charts",
                        help="Directory to save charts (default: charts/)")
    parser.add_argument("--crop", type=float, default=0,
                        help="Seconds to trim from start and end of timestamps before computing metrics (default: 0)")
    parser.add_argument("--max-qps", type=int, default=None,
                        help="Maximum target QPS to include in charts")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    log.info("=== plot_qps_accuracy ===")
    log.info("  Source: %s", args.csv if args.csv else args.raw_dir)
    log.info("  Output dir: %s", args.output_dir)
    log.info("  Crop: %.2fs", args.crop)

    if args.csv:
        results = load_from_csv(args.csv)
        log.info("Loaded %d rows from %s", len(results), args.csv)
    else:
        results = load_from_raw_dir(args.raw_dir, crop_s=args.crop)
        log.info("Computed %d result rows from raw timestamps", len(results))

    if not results:
        log.error("No results to plot")
        sys.exit(1)

    # When reading from raw files, export the computed results as CSV and JSON
    if args.raw_dir:
        os.makedirs(args.output_dir, exist_ok=True)

        csv_out = os.path.join(args.output_dir, "qps_accuracy.csv")
        fieldnames = list(results[0].keys())
        with open(csv_out, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(results)
        log.info("Saved CSV to %s", csv_out)

        json_out = os.path.join(args.output_dir, "qps_accuracy.json")
        with open(json_out, "w") as f:
            json.dump(results, f, indent=2)
        log.info("Saved JSON to %s", json_out)

    if args.max_qps is not None:
        results = [r for r in results if r["target_qps"] <= args.max_qps]
        log.info("Filtered to %d rows with target_qps <= %d", len(results), args.max_qps)
        if not results:
            log.error("No results remaining after filtering")
            sys.exit(1)

    tools_found = sorted(set(r["tool"] for r in results))
    intervals_found = sorted(set(r["interval"] for r in results if r["interval"] != "N/A"))
    log.info("Tools: %s, Intervals: %s", tools_found, intervals_found)

    plot_qps_accuracy(results, args.output_dir)
    plot_pps_accuracy(results, args.output_dir)
    log.info("Charts saved to %s", args.output_dir)


if __name__ == "__main__":
    main()
