import logging
import os
from collections import defaultdict

import math
import yaml

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

log = logging.getLogger(__name__)


def _interval_sort_key(label):
    """Sort interval labels by numeric timescale (e.g. 10ms < 100ms < 1s)."""
    import re
    m = re.match(r"(\d+)(ns|ms|s)", label)
    if not m:
        return float("inf")
    val = int(m.group(1))
    unit = m.group(2)
    multipliers = {"ns": 1, "ms": 1_000_000, "s": 1_000_000_000}
    return val * multipliers.get(unit, float("inf"))


LINE_STYLES = ["-", "--", "-.", ":", (0, (3, 1, 1, 1)), (0, (5, 2)), (0, (1, 1)), (0, (3, 5, 1, 5))]
MARKERS = ["o", "s", "^", "D", "v", "P", "X", "*"]
COLORS = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728",
    "#9467bd", "#8c564b", "#e377c2", "#7f7f7f",
    "#bcbd22", "#17becf", "#aec7e8", "#ffbb78",
]

# Load fixed tool style registry from chart_config.yaml
_TOOL_STYLE_REGISTRY = {}
_CHART_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "chart_config.yaml",
)
if os.path.exists(_CHART_CONFIG_PATH):
    with open(_CHART_CONFIG_PATH) as _f:
        _chart_cfg = yaml.safe_load(_f) or {}
    _TOOL_STYLE_REGISTRY = _chart_cfg.get("tool_styles", {})


def _tool_style(tool_name, all_tools_sorted):
    """Return a dict of plot kwargs (color, marker, linestyle) for a tool.

    Uses the fixed index from chart_config.yaml if available, otherwise
    falls back to the next unused index after the registry entries.
    """
    if tool_name in _TOOL_STYLE_REGISTRY:
        i = _TOOL_STYLE_REGISTRY[tool_name]
    else:
        max_registered = max(_TOOL_STYLE_REGISTRY.values()) + 1 if _TOOL_STYLE_REGISTRY else 0
        unknown_tools = sorted(t for t in all_tools_sorted if t not in _TOOL_STYLE_REGISTRY)
        if tool_name in unknown_tools:
            i = max_registered + unknown_tools.index(tool_name)
        else:
            i = max_registered
        log.warning("Tool '%s' not in chart_config.yaml, using fallback index %d", tool_name, i)
    return dict(
        color=COLORS[i % len(COLORS)],
        marker=MARKERS[i % len(MARKERS)],
        linestyle=LINE_STYLES[i % len(LINE_STYLES)],
    )


def _trial_mean_std(rows, key):
    """Return (mean, stddev) of rows[key] across trials."""
    vals = [r[key] for r in rows]
    n = len(vals)
    mean = sum(vals) / n
    if n > 1:
        variance = sum((v - mean) ** 2 for v in vals) / (n - 1)
        std = math.sqrt(variance)
    else:
        std = 0.0
    return mean, std


