import os
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def plot_max_throughput(results, output_dir):
    """Plot requested vs achieved QPS per tool (Script 1).

    Args:
        results: list of dicts with keys: tool, requested_qps, achieved_qps_responder
        output_dir: directory to save charts
    """
    os.makedirs(output_dir, exist_ok=True)

    # Group by tool
    by_tool = defaultdict(lambda: ([], []))
    for row in results:
        tool = row["tool"]
        by_tool[tool][0].append(row["requested_qps"])
        by_tool[tool][1].append(row["achieved_qps_responder"])

    LINE_STYLES = ["-", "--", "-.", ":", (0, (3, 1, 1, 1)), (0, (5, 2)), (0, (1, 1)), (0, (3, 5, 1, 5))]
    MARKERS = ["o", "s", "^", "D", "v", "P", "X", "*"]

    fig, ax = plt.subplots(figsize=(12, 7))

    for i, (tool, (x, y)) in enumerate(sorted(by_tool.items())):
        ax.plot(x, y, marker=MARKERS[i % len(MARKERS)], markersize=4,
                linestyle=LINE_STYLES[i % len(LINE_STYLES)], linewidth=1.5, label=tool)

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

    path = os.path.join(output_dir, "requested_vs_achieved.png")
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

    for interval in sorted(intervals):
        # Filter to this interval, average across trials
        by_tool_qps = defaultdict(lambda: defaultdict(list))
        for row in results:
            if row["interval"] != interval:
                continue
            by_tool_qps[row["tool"]][row["target_qps"]].append(row)

        # --- Mean QPS chart ---
        fig, ax = plt.subplots(figsize=(12, 7))
        for tool in sorted(by_tool_qps.keys()):
            x_vals = sorted(by_tool_qps[tool].keys())
            y_mean = []
            for qps in x_vals:
                rows = by_tool_qps[tool][qps]
                avg = sum(r["mean_qps"] for r in rows) / len(rows)
                y_mean.append(avg)
            ax.plot(x_vals, y_mean, marker="o", markersize=3, label=tool)

        if x_vals:
            ax.plot([min(x_vals), max(x_vals)], [min(x_vals), max(x_vals)],
                    "--", color="gray", alpha=0.5, label="Ideal")

        ax.set_xlabel("Target QPS")
        ax.set_ylabel(f"Mean Achieved QPS ({interval} intervals)")
        ax.set_title(f"QPS Accuracy: Mean Achieved vs Target ({interval})")
        ax.legend(loc="upper left", fontsize=8)
        ax.grid(True, alpha=0.3)

        path = os.path.join(output_dir, f"accuracy_mean_{interval}.png")
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)

        # --- StdDev chart ---
        fig, ax = plt.subplots(figsize=(12, 7))
        for tool in sorted(by_tool_qps.keys()):
            x_vals = sorted(by_tool_qps[tool].keys())
            y_std = []
            for qps in x_vals:
                rows = by_tool_qps[tool][qps]
                avg = sum(r["stddev"] for r in rows) / len(rows)
                y_std.append(avg)
            ax.plot(x_vals, y_std, marker="o", markersize=3, label=tool)

        ax.set_xlabel("Target QPS")
        ax.set_ylabel(f"QPS Standard Deviation ({interval} intervals)")
        ax.set_title(f"QPS Accuracy: Standard Deviation ({interval})")
        ax.legend(loc="upper left", fontsize=8)
        ax.grid(True, alpha=0.3)

        path = os.path.join(output_dir, f"accuracy_stddev_{interval}.png")
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)

        # --- Max Deviation chart ---
        fig, ax = plt.subplots(figsize=(12, 7))
        for tool in sorted(by_tool_qps.keys()):
            x_vals = sorted(by_tool_qps[tool].keys())
            y_maxdev = []
            for qps in x_vals:
                rows = by_tool_qps[tool][qps]
                avg = sum(r["max_deviation"] for r in rows) / len(rows)
                y_maxdev.append(avg)
            ax.plot(x_vals, y_maxdev, marker="o", markersize=3, label=tool)

        ax.set_xlabel("Target QPS")
        ax.set_ylabel(f"Max Deviation from Target ({interval} intervals)")
        ax.set_title(f"QPS Accuracy: Maximum Deviation ({interval})")
        ax.legend(loc="upper left", fontsize=8)
        ax.grid(True, alpha=0.3)

        path = os.path.join(output_dir, f"accuracy_maxdev_{interval}.png")
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

    for dns_service in dns_services:
        service_results = [r for r in results if r["dns_service"] == dns_service]

        # Average across trials
        by_tool_qps = defaultdict(lambda: defaultdict(list))
        for row in service_results:
            by_tool_qps[row["tool"]][row["target_qps"]].append(row)

        # --- Answer Rate vs QPS ---
        fig, ax = plt.subplots(figsize=(12, 7))
        for tool in sorted(by_tool_qps.keys()):
            x_vals = sorted(by_tool_qps[tool].keys())
            y_vals = []
            for qps in x_vals:
                rows = by_tool_qps[tool][qps]
                avg = sum(r["answer_rate_pct"] for r in rows) / len(rows)
                y_vals.append(avg)
            ax.plot(x_vals, y_vals, marker="o", markersize=3, label=tool)

        ax.axhline(y=99.99, color="red", linestyle="--", alpha=0.5, label="99.99% threshold")
        ax.set_xlabel("Target QPS")
        ax.set_ylabel("Answer Rate (%)")
        ax.set_title(f"Answer Rate vs QPS — {dns_service}")
        ax.legend(loc="lower left", fontsize=8)
        ax.grid(True, alpha=0.3)
        ax.set_ylim(bottom=max(0, ax.get_ylim()[0]), top=101)

        path = os.path.join(output_dir, f"{dns_service}_answer_rate.png")
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
                x_vals = sorted(by_tool_qps[tool].keys())
                y_vals = []
                for qps in x_vals:
                    rows = by_tool_qps[tool][qps]
                    lats = [r["avg_latency_s"] for r in rows
                            if r.get("avg_latency_s") is not None]
                    avg = sum(lats) / len(lats) if lats else 0
                    y_vals.append(avg * 1000)  # Convert to ms
                ax.plot(x_vals, y_vals, marker="o", markersize=3, label=tool)

            ax.set_xlabel("Target QPS")
            ax.set_ylabel("Average Latency (ms)")
            ax.set_title(f"Average Latency vs QPS — {dns_service}")
            ax.legend(loc="upper left", fontsize=8)
            ax.grid(True, alpha=0.3)

            path = os.path.join(output_dir, f"{dns_service}_latency.png")
            fig.savefig(path, dpi=150, bbox_inches="tight")
            plt.close(fig)

        # --- Queries Sent vs Answers Received ---
        fig, axes = plt.subplots(1, 2, figsize=(16, 7))

        for tool in sorted(by_tool_qps.keys()):
            x_vals = sorted(by_tool_qps[tool].keys())
            sent = []
            completed = []
            for qps in x_vals:
                rows = by_tool_qps[tool][qps]
                avg_sent = sum(r["queries_sent"] for r in rows) / len(rows)
                avg_comp = sum(r["queries_completed"] for r in rows) / len(rows)
                sent.append(avg_sent)
                completed.append(avg_comp)
            axes[0].plot(x_vals, sent, marker="o", markersize=3, label=tool)
            axes[1].plot(x_vals, completed, marker="o", markersize=3, label=tool)

        axes[0].set_xlabel("Target QPS")
        axes[0].set_ylabel("Queries Sent")
        axes[0].set_title(f"Queries Sent vs Target QPS — {dns_service}")
        axes[0].legend(loc="upper left", fontsize=7)
        axes[0].grid(True, alpha=0.3)

        axes[1].set_xlabel("Target QPS")
        axes[1].set_ylabel("Answers Received")
        axes[1].set_title(f"Answers Received vs Target QPS — {dns_service}")
        axes[1].legend(loc="upper left", fontsize=7)
        axes[1].grid(True, alpha=0.3)

        path = os.path.join(output_dir, f"{dns_service}_qps_comparison.png")
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)

    # --- 99.99% Threshold Summary Table ---
    _generate_threshold_summary(results, output_dir)


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
