import logging
import os
from collections import defaultdict

import math
import yaml

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Raise default font sizes for all charts (applies to any axis label, title,
# or tick that doesn't specify fontsize explicitly).
plt.rcParams.update({
    "font.size":         18,
    "font.weight":       "bold",
    "axes.titlesize":    18,
    "axes.titleweight":  "bold",
    "axes.labelsize":    18,
    "axes.labelweight":  "bold",
    "xtick.labelsize":   18,
    "ytick.labelsize":   18,
    "legend.fontsize":   18,
    "figure.titlesize":  18,
    "figure.titleweight": "bold",
})

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


LINE_STYLES = ["-", "--", "-.", ":", (0, (3, 1, 1, 1)), (0, (5, 2)), (0, (1, 1)), (0, (3, 5, 1, 5)), (0, (5, 1))]
MARKERS = ["o", "s", "^", "D", "v", "P", "X", "*", "h"]
# Palette chosen so that consecutive indices have very different luminance.
# Combined with the distinct LINE_STYLES and MARKERS above, lines remain
# distinguishable when the figure is printed or photocopied in greyscale.
COLORS = [
    "#000000",  # 0  black           (L~0)
    "#FFB000",  # 1  gold            (L~75)
    "#648FFF",  # 2  blue            (L~42)
    "#DC267F",  # 3  magenta         (L~30)
    "#FE6100",  # 4  orange          (L~55)
    "#785EF0",  # 5  purple          (L~30)
    "#009E73",  # 6  teal-green      (L~51)
    "#B0B0B0",  # 7  light grey      (L~68)
    "#6B3D00",  # 8  brown           (L~22)
    "#56B4E9",  # 9  sky blue        (L~65)
    "#F0E442",  # 10 yellow          (L~87)
    "#4D4D4D",  # 11 dark grey       (L~30)
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

    fig, ax = plt.subplots(figsize=(12, 12))

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
    ax.set_ylabel("Achieved QPS")
    #ax.set_title("Maximum Throughput: Requested vs Achieved QPS")
    ax.legend(loc="upper left")
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
        fig, ax = plt.subplots(figsize=(12, 12))
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
        ax.set_ylabel(f"Mean Achieved QPS")
        #ax.set_title(f"QPS Accuracy: Mean Achieved vs Target ({interval})")
        ax.legend(loc="upper left", fontsize=16)
        ax.grid(True, alpha=0.3)

        path = os.path.join(output_dir, f"accuracy_mean_{interval}.pdf")
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)

        # --- StdDev chart ---
        fig, ax = plt.subplots(figsize=(12, 10))
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
        ax.set_ylabel(f"QPS Standard Deviation")
        #ax.set_title(f"QPS Accuracy: Standard Deviation ({interval})")
        ax.legend(loc="upper left", fontsize=18)
        ax.grid(True, alpha=0.3)

        path = os.path.join(output_dir, f"accuracy_stddev_{interval}.pdf")
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)

        # --- Max Deviation chart ---
        fig, ax = plt.subplots(figsize=(12, 12))
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
        ax.set_ylabel(f"Max Deviation from Target")
        #ax.set_title(f"QPS Accuracy: Maximum Deviation ({interval})")
        ax.legend(loc="upper left", fontsize=14, frameon=False)
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
                ax.legend(loc="best", fontsize=9)
                ax.grid(True, alpha=0.3)

        fig.suptitle("QPS Accuracy: All Metrics and Intervals", fontsize=18, y=1.01)
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

        ax.set_xlabel(f"Expected Packet Count")
        ax.set_ylabel(f"Mean Packet Count")
        ax.set_title(f"Packet Count Accuracy: Mean Achieved vs Expected ({interval})")
        ax.legend(loc="upper left", fontsize=16)
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

        ax.set_xlabel(f"Expected Packet Count")
        ax.set_ylabel(f"Packet Count Standard Deviation")
        ax.set_title(f"Packet Count Accuracy: Standard Deviation ({interval})")
        ax.legend(loc="upper left", fontsize=16)
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

        ax.set_xlabel(f"Expected Packet Count")
        ax.set_ylabel(f"Max Packet Count Deviation from Expected")
        ax.set_title(f"Packet Count Accuracy: Maximum Deviation ({interval})")
        ax.legend(loc="upper left", fontsize=16)
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
                ax.legend(loc="best", fontsize=9)
                ax.grid(True, alpha=0.3)

        fig.suptitle("PPS Accuracy: All Metrics and Intervals", fontsize=18, y=1.01)
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
        ax.legend(loc="lower left", fontsize=11)
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
            ax.legend(loc="upper left", fontsize=16)
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
        ax.legend(loc="upper left", fontsize=10)
        ax.grid(True, alpha=0.3)

        path = os.path.join(output_dir, f"{dns_service}_qps_comparison.pdf")
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)

    # --- Combined Grid: all DNS services x (answer_rate, latency, qps_comparison) ---
    _plot_load_impact_grid(results, all_tools, output_dir)

    # --- Combined 1x3: all DNS services overlaid on each of the three metrics ---
    _plot_load_impact_combined(results, output_dir)

    # --- Per-service collectl resource charts (individual + 1x3 combined) ---
    _plot_load_impact_resources(results, all_tools, output_dir)

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
    #fig.suptitle("Load Generator Impact — All DNS Services", fontsize=18, fontweight="bold", y=1.01)

    col_titles = ["Answer Rate (%)", "Average Latency (ms)", "Queries Sent vs Answers Received"]
    for col, title in enumerate(col_titles):
        axes[0][col].set_title(title, fontsize=14, fontweight="bold")

    for row_idx, dns_service in enumerate(dns_services):
        by_tool_qps = service_data[dns_service]
        tools_here = sorted(by_tool_qps.keys())

        # Row label on the leftmost axis
        axes[row_idx][0].set_ylabel(f"{dns_service}\nAnswer Rate (%)", fontsize=14)

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
        ax.set_xlabel("Target QPS", fontsize=14)
        ax.set_ylim(bottom=max(0, ax.get_ylim()[0]), top=101)
        ax.grid(True, alpha=0.3)
        ax.tick_params(labelsize=14)
        ax.xaxis.get_offset_text().set_fontsize(14)
        ax.yaxis.get_offset_text().set_fontsize(14)

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
        else:
            ax.text(0.5, 0.5, "No latency data", ha="center", va="center",
                    transform=ax.transAxes, fontsize=14, color="gray")
        ax.set_xlabel("Target QPS", fontsize=14)
        ax.set_ylabel("Avg Latency (ms)", fontsize=14)
        ax.grid(True, alpha=0.3)
        ax.tick_params(labelsize=14)
        ax.xaxis.get_offset_text().set_fontsize(14)
        ax.yaxis.get_offset_text().set_fontsize(14)

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
        ax.set_xlabel("Queries Sent", fontsize=14)
        ax.set_ylabel("Answers Received", fontsize=14)
        ax.grid(True, alpha=0.3)
        ax.tick_params(labelsize=14)
        ax.xaxis.get_offset_text().set_fontsize(14)
        ax.yaxis.get_offset_text().set_fontsize(14)

    # Build a single shared legend from all unique (handle, label) pairs
    # across every subplot, so it doesn't overlap any chart.
    seen = {}
    for ax_row in axes:
        for ax in ax_row:
            for handle, label in zip(*ax.get_legend_handles_labels()):
                if label not in seen:
                    seen[label] = handle
    if seen:
        ncol = min(len(seen), 5)
        fig.legend(
            seen.values(), seen.keys(),
            loc="lower center",
            bbox_to_anchor=(0.5, -0.02),
            ncol=ncol,
            fontsize=13,
            frameon=False,
        )

    fig.tight_layout(rect=[0, 0.04, 1, 1])
    path = os.path.join(output_dir, "all_services_grid.pdf")
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def _plot_load_impact_combined(results, output_dir):
    """Combined 1x3 chart: all DNS services overlaid per metric.

    Panels: Answer Rate, Avg Latency, Queries Sent vs Answers Received.
    Designed for at most 2 tools and 5 DNS services. The palette is
    greyscale-readable: tool determines color (two luminance-distinct
    choices), DNS service determines linestyle and marker.
    """
    dns_services = sorted(set(row["dns_service"] for row in results))
    if not dns_services:
        return
    tools_present = sorted(set(row["tool"] for row in results))

    # Palette (sized for <=5 services, <=2 tools):
    #   - service colors: luminance-distinct hues so each DNS service is its
    #     own color (still readable when printed in greyscale thanks to the
    #     differing linestyles/markers below)
    #   - tool linestyles/markers: distinguish tools within the same service
    COMBINED_SERVICE_COLORS = ["#000000", "#FE6100", "#648FFF", "#DC267F", "#009E73"]
    COMBINED_TOOL_MARKERS = ["o", "s", "^", "D", "v"]
    COMBINED_TOOL_LINESTYLES = ["-", "--", "-.", ":", (0, (3, 1, 1, 1))]

    svc_color = {s: COMBINED_SERVICE_COLORS[i % len(COMBINED_SERVICE_COLORS)]
                 for i, s in enumerate(dns_services)}
    tool_marker = {t: COMBINED_TOOL_MARKERS[i % len(COMBINED_TOOL_MARKERS)]
                   for i, t in enumerate(tools_present)}
    tool_linestyle = {t: COMBINED_TOOL_LINESTYLES[i % len(COMBINED_TOOL_LINESTYLES)]
                      for i, t in enumerate(tools_present)}

    def _clip_lower(means, errs):
        """Asymmetric error bars whose lower whisker never crosses zero."""
        lower = [max(0.0, min(e, m if m is not None else e)) for e, m in zip(errs, means)]
        return [lower, list(errs)]

    # Group once: tool -> dns_service -> target_qps -> [rows]
    by_tool_svc_qps = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for row in results:
        by_tool_svc_qps[row["tool"]][row["dns_service"]][row["target_qps"]].append(row)

    fig, axes = plt.subplots(1, 3, figsize=(18, 6), squeeze=False)
    ax_rate, ax_lat, ax_qc = axes[0][0], axes[0][1], axes[0][2]

    all_sent_vals = []
    for tool in tools_present:
        for svc in dns_services:
            if svc not in by_tool_svc_qps[tool]:
                continue
            svc_qps = by_tool_svc_qps[tool][svc]
            label = f"{tool} / {svc}"
            plot_kwargs = dict(
                color=svc_color[svc],
                marker=tool_marker[tool],
                linestyle=tool_linestyle[tool],
            )

            x_vals = sorted(svc_qps.keys())

            # Answer Rate
            y_mean, y_err = [], []
            for qps in x_vals:
                mean, std = _trial_mean_std(svc_qps[qps], "answer_rate_pct")
                y_mean.append(mean)
                y_err.append(std)
            ax_rate.errorbar(x_vals, y_mean, yerr=_clip_lower(y_mean, y_err),
                             markersize=3, capsize=2, linewidth=1.2,
                             label=label, **plot_kwargs)

            # Latency (ms) — only if tool reports it
            has_latency = any(
                r.get("avg_latency_s") is not None
                for rows in svc_qps.values() for r in rows
            )
            if has_latency:
                y_mean, y_err = [], []
                for qps in x_vals:
                    lats = [r["avg_latency_s"] * 1000 for r in svc_qps[qps]
                            if r.get("avg_latency_s") is not None]
                    n = len(lats)
                    mean = sum(lats) / n if n else 0
                    std = math.sqrt(sum((v - mean) ** 2 for v in lats) / (n - 1)) if n > 1 else 0.0
                    y_mean.append(mean)
                    y_err.append(std)
                ax_lat.errorbar(x_vals, y_mean, yerr=_clip_lower(y_mean, y_err),
                                markersize=3, capsize=2, linewidth=1.2,
                                label=label, **plot_kwargs)

            # Queries Sent vs Answers Received (both axes clipped at zero)
            sent_mean, sent_err, comp_mean, comp_err = [], [], [], []
            for qps in x_vals:
                ms, ss = _trial_mean_std(svc_qps[qps], "queries_sent")
                mc, sc = _trial_mean_std(svc_qps[qps], "queries_completed")
                sent_mean.append(ms)
                sent_err.append(ss)
                comp_mean.append(mc)
                comp_err.append(sc)
            ax_qc.errorbar(sent_mean, comp_mean,
                           xerr=_clip_lower(sent_mean, sent_err),
                           yerr=_clip_lower(comp_mean, comp_err),
                           markersize=3, capsize=2, linewidth=1.2,
                           label=label, **plot_kwargs)
            all_sent_vals.extend(sent_mean)

    ax_rate.axhline(y=99.99, color="red", linestyle="--", alpha=0.5, linewidth=0.8,
                    label="99.99% threshold")
    ax_rate.set_xlabel("Target QPS", fontsize=14)
    ax_rate.set_ylabel("Answer Rate (%)", fontsize=14)
    ax_rate.set_title("Answer Rate", fontsize=14, fontweight="bold")
    ax_rate.set_ylim(bottom=max(0, ax_rate.get_ylim()[0]), top=101)
    ax_rate.grid(True, alpha=0.3)
    ax_rate.tick_params(labelsize=14)

    ax_lat.set_xlabel("Target QPS", fontsize=14)
    ax_lat.set_ylabel("Avg Latency (ms)", fontsize=14)
    ax_lat.set_title("Average Latency", fontsize=14, fontweight="bold")
    ax_lat.grid(True, alpha=0.3)
    ax_lat.tick_params(labelsize=14)
    if not ax_lat.has_data():
        ax_lat.text(0.5, 0.5, "No latency data", ha="center", va="center",
                    transform=ax_lat.transAxes, fontsize=14, color="gray")

    if all_sent_vals:
        lo, hi = min(all_sent_vals), max(all_sent_vals)
        ax_qc.plot([lo, hi], [lo, hi], "--", color="gray", alpha=0.5,
                   linewidth=0.8, label="Ideal")
    ax_qc.set_xlabel("Queries Sent", fontsize=14)
    ax_qc.set_ylabel("Answers Received", fontsize=14)
    ax_qc.set_title("Queries Sent vs Answers Received", fontsize=14, fontweight="bold")
    ax_qc.grid(True, alpha=0.3)
    ax_qc.tick_params(labelsize=14)

    # Shared legend below the grid
    seen = {}
    for ax in (ax_rate, ax_lat, ax_qc):
        for handle, label in zip(*ax.get_legend_handles_labels()):
            if label not in seen:
                seen[label] = handle
    if seen:
        ncol = min(len(seen), 4)
        fig.legend(
            seen.values(), seen.keys(),
            loc="lower center",
            bbox_to_anchor=(0.5, -0.02),
            ncol=ncol,
            fontsize=12,
            frameon=False,
        )

    fig.tight_layout(rect=[0, 0.08, 1, 1])
    path = os.path.join(output_dir, "all_services_combined.pdf")
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


