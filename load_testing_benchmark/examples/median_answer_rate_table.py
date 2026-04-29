#!/usr/bin/env python3
"""Print a LaTeX table summarizing answer-rate medians from a results CSV.

Two modes are supported via --mode:

  medians (default)
    A grid of medians: one row per DNS server, one column per target_qps.

  max-qps
    For each DNS server, the highest target_qps whose median answer_rate_pct
    is strictly above --threshold (default 99.99).

Usage:
    # Default: full medians grid for the QPS sweep in the CSV
    python examples/median_answer_rate_table.py /path/to/results.csv

    # Crop to a target QPS range (inclusive on both ends)
    python examples/median_answer_rate_table.py results.csv \
        --qps-start 500000 --qps-end 1500000

    # Max QPS at which median answer rate stays above 99.99
    python examples/median_answer_rate_table.py results.csv --mode max-qps

    # Custom threshold + siunitx-formatted QPS values
    python examples/median_answer_rate_table.py results.csv \
        --mode max-qps --threshold 99.9 --siunitx
"""
import argparse
import csv
import os
import statistics
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def load_csv(csv_path, qps_start, qps_end):
    """Return {(dns_service, target_qps): [answer_rate_pct, ...]}.

    Rows whose target_qps falls outside [qps_start, qps_end] are dropped.
    Rows with missing/non-numeric answer_rate_pct are skipped.
    """
    grouped = defaultdict(list)
    with open(os.path.expanduser(csv_path), newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                target_qps = int(row["target_qps"])
            except (KeyError, TypeError, ValueError):
                continue
            if target_qps < qps_start or target_qps > qps_end:
                continue
            raw = row.get("answer_rate_pct", "")
            if raw in (None, ""):
                continue
            try:
                answer_rate = float(raw)
            except ValueError:
                continue
            service = row.get("dns_service", "")
            grouped[(service, target_qps)].append(answer_rate)
    return grouped


def compute_medians(grouped):
    """Return {(dns_service, target_qps): median_answer_rate}."""
    return {key: statistics.median(values) for key, values in grouped.items()}


def format_qps(qps, siunitx):
    if siunitx:
        return rf"\num{{{qps}}}"
    return abbreviate_qps(qps)


def abbreviate_qps(qps):
    """Render an integer QPS as e.g. 1M, 1.1M, 500K, or the raw value."""
    if qps >= 1_000_000:
        return _strip_trailing_zeros(qps / 1_000_000) + "M"
    if qps >= 1_000:
        return _strip_trailing_zeros(qps / 1_000) + "K"
    return str(qps)


def _strip_trailing_zeros(value):
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text or "0"


def render_medians_table(grouped, decimals, caption, label, siunitx):
    services = sorted({s for s, _ in grouped})
    qps_values = sorted({q for _, q in grouped})

    if not services or not qps_values:
        raise SystemExit("No data to render after filtering.")

    medians = compute_medians(grouped)
    col_spec = "l" + "r" * len(qps_values)
    header_cells = ["DNS server"] + [format_qps(q, siunitx) for q in qps_values]

    lines = []
    lines.append(r"\begin{table*}[h]")
    lines.append(r"  \centering")
    lines.append(rf"  \begin{{tabular}}{{{col_spec}}}")
    lines.append(r"    \hline")
    lines.append("    " + " & ".join(header_cells) + r" \\")
    lines.append(r"    \hline")

    cell_fmt = f"{{:.{decimals}f}}"
    for service in services:
        cells = [service]
        for qps in qps_values:
            value = medians.get((service, qps))
            if value is None:
                cells.append("--")
            else:
                cells.append(cell_fmt.format(value))
        lines.append("    " + " & ".join(cells) + r" \\")

    lines.append(r"    \hline")
    lines.append(r"  \end{tabular}")
    lines.append(rf"  \caption{{{caption}}}")
    lines.append(rf"  \label{{{label}}}")
    lines.append(r"\end{table*}")
    return "\n".join(lines) + "\n"


def render_max_qps_table(grouped, threshold, caption, label, siunitx):
    services = sorted({s for s, _ in grouped})
    if not services:
        raise SystemExit("No data to render after filtering.")

    medians = compute_medians(grouped)

    lines = []
    lines.append(r"\begin{table}[h]")
    lines.append(r"  \centering")
    lines.append(r"  \begin{tabular}{lr}")
    lines.append(r"    \hline")
    lines.append(r"    DNS server & Max target QPS \\")
    lines.append(r"    \hline")

    for service in services:
        passing = [
            qps for (svc, qps), median_val in medians.items()
            if svc == service and median_val > threshold
        ]
        if passing:
            cell = format_qps(max(passing), siunitx)
        else:
            cell = "--"
        lines.append(f"    {service} & {cell} " + r"\\")

    lines.append(r"    \hline")
    lines.append(r"  \end{tabular}")
    lines.append(rf"  \caption{{{caption}}}")
    lines.append(rf"  \label{{{label}}}")
    lines.append(r"\end{table}")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csv", help="Path to results CSV file")
    parser.add_argument("--mode", choices=("medians", "max-qps"), default="medians",
                        help="Which table to emit (default: medians)")
    parser.add_argument("--qps-start", type=int, default=None,
                        help="Minimum target_qps to include (inclusive)")
    parser.add_argument("--qps-end", type=int, default=None,
                        help="Maximum target_qps to include (inclusive)")
    parser.add_argument("--decimals", type=int, default=2,
                        help="Decimal places for median values in 'medians' mode (default: 2)")
    parser.add_argument("--threshold", type=float, default=99.99,
                        help="Threshold used in 'max-qps' mode; cells must exceed this (default: 99.99)")
    parser.add_argument("--caption", default=None,
                        help="LaTeX caption text (mode-specific default if omitted)")
    parser.add_argument("--label", default=None,
                        help="LaTeX label (mode-specific default if omitted)")
    parser.add_argument("--siunitx", action="store_true",
                        help="Wrap QPS values in \\num{} (requires the siunitx package)")
    parser.add_argument("--output", default=None,
                        help="Write the table to this file (default: stdout)")
    args = parser.parse_args()

    qps_start = args.qps_start if args.qps_start is not None else float("-inf")
    qps_end = args.qps_end if args.qps_end is not None else float("inf")

    grouped = load_csv(args.csv, qps_start, qps_end)

    if args.mode == "medians":
        caption = args.caption if args.caption is not None else (
            r"Median answer rate (\%) by DNS server and target QPS."
        )
        label = args.label if args.label is not None else "tab:median-answer-rate"
        table = render_medians_table(grouped, args.decimals, caption, label, args.siunitx)
    else:
        caption = args.caption if args.caption is not None else (
            rf"Max target QPS at which median answer rate exceeds {args.threshold}\%."
        )
        label = args.label if args.label is not None else "tab:max-qps-above-threshold"
        table = render_max_qps_table(grouped, args.threshold, caption, label, args.siunitx)

    if args.output:
        with open(os.path.expanduser(args.output), "w") as f:
            f.write(table)
    else:
        sys.stdout.write(table)


if __name__ == "__main__":
    main()