def plot_max_throughput(results, output_dir):
    """Plot requested vs achieved QPS per tool (Script 1).

    Args:
        results: list of dicts with keys: tool, requested_qps, achieved_qps_responder, trial
        output_dir: directory to save charts
    """
    os.makedirs(output_dir, exist_ok=True)

    # Group by tool -> requested_qps -> list of achieved values (across trials)
    by_tool_qps = defaultdict(lambda: defaultdict(list))
    for row in results:
        by_tool_qps[row["tool"]][row["requested_qps"]].append(row)

    all_tools = sorted(by_tool_qps.keys())

    fig, ax = plt.subplots(figsize=(12, 7))

    for tool in all_tools:
        style = _tool_style(tool, all_tools)
        x_vals = sorted(by_tool_qps[tool].keys())
        y_mean, y_err = [], []
        for qps in x_vals:
            mean, std = _trial_mean_std(by_tool_qps[tool][qps], "achieved_qps_responder")
            y_mean.append(mean)
            y_err.append(std)
        ax.errorbar(x_vals, y_mean, yerr=y_err, markersize=4,
                    capsize=3, linewidth=1.5, label=tool, **style)

    # Plot ideal line (y=x)
    all_x = [row["requested_qps"] for row in results]
    if all_x:
        max_val = max(all_x)
        min_val = min(all_x)
        ax.plot([min_val, max_val], [min_val, max_val], "--", color="gray", alpha=0.5, label="Ideal (y=x)")

    ax.set_xlabel("Requested QPS")
    ax.set_ylabel("Achieved QPS (dns_responder)")
    ax.set_title("Maximum Throughput: Requested vs Achieved QPS")
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(True, alpha=0.3)

    path = os.path.join(output_dir, "requested_vs_achieved.pdf")
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def plot_qps_accuracy(results, output_dir):
    """Plot QPS accuracy metrics per tool and interval (Script 2).

    Args:
        results: list of dicts with keys: tool, target_qps, interval, mean_qps, stddev, max_deviation
        output_dir: directory to save charts
    """
    os.makedirs(output_dir, exist_ok=True)

    intervals = set(row["interval"] for row in results if row["interval"] != "N/A")
    all_tools = sorted(set(row["tool"] for row in results))

    for interval in sorted(intervals, key=_interval_sort_key):
        # Filter to this interval, average across trials
        by_tool_qps = defaultdict(lambda: defaultdict(list))
        for row in results:
            if row["interval"] != interval:
                continue
            by_tool_qps[row["tool"]][row["target_qps"]].append(row)

        # --- Mean QPS chart ---
        fig, ax = plt.subplots(figsize=(12, 7))
        for tool in sorted(by_tool_qps.keys()):
            style = _tool_style(tool, all_tools)
            x_vals = sorted(by_tool_qps[tool].keys())
            y_mean, y_err = [], []
            for qps in x_vals:
                mean, std = _trial_mean_std(by_tool_qps[tool][qps], "mean_qps")
                y_mean.append(mean)
                y_err.append(std)
            ax.errorbar(x_vals, y_mean, yerr=y_err, markersize=4,
                        capsize=3, linewidth=1.5, label=tool, **style)

        if x_vals:
            ax.plot([min(x_vals), max(x_vals)], [min(x_vals), max(x_vals)],
                    "--", color="gray", alpha=0.5, label="Ideal")

        ax.set_xlabel("Target QPS")
        ax.set_ylabel(f"Mean Achieved QPS ({interval} intervals)")
        ax.set_title(f"QPS Accuracy: Mean Achieved vs Target ({interval})")
        ax.legend(loc="upper left", fontsize=8)
        ax.grid(True, alpha=0.3)

        path = os.path.join(output_dir, f"accuracy_mean_{interval}.pdf")
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)

        # --- StdDev chart ---
        fig, ax = plt.subplots(figsize=(12, 7))
        for tool in sorted(by_tool_qps.keys()):
            style = _tool_style(tool, all_tools)
            x_vals = sorted(by_tool_qps[tool].keys())
            y_mean, y_err = [], []
            for qps in x_vals:
                mean, std = _trial_mean_std(by_tool_qps[tool][qps], "stddev")
                y_mean.append(mean)
                y_err.append(std)
            ax.errorbar(x_vals, y_mean, yerr=y_err, markersize=4,
                        capsize=3, linewidth=1.5, label=tool, **style)

        ax.set_xlabel("Target QPS")
        ax.set_ylabel(f"QPS Standard Deviation ({interval} intervals)")
        ax.set_title(f"QPS Accuracy: Standard Deviation ({interval})")
        ax.legend(loc="upper left", fontsize=8)
        ax.grid(True, alpha=0.3)

        path = os.path.join(output_dir, f"accuracy_stddev_{interval}.pdf")
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)

        # --- Max Deviation chart ---
        fig, ax = plt.subplots(figsize=(12, 7))
        for tool in sorted(by_tool_qps.keys()):
            style = _tool_style(tool, all_tools)
            x_vals = sorted(by_tool_qps[tool].keys())
            y_mean, y_err = [], []
            for qps in x_vals:
                mean, std = _trial_mean_std(by_tool_qps[tool][qps], "max_deviation")
                y_mean.append(mean)
                y_err.append(std)
            ax.errorbar(x_vals, y_mean, yerr=y_err, markersize=4,
                        capsize=3, linewidth=1.5, label=tool, **style)

        ax.set_xlabel("Target QPS")
        ax.set_ylabel(f"Max Deviation from Target ({interval} intervals)")
        ax.set_title(f"QPS Accuracy: Maximum Deviation ({interval})")
        ax.legend(loc="upper left", fontsize=8)
        ax.grid(True, alpha=0.3)

        path = os.path.join(output_dir, f"accuracy_maxdev_{interval}.pdf")
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)

    # --- Combined 3x3 grid: rows=metrics, cols=intervals ---
    sorted_intervals = sorted(intervals, key=_interval_sort_key)
    if sorted_intervals:
        metrics_config = [
            ("mean_qps", "Mean Achieved QPS", True),
            ("stddev", "QPS Standard Deviation", False),
            ("max_deviation", "Max Deviation from Target", False),
        ]
        fig, axes = plt.subplots(3, len(sorted_intervals),
                                 figsize=(6 * len(sorted_intervals), 15),
                                 squeeze=False)

        for col, interval in enumerate(sorted_intervals):
            by_tool_qps = defaultdict(lambda: defaultdict(list))
            for row in results:
                if row["interval"] != interval:
                    continue
                by_tool_qps[row["tool"]][row["target_qps"]].append(row)

            for metric_row, (key, ylabel, show_ideal) in enumerate(metrics_config):
                ax = axes[metric_row][col]
                for tool in sorted(by_tool_qps.keys()):
                    style = _tool_style(tool, all_tools)
                    x_vals = sorted(by_tool_qps[tool].keys())
                    y_mean, y_err = [], []
                    for qps in x_vals:
                        mean, std = _trial_mean_std(by_tool_qps[tool][qps], key)
                        y_mean.append(mean)
                        y_err.append(std)
                    ax.errorbar(x_vals, y_mean, yerr=y_err, markersize=3,
                                capsize=3, linewidth=1.5, label=tool, **style)

                if show_ideal and x_vals:
                    ax.plot([min(x_vals), max(x_vals)], [min(x_vals), max(x_vals)],
                            "--", color="gray", alpha=0.5, label="Ideal")

                ax.set_xlabel("Target QPS")
                ax.set_ylabel(f"{ylabel} ({interval})")
                ax.set_title(f"{ylabel} ({interval})")
                ax.legend(loc="best", fontsize=6)
                ax.grid(True, alpha=0.3)

        fig.suptitle("QPS Accuracy: All Metrics and Intervals", fontsize=14, y=1.01)
        fig.tight_layout()
        path = os.path.join(output_dir, "qps_accuracy_combined.pdf")
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)