# metric_id -> (median_csv_key, peak_csv_key, ylabel, category)
COLLECTL_METRICS = [
    ("cpu_totl",   "cpu_totl_median_pct",  "cpu_totl_peak_pct",  "CPU Total %",     "cpu"),
    ("cpu_user",   "cpu_user_median_pct",  "cpu_user_peak_pct",  "CPU User %",      "cpu"),
    ("cpu_sys",    "cpu_sys_median_pct",   "cpu_sys_peak_pct",   "CPU Sys %",       "cpu"),
    ("mem_used",   "mem_used_median_mb",   "mem_used_peak_mb",   "Mem Used (MB)",   "mem"),
    ("mem_tot",    "mem_tot_median_mb",    "mem_tot_peak_mb",    "Mem Total (MB)",  "mem"),
    ("mem_free",   "mem_free_median_mb",   "mem_free_peak_mb",   "Mem Free (MB)",   "mem"),
    ("mem_cached", "mem_cached_median_mb", "mem_cached_peak_mb", "Mem Cached (MB)", "mem"),
    ("net_kb",     "net_kb_median_kbps",   "net_kb_peak_kbps",   "Net KB/s",        "net"),
    ("net_rx_pkt", "net_rx_pkt_median",    "net_rx_pkt_peak",    "Net Rx Pkt/s",    "net"),
    ("net_tx_pkt", "net_tx_pkt_median",    "net_tx_pkt_peak",    "Net Tx Pkt/s",    "net"),
]

