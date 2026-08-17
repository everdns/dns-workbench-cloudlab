#!/usr/bin/env python3
"""Plot `queries_not_received_tool / actual_duration_secs` per tool.

The y-axis is labeled "Replies Dropped by Tool Per Second" — i.e. responses
the DNS responder sent (tx_total) that the load-testing tool did not count
as completed (tool_queries_completed), divided by the trial's actual
duration in seconds.

Usage:
    python examples/plot_queries_not_received_tool.py --csv tmp/results.csv
    python examples/plot_queries_not_received_tool.py --csv tmp/results.csv --output-dir charts/
    python examples/plot_queries_not_received_tool.py --csv tmp/results.csv --max-qps 500000
    python examples/plot_queries_not_received_tool.py --csv tmp/results.csv --no-legend
"""
import argparse
import csv
import logging
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from benchmark.charts import (
    _bold_axis_text,
    _save_standalone_legend,
    _tool_style,
    _trial_median_p1_p99,
)

log = logging.getLogger(__name__)


def load_from_csv(csv_path):
    results = []
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Only aggregate rows carry the responder-derived columns below.
            if row.get("host", "ALL") != "ALL":
                continue
            row["requested_qps"] = int(float(row["requested_qps"]))
            row["trial"] = int(float(row["trial"]))
            row["queries_not_received_tool"] = int(float(row["queries_not_received_tool"]))
            row["actual_duration_secs"] = float(row["actual_duration_secs"])
            row["replies_dropped_per_sec"] = (
                row["queries_not_received_tool"] / row["actual_duration_secs"]
            )
            results.append(row)
    return results


def plot_queries_not_received_tool(results, output_dir, legend: bool = True):
    os.makedirs(output_dir, exist_ok=True)

    by_tool_qps = defaultdict(lambda: defaultdict(list))
    for row in results:
        by_tool_qps[row["tool"]][row["requested_qps"]].append(row)

    all_tools = sorted(by_tool_qps.keys())

    fig, ax = plt.subplots(figsize=(7, 4))

    for tool in all_tools:
        style = _tool_style(tool, all_tools)
        x_vals = sorted(by_tool_qps[tool].keys())
        y_med, y_lo, y_hi = [], [], []
        for qps in x_vals:
            m, lo, hi = _trial_median_p1_p99(
                by_tool_qps[tool][qps], "replies_dropped_per_sec"
            )
            y_med.append(m)
            y_lo.append(lo)
            y_hi.append(hi)
        ax.errorbar(x_vals, y_med, yerr=[y_lo, y_hi], markersize=4,
                    capsize=3, linewidth=1.5, label=tool, **style)

    ax.set_xlabel("Requested QPS", fontsize=18, fontweight="bold")
    ax.set_ylabel("Replies Dropped/s", fontsize=18, fontweight="bold")
    if legend:
        ax.legend(loc="upper left", frameon=False,
                  prop={"size": 18, "weight": "bold"})
    ax.grid(True, alpha=0.3)
    _bold_axis_text(ax)

    path = os.path.join(output_dir, "replies_dropped_by_tool_per_sec.pdf")
    fig.savefig(path, dpi=150, bbox_inches="tight")
    if not legend:
        _save_standalone_legend(fig, path, ncol=min(len(all_tools), 3))
    plt.close(fig)
    return path


def main():
    parser = argparse.ArgumentParser(
        description="Plot queries_not_received_tool from a max_throughput CSV"
    )
    parser.add_argument("--csv", required=True, help="Path to results CSV file")
    parser.add_argument("--output-dir", default="charts",
                        help="Directory to save charts (default: charts/)")
    parser.add_argument("--max-qps", type=int, default=None,
                        help="Maximum requested QPS to include")
    parser.add_argument("--no-legend", dest="legend", action="store_false",
                        help="Omit the legend from the chart; it is written to a "
                             "separate <chart>_legend.pdf instead (default: legend drawn)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    results = load_from_csv(args.csv)
    log.info("Loaded %d rows from %s", len(results), args.csv)

    if args.max_qps is not None:
        results = [r for r in results if r["requested_qps"] <= args.max_qps]
        log.info("Filtered to %d rows with requested_qps <= %d", len(results), args.max_qps)

    if not results:
        log.error("No results to plot")
        sys.exit(1)

    path = plot_queries_not_received_tool(results, args.output_dir, legend=args.legend)
    log.info("Chart saved to %s", path)
    if not args.legend:
        stem, ext = os.path.splitext(path)
        log.info("Legend saved to %s", f"{stem}_legend{ext}")


if __name__ == "__main__":
    main()