def plot_pps_accuracy(results, output_dir):
    """Plot PPS accuracy metrics per tool and interval (Script 2).

    Args:
        results: list of dicts with keys: tool, target_qps, interval,
                 expected_pps, mean_pps, pps_stddev, pps_max_deviation
        output_dir: directory to save charts
    """
    os.makedirs(output_dir, exist_ok=True)

    intervals = set(row["interval"] for row in results if row["interval"] != "N/A")
    all_tools = sorted(set(row["tool"] for row in results))

    for interval in sorted(intervals, key=_interval_sort_key):
        # Group by tool and expected_pps (interval-specific x-axis)
        by_tool_pps = defaultdict(lambda: defaultdict(list))
        for row in results:
            if row["interval"] != interval:
                continue
            exp_pps = row["expected_pps"]
            by_tool_pps[row["tool"]][exp_pps].append(row)

        # --- Mean PPS chart ---
        fig, ax = plt.subplots(figsize=(12, 7))
        for tool in sorted(by_tool_pps.keys()):
            style = _tool_style(tool, all_tools)
            x_vals = sorted(by_tool_pps[tool].keys())
            y_mean, y_err = [], []
            for exp_pps in x_vals:
                mean, std = _trial_mean_std(by_tool_pps[tool][exp_pps], "mean_pps")
                y_mean.append(mean)
                y_err.append(std)
            ax.errorbar(x_vals, y_mean, yerr=y_err, markersize=4,
                        capsize=3, linewidth=1.5, label=tool, **style)

        if x_vals:
            ax.plot([min(x_vals), max(x_vals)], [min(x_vals), max(x_vals)],
                    "--", color="gray", alpha=0.5, label="Ideal (y=x)")

        ax.set_xlabel(f"Expected Packet Count ({interval} intervals)")
        ax.set_ylabel(f"Mean Packet Count ({interval} intervals)")
        ax.set_title(f"Packet Count Accuracy: Mean Achieved vs Expected ({interval})")
        ax.legend(loc="upper left", fontsize=8)
        ax.grid(True, alpha=0.3)

        path = os.path.join(output_dir, f"pps_mean_{interval}.pdf")
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)

        # --- PPS StdDev chart ---
        fig, ax = plt.subplots(figsize=(12, 7))
        for tool in sorted(by_tool_pps.keys()):
            style = _tool_style(tool, all_tools)
            x_vals = sorted(by_tool_pps[tool].keys())
            y_mean, y_err = [], []
            for exp_pps in x_vals:
                mean, std = _trial_mean_std(by_tool_pps[tool][exp_pps], "pps_stddev")
                y_mean.append(mean)
                y_err.append(std)
            ax.errorbar(x_vals, y_mean, yerr=y_err, markersize=4,
                        capsize=3, linewidth=1.5, label=tool, **style)

        ax.set_xlabel(f"Expected Packet Count ({interval} intervals)")
        ax.set_ylabel(f"Packet Count Standard Deviation ({interval} intervals)")
        ax.set_title(f"Packet Count Accuracy: Standard Deviation ({interval})")
        ax.legend(loc="upper left", fontsize=8)
        ax.grid(True, alpha=0.3)

        path = os.path.join(output_dir, f"pps_stddev_{interval}.pdf")
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)

        # --- PPS Max Deviation chart ---
        fig, ax = plt.subplots(figsize=(12, 7))
        for tool in sorted(by_tool_pps.keys()):
            style = _tool_style(tool, all_tools)
            x_vals = sorted(by_tool_pps[tool].keys())
            y_mean, y_err = [], []
            for exp_pps in x_vals:
                mean, std = _trial_mean_std(by_tool_pps[tool][exp_pps], "pps_max_deviation")
                y_mean.append(mean)
                y_err.append(std)
            ax.errorbar(x_vals, y_mean, yerr=y_err, markersize=4,
                        capsize=3, linewidth=1.5, label=tool, **style)

        ax.set_xlabel(f"Expected Packet Count ({interval} intervals)")
        ax.set_ylabel(f"Max Packet Count Deviation from Expected ({interval} intervals)")
        ax.set_title(f"Packet Count Accuracy: Maximum Deviation ({interval})")
        ax.legend(loc="upper left", fontsize=8)
        ax.grid(True, alpha=0.3)

        path = os.path.join(output_dir, f"pps_maxdev_{interval}.pdf")
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)

    # --- Combined 3x3 grid: rows=metrics, cols=intervals ---
    sorted_intervals = sorted(intervals, key=_interval_sort_key)
    if sorted_intervals:
        metrics_config = [
            ("mean_pps", "Mean Packet Count", True),
            ("pps_stddev", "Packet Count Standard Deviation", False),
            ("pps_max_deviation", "Max Packet Count Deviation from Expected", False),
        ]
        fig, axes = plt.subplots(3, len(sorted_intervals),
                                 figsize=(6 * len(sorted_intervals), 15),
                                 squeeze=False)

        for col, interval in enumerate(sorted_intervals):
            by_tool_pps = defaultdict(lambda: defaultdict(list))
            for row in results:
                if row["interval"] != interval:
                    continue
                by_tool_pps[row["tool"]][row["expected_pps"]].append(row)

            for metric_row, (key, ylabel, show_ideal) in enumerate(metrics_config):
                ax = axes[metric_row][col]
                for tool in sorted(by_tool_pps.keys()):
                    style = _tool_style(tool, all_tools)
                    x_vals = sorted(by_tool_pps[tool].keys())
                    y_mean, y_err = [], []
                    for exp_pps in x_vals:
                        mean, std = _trial_mean_std(by_tool_pps[tool][exp_pps], key)
                        y_mean.append(mean)
                        y_err.append(std)
                    ax.errorbar(x_vals, y_mean, yerr=y_err, markersize=3,
                                capsize=3, linewidth=1.5, label=tool, **style)

                if show_ideal and x_vals:
                    ax.plot([min(x_vals), max(x_vals)], [min(x_vals), max(x_vals)],
                            "--", color="gray", alpha=0.5, label="Ideal (y=x)")

                ax.set_xlabel(f"Expected Packet Count ({interval})")
                ax.set_ylabel(f"{ylabel} ({interval})")
                ax.set_title(f"{ylabel} ({interval})")
                ax.legend(loc="best", fontsize=6)
                ax.grid(True, alpha=0.3)

        fig.suptitle("PPS Accuracy: All Metrics and Intervals", fontsize=14, y=1.01)
        fig.tight_layout()
        path = os.path.join(output_dir, "pps_accuracy_combined.pdf")
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)


