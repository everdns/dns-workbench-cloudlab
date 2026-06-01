#!/usr/bin/env python3
"""Print the max achieved_qps_responder for each tool in a results CSV.

Usage:
    python examples/max_qps_per_tool.py ~/tmp/results.csv
    python examples/max_qps_per_tool.py ~/tmp/results.csv ~/tmp/other.csv
"""
import argparse
import csv
import os
import sys
from collections import defaultdict


def max_qps_per_tool(csv_path):
    maxes = defaultdict(lambda: float("-inf"))
    with open(os.path.expanduser(csv_path), newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            qps_raw = row.get("achieved_qps_responder", "")
            if qps_raw in (None, ""):
                continue
            try:
                qps = float(qps_raw)
            except ValueError:
                continue
            tool = row["tool"]
            if qps > maxes[tool]:
                maxes[tool] = qps
    return dict(maxes)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", nargs="+", help="Path(s) to results CSV file(s)")
    args = parser.parse_args()

    for path in args.csv:
        maxes = max_qps_per_tool(path)
        print(f"{path}:")
        if not maxes:
            print("  (no rows)")
            continue
        for tool, qps in sorted(maxes.items(), key=lambda kv: kv[1], reverse=True):
            print(f"  {tool}: {qps:.2f}")


if __name__ == "__main__":
    main()