# Which metrics appear in each combined 1x3 grid (Tot excluded — near-constant).
COLLECTL_COMBINED = {
    "cpu": ["cpu_user", "cpu_sys", "cpu_totl"],
    "mem": ["mem_used", "mem_free", "mem_cached"],
    "net": ["net_kb", "net_rx_pkt", "net_tx_pkt"],
}


def _plot_resource_panel(ax, by_tool_qps, median_key, peak_key, all_tools, ylabel):
    """Draw one resource-metric panel: median + peak lines per tool vs QPS."""
    for tool in sorted(by_tool_qps.keys()):
        style = _tool_style(tool, all_tools)
        x_vals = sorted(by_tool_qps[tool].keys())

        med_x, med_mean, med_err = [], [], []
        peak_x, peak_mean, peak_err = [], [], []
        for qps in x_vals:
            rows = by_tool_qps[tool][qps]
            med_vals = [r[median_key] for r in rows if r.get(median_key) is not None]
            peak_vals = [r[peak_key] for r in rows if r.get(peak_key) is not None]
            if med_vals:
                n = len(med_vals)
                m = sum(med_vals) / n
                s = math.sqrt(sum((v - m) ** 2 for v in med_vals) / (n - 1)) if n > 1 else 0.0
                med_x.append(qps)
                med_mean.append(m)
                med_err.append(s)
            if peak_vals:
                n = len(peak_vals)
                m = sum(peak_vals) / n
                s = math.sqrt(sum((v - m) ** 2 for v in peak_vals) / (n - 1)) if n > 1 else 0.0
                peak_x.append(qps)
                peak_mean.append(m)
                peak_err.append(s)

        if med_x:
            ax.errorbar(
                med_x, med_mean, yerr=med_err, markersize=4, capsize=3, linewidth=1.5,
                color=style["color"], marker=style["marker"], linestyle="-",
                label=f"{tool} (median)",
            )
        if peak_x:
            ax.errorbar(
                peak_x, peak_mean, yerr=peak_err, markersize=4, capsize=3, linewidth=1.5,
                color=style["color"], marker=style["marker"], linestyle="--",
                label=f"{tool} (peak)",
            )

    ax.set_xlabel("Target QPS")
    ax.set_ylabel(ylabel)
    ax.grid(True, alpha=0.3)