def plot_load_impact(results, output_dir):
    """Plot load generator impact analysis charts (Script 3).

    Args:
        results: list of dicts with keys: dns_service, tool, target_qps,
                 achieved_qps, answer_rate_pct, avg_latency_s, etc.
        output_dir: directory to save charts
    """
    os.makedirs(output_dir, exist_ok=True)

    dns_services = sorted(set(row["dns_service"] for row in results))
    all_tools = sorted(set(row["tool"] for row in results))

    for dns_service in dns_services:
        service_results = [r for r in results if r["dns_service"] == dns_service]

        # Average across trials
        by_tool_qps = defaultdict(lambda: defaultdict(list))
        for row in service_results:
            by_tool_qps[row["tool"]][row["target_qps"]].append(row)

        # --- Answer Rate vs QPS ---
        fig, ax = plt.subplots(figsize=(12, 7))
        for tool in sorted(by_tool_qps.keys()):
            style = _tool_style(tool, all_tools)
            x_vals = sorted(by_tool_qps[tool].keys())
            y_mean, y_err = [], []
            for qps in x_vals:
                mean, std = _trial_mean_std(by_tool_qps[tool][qps], "answer_rate_pct")
                y_mean.append(mean)
                y_err.append(std)
            ax.errorbar(x_vals, y_mean, yerr=y_err, markersize=4,
                        capsize=3, linewidth=1.5, label=tool, **style)

        ax.axhline(y=99.99, color="red", linestyle="--", alpha=0.5, label="99.99% threshold")
        ax.set_xlabel("Target QPS")
        ax.set_ylabel("Answer Rate (%)")
        ax.set_title(f"Answer Rate vs QPS — {dns_service}")
        ax.legend(loc="lower left", fontsize=8)
        ax.grid(True, alpha=0.3)
        ax.set_ylim(bottom=max(0, ax.get_ylim()[0]), top=101)

        path = os.path.join(output_dir, f"{dns_service}_answer_rate.pdf")
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)

        # --- Latency vs QPS (for tools that report latency) ---
        latency_tools = {
            tool for tool in by_tool_qps
            if any(r.get("avg_latency_s") is not None
                   for rows in by_tool_qps[tool].values() for r in rows)
        }

        if latency_tools:
            fig, ax = plt.subplots(figsize=(12, 7))
            for tool in sorted(latency_tools):
                style = _tool_style(tool, all_tools)
                x_vals = sorted(by_tool_qps[tool].keys())
                y_mean, y_err = [], []
                for qps in x_vals:
                    rows = by_tool_qps[tool][qps]
                    lats = [r["avg_latency_s"] * 1000 for r in rows
                            if r.get("avg_latency_s") is not None]
                    n = len(lats)
                    mean = sum(lats) / n if n else 0
                    if n > 1:
                        variance = sum((v - mean) ** 2 for v in lats) / (n - 1)
                        std = math.sqrt(variance)
                    else:
                        std = 0.0
                    y_mean.append(mean)
                    y_err.append(std)
                ax.errorbar(x_vals, y_mean, yerr=y_err, markersize=4,
                            capsize=3, linewidth=1.5, label=tool, **style)

            ax.set_xlabel("Target QPS")
            ax.set_ylabel("Average Latency (ms)")
            ax.set_title(f"Average Latency vs QPS — {dns_service}")
            ax.legend(loc="upper left", fontsize=8)
            ax.grid(True, alpha=0.3)

            path = os.path.join(output_dir, f"{dns_service}_latency.pdf")
            fig.savefig(path, dpi=150, bbox_inches="tight")
            plt.close(fig)

        # --- Queries Sent vs Answers Received ---
        fig, ax = plt.subplots(figsize=(12, 7))

        all_sent = []
        for tool in sorted(by_tool_qps.keys()):
            style = _tool_style(tool, all_tools)
            x_vals = sorted(by_tool_qps[tool].keys())
            sent_mean, sent_err = [], []
            comp_mean, comp_err = [], []
            for qps in x_vals:
                mean_s, std_s = _trial_mean_std(by_tool_qps[tool][qps], "queries_sent")
                mean_c, std_c = _trial_mean_std(by_tool_qps[tool][qps], "queries_completed")
                sent_mean.append(mean_s)
                sent_err.append(std_s)
                comp_mean.append(mean_c)
                comp_err.append(std_c)
            ax.errorbar(sent_mean, comp_mean, xerr=sent_err, yerr=comp_err,
                        markersize=4, capsize=3, linewidth=1.5, label=tool, **style)
            all_sent.extend(sent_mean)

        if all_sent:
            lo, hi = min(all_sent), max(all_sent)
            ax.plot([lo, hi], [lo, hi], "--", color="gray", alpha=0.5, label="Ideal (y=x)")

        ax.set_xlabel("Queries Sent")
        ax.set_ylabel("Answers Received")
        ax.set_title(f"Queries Sent vs Answers Received — {dns_service}")
        ax.legend(loc="upper left", fontsize=7)
        ax.grid(True, alpha=0.3)

        path = os.path.join(output_dir, f"{dns_service}_qps_comparison.pdf")
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)

    # --- Combined Grid: all DNS services x (answer_rate, latency, qps_comparison) ---
    _plot_load_impact_grid(results, all_tools, output_dir)

    # --- 99.99% Threshold Summary Table ---
    _generate_threshold_summary(results, output_dir)


def _plot_load_impact_grid(results, all_tools, output_dir):
    """Combined grid chart: rows = DNS services, cols = answer_rate / latency / qps_comparison."""
    dns_services = sorted(set(row["dns_service"] for row in results))
    n_rows = len(dns_services)
    if n_rows == 0:
        return

    # Build per-service aggregated data once
    service_data = {}
    for dns_service in dns_services:
        service_results = [r for r in results if r["dns_service"] == dns_service]
        by_tool_qps = defaultdict(lambda: defaultdict(list))
        for row in service_results:
            by_tool_qps[row["tool"]][row["target_qps"]].append(row)
        service_data[dns_service] = by_tool_qps

    n_cols = 3
    fig, axes = plt.subplots(
        n_rows, n_cols,
        figsize=(6 * n_cols, 4 * n_rows),
        squeeze=False,
    )
    fig.suptitle("Load Generator Impact — All DNS Services", fontsize=14, fontweight="bold", y=1.01)

    col_titles = ["Answer Rate (%)", "Average Latency (ms)", "Queries Sent vs Answers Received"]
    for col, title in enumerate(col_titles):
        axes[0][col].set_title(title, fontsize=11, fontweight="bold")

    for row_idx, dns_service in enumerate(dns_services):
        by_tool_qps = service_data[dns_service]
        tools_here = sorted(by_tool_qps.keys())

        # Row label on the leftmost axis
        axes[row_idx][0].set_ylabel(f"{dns_service}\nAnswer Rate (%)", fontsize=9)

        # --- Col 0: Answer Rate ---
        ax = axes[row_idx][0]
        for tool in tools_here:
            style = _tool_style(tool, all_tools)
            x_vals = sorted(by_tool_qps[tool].keys())
            y_mean, y_err = [], []
            for qps in x_vals:
                mean, std = _trial_mean_std(by_tool_qps[tool][qps], "answer_rate_pct")
                y_mean.append(mean)
                y_err.append(std)
            ax.errorbar(x_vals, y_mean, yerr=y_err, markersize=3,
                        capsize=2, linewidth=1.2, label=tool, **style)
        ax.axhline(y=99.99, color="red", linestyle="--", alpha=0.5, linewidth=0.8)
        ax.set_xlabel("Target QPS", fontsize=8)
        ax.set_ylim(bottom=max(0, ax.get_ylim()[0]), top=101)
        ax.grid(True, alpha=0.3)
        ax.tick_params(labelsize=7)
        ax.legend(fontsize=6, loc="lower left")

        # --- Col 1: Latency ---
        ax = axes[row_idx][1]
        latency_tools = {
            tool for tool in by_tool_qps
            if any(r.get("avg_latency_s") is not None
                   for rows in by_tool_qps[tool].values() for r in rows)
        }
        if latency_tools:
            for tool in sorted(latency_tools):
                style = _tool_style(tool, all_tools)
                x_vals = sorted(by_tool_qps[tool].keys())
                y_mean, y_err = [], []
                for qps in x_vals:
                    rows = by_tool_qps[tool][qps]
                    lats = [r["avg_latency_s"] * 1000 for r in rows
                            if r.get("avg_latency_s") is not None]
                    n = len(lats)
                    mean = sum(lats) / n if n else 0
                    std = math.sqrt(sum((v - mean) ** 2 for v in lats) / (n - 1)) if n > 1 else 0.0
                    y_mean.append(mean)
                    y_err.append(std)
                ax.errorbar(x_vals, y_mean, yerr=y_err, markersize=3,
                            capsize=2, linewidth=1.2, label=tool, **style)
            ax.legend(fontsize=6, loc="upper left")
        else:
            ax.text(0.5, 0.5, "No latency data", ha="center", va="center",
                    transform=ax.transAxes, fontsize=9, color="gray")
        ax.set_xlabel("Target QPS", fontsize=8)
        ax.set_ylabel("Avg Latency (ms)", fontsize=8)
        ax.grid(True, alpha=0.3)
        ax.tick_params(labelsize=7)

        # --- Col 2: Queries Sent vs Answers Received ---
        ax = axes[row_idx][2]
        all_sent_grid = []
        for tool in tools_here:
            style = _tool_style(tool, all_tools)
            x_vals = sorted(by_tool_qps[tool].keys())
            sent_mean, comp_mean = [], []
            for qps in x_vals:
                ms, _ = _trial_mean_std(by_tool_qps[tool][qps], "queries_sent")
                mc, _ = _trial_mean_std(by_tool_qps[tool][qps], "queries_completed")
                sent_mean.append(ms)
                comp_mean.append(mc)
            ax.plot(sent_mean, comp_mean, marker=style.get("marker"),
                    color=style.get("color"), linestyle=style.get("linestyle", "-"),
                    markersize=3, linewidth=1.2, label=tool)
            all_sent_grid.extend(sent_mean)
        if all_sent_grid:
            lo, hi = min(all_sent_grid), max(all_sent_grid)
            ax.plot([lo, hi], [lo, hi], "--", color="gray", alpha=0.5, linewidth=0.8, label="Ideal")
        ax.set_xlabel("Queries Sent", fontsize=8)
        ax.set_ylabel("Answers Received", fontsize=8)
        ax.grid(True, alpha=0.3)
        ax.tick_params(labelsize=7)
        ax.legend(fontsize=6, loc="upper left")

    fig.tight_layout()
    path = os.path.join(output_dir, "all_services_grid.pdf")
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def _generate_threshold_summary(results, output_dir):
    """Generate a text summary of the QPS at which each tool falls below 99.99% answer rate."""
    by_service_tool = defaultdict(lambda: defaultdict(list))
    for row in results:
        by_service_tool[row["dns_service"]][row["tool"]].append(row)

    lines = ["99.99% Answer Rate Threshold QPS", "=" * 60, ""]
    lines.append(f"{'DNS Service':<25} {'Tool':<30} {'Threshold QPS':>15}")
    lines.append("-" * 70)

    for service in sorted(by_service_tool.keys()):
        for tool in sorted(by_service_tool[service].keys()):
            rows = sorted(by_service_tool[service][tool],
                          key=lambda r: r["target_qps"])

            # Average answer rate by QPS
            by_qps = defaultdict(list)
            for r in rows:
                by_qps[r["target_qps"]].append(r["answer_rate_pct"])

            threshold_qps = "N/A"
            for qps in sorted(by_qps.keys()):
                avg_rate = sum(by_qps[qps]) / len(by_qps[qps])
                if avg_rate < 99.99:
                    threshold_qps = str(qps)
                    break

            lines.append(f"{service:<25} {tool:<30} {threshold_qps:>15}")

    summary = "\n".join(lines)
    path = os.path.join(output_dir, "threshold_summary.txt")
    with open(path, "w") as f:
        f.write(summary)