def _plot_load_impact_resources(results, all_tools, output_dir):
    """Plot collectl resource charts per DNS service: individual per metric + 1x3 combined grids per category."""
    os.makedirs(output_dir, exist_ok=True)
    dns_services = sorted(set(row["dns_service"] for row in results))
    if not dns_services:
        return

    for dns_service in dns_services:
        service_results = [r for r in results if r["dns_service"] == dns_service]

        # Group once: tool -> target_qps -> list of rows
        by_tool_qps = defaultdict(lambda: defaultdict(list))
        for row in service_results:
            by_tool_qps[row["tool"]][row["target_qps"]].append(row)

        # --- Individual per-metric charts ---
        for metric_id, median_key, peak_key, ylabel, _cat in COLLECTL_METRICS:
            has_data = any(
                r.get(median_key) is not None or r.get(peak_key) is not None
                for r in service_results
            )
            if not has_data:
                continue

            fig, ax = plt.subplots(figsize=(12, 7))
            _plot_resource_panel(ax, by_tool_qps, median_key, peak_key, all_tools, ylabel)
            ax.legend(loc="best", fontsize=11)
            path = os.path.join(output_dir, f"{dns_service}_{metric_id}.pdf")
            fig.savefig(path, dpi=150, bbox_inches="tight")
            plt.close(fig)

        # --- Combined 1x3 grids per category ---
        metric_by_id = {m[0]: m for m in COLLECTL_METRICS}
        for category, metric_ids in COLLECTL_COMBINED.items():
            has_any = False
            for mid in metric_ids:
                _mid, med_k, peak_k, _yl, _c = metric_by_id[mid]
                if any(r.get(med_k) is not None or r.get(peak_k) is not None
                       for r in service_results):
                    has_any = True
                    break
            if not has_any:
                continue

            fig, axes = plt.subplots(1, 3, figsize=(21, 7), squeeze=False)
            for col, mid in enumerate(metric_ids):
                _mid, med_k, peak_k, yl, _c = metric_by_id[mid]
                ax = axes[0][col]
                _plot_resource_panel(ax, by_tool_qps, med_k, peak_k, all_tools, yl)
                ax.set_title(yl, fontsize=14, fontweight="bold")

            # Shared legend below the figure (dedup by label)
            seen = {}
            for ax in axes[0]:
                for handle, label in zip(*ax.get_legend_handles_labels()):
                    if label not in seen:
                        seen[label] = handle
            if seen:
                ncol = min(len(seen), 5)
                fig.legend(
                    seen.values(), seen.keys(),
                    loc="lower center",
                    bbox_to_anchor=(0.5, -0.02),
                    ncol=ncol,
                    fontsize=12,
                    frameon=False,
                )

            fig.tight_layout(rect=[0, 0.06, 1, 1])
            path = os.path.join(output_dir, f"{dns_service}_{category}_combined.pdf")
            fig.savefig(path, dpi=150, bbox_inches="tight")
            plt.close(fig)
