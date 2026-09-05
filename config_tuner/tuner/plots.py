"""Figures comparing optimization runs.

The headline is the anytime-performance curve: best score so far against
evaluations spent. That axis is the whole research claim -- both optimizers pay
the same ~25 minutes per evaluation, so "fewer evaluations to the same QPS" is
the thing worth measuring, and everything else is supporting evidence.

Three conventions carried through every figure:

* Cache hits are marked but do not advance the x axis. They cost no testbed
  time, so counting them as evaluations would flatter whichever optimizer
  happened to repeat itself more.
* Censored (ceiling-limited) and generator-limited results are drawn distinctly
  and never contribute to a best-so-far line. They are lower bounds and
  measurements of the load generator respectively.
* The noise band is shaded around the baseline. A curve that rises inside that
  band has not demonstrated anything, and the figure should make that obvious
  rather than leaving it to the caption.
"""

import csv
import logging
import os

log = logging.getLogger(__name__)

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    HAVE_MPL = True
except ImportError:  # pragma: no cover
    HAVE_MPL = False

# Deliberately distinguishable in greyscale as well as colour, since these end
# up in a paper.
STYLES = {
    "grid": {"color": "#4C72B0", "marker": "o", "linestyle": "-", "label": "grid search"},
    "agent": {"color": "#C44E52", "marker": "s", "linestyle": "--", "label": "agentic search"},
}
STATUS_MARKERS = {
    "ok": "o",
    "apply_failed": "x",
    "no_passing_level": "v",
    "timeout": "*",
    "infra_error": "P",
}


def _style(optimizer, index=0):
    if optimizer in STYLES:
        return dict(STYLES[optimizer])
    palette = ["#55A868", "#8172B2", "#CCB974", "#64B5CD"]
    return {"color": palette[index % len(palette)], "marker": "^",
            "linestyle": ":", "label": optimizer}


def load_runs(run_dirs):
    """Read ledger.csv from each run directory into a list of row dicts."""
    runs = []
    for run_dir in run_dirs:
        path = os.path.join(os.path.expanduser(run_dir), "ledger.csv")
        if not os.path.exists(path):
            log.warning("No ledger.csv in %s -- skipping", run_dir)
            continue
        with open(path) as f:
            rows = list(csv.DictReader(f))
        if not rows:
            continue
        for row in rows:
            for key in ("max_qps_passed", "best_so_far", "eval_seconds",
                        "elapsed_s", "qps_fidelity_pct"):
                row[key] = float(row[key]) if row.get(key) not in (None, "", "None") else None
            for key in ("cached", "changes_semantics", "hit_max_qps_ceiling"):
                row[key] = str(row.get(key, "")).lower() in ("true", "1")
        runs.append({
            "run_id": rows[0].get("run_id", os.path.basename(run_dir)),
            "optimizer": rows[0].get("optimizer", "unknown"),
            "rows": rows,
            "dir": run_dir,
        })
    return runs


def _anytime_series(rows):
    """(evaluations spent, best-so-far) with cache hits not advancing x."""
    spent, best = 0, None
    xs, ys = [], []
    for row in rows:
        if not row["cached"]:
            spent += 1
        score = row["max_qps_passed"]
        if (row.get("status") == "ok" and score is not None
                and not row["hit_max_qps_ceiling"]):
            best = score if best is None else max(best, score)
        if best is not None:
            xs.append(spent)
            ys.append(best)
    return xs, ys


def _baseline_of(run):
    for row in run["rows"]:
        if row.get("status") == "ok" and row["max_qps_passed"] is not None:
            return row["max_qps_passed"]
    return None


def plot_anytime(runs, out_dir, noise_floor=None, x_axis="evals"):
    """The headline figure: best-so-far against cost."""
    if not HAVE_MPL:
        return None
    fig, ax = plt.subplots(figsize=(7.2, 4.4))

    baseline = next((_baseline_of(r) for r in runs if _baseline_of(r)), None)
    for i, run in enumerate(runs):
        style = _style(run["optimizer"], i)
        if x_axis == "evals":
            xs, ys = _anytime_series(run["rows"])
            xlabel = "evaluations spent (~25 min each)"
        else:
            xs, ys = [], []
            best = None
            for row in run["rows"]:
                score = row["max_qps_passed"]
                if (row.get("status") == "ok" and score is not None
                        and not row["hit_max_qps_ceiling"]):
                    best = score if best is None else max(best, score)
                if best is not None and row["elapsed_s"] is not None:
                    xs.append(row["elapsed_s"] / 60.0)
                    ys.append(best)
            xlabel = "wall clock (minutes)"
        if not xs:
            continue
        ax.step(xs, ys, where="post", linewidth=2, **style)
        # Mark the cache hits: free measurements that do not advance the x axis.
        cached_x = [x for x, row in zip(xs, [r for r in run["rows"]]) if row["cached"]]
        if cached_x:
            ax.plot(cached_x, [ys[xs.index(x)] for x in cached_x], "|",
                    color=style["color"], markersize=10, alpha=0.6)

    if baseline:
        ax.axhline(baseline, color="#555555", linestyle=":", linewidth=1.2,
                   label=f"baseline ({baseline:,.0f} QPS)")
        if noise_floor:
            # A curve that rises only inside this band has shown nothing.
            ax.axhspan(baseline - noise_floor, baseline + noise_floor,
                       color="#888888", alpha=0.15,
                       label=f"noise floor (±{noise_floor:,.0f})")

    ax.set_xlabel(xlabel)
    ax.set_ylabel("best max sustainable QPS found")
    ax.set_title("Anytime performance: how much tuning per evaluation spent")
    ax.grid(alpha=0.3)
    ax.legend(loc="lower right", fontsize=9)
    fig.tight_layout()

    name = "anytime_by_evals.pdf" if x_axis == "evals" else "anytime_by_walltime.pdf"
    return _save(fig, out_dir, name)


def plot_scatter(runs, out_dir):
    """Every evaluation, so exploration behaviour and failures are visible."""
    if not HAVE_MPL:
        return None
    fig, ax = plt.subplots(figsize=(7.2, 4.4))
    for i, run in enumerate(runs):
        style = _style(run["optimizer"], i)
        for j, row in enumerate(run["rows"]):
            score = row["max_qps_passed"]
            marker = STATUS_MARKERS.get(row.get("status"), "d")
            if score is None:
                continue
            ax.scatter(j, score, marker=marker, color=style["color"],
                       alpha=0.45 if row["changes_semantics"] else 0.9,
                       edgecolors="none", s=48)
        ax.scatter([], [], marker=style["marker"], color=style["color"],
                   label=style["label"])
    ax.scatter([], [], marker="o", color="#999999", alpha=0.45,
               label="lighter = changes protocol semantics")
    ax.set_xlabel("evaluation index")
    ax.set_ylabel("max sustainable QPS")
    ax.set_title("Every evaluation, including the ones that failed")
    ax.grid(alpha=0.3)
    ax.legend(loc="lower right", fontsize=8)
    fig.tight_layout()
    return _save(fig, out_dir, "all_evaluations.pdf")


def plot_parameter_effects(runs, out_dir, noise_floor=None):
    """QPS by value, per parameter, pooling every run.

    Scientifically the most interesting figure: not which optimizer won, but
    which knobs actually mattered on this hardware.
    """
    if not HAVE_MPL:
        return None
    pooled = [row for run in runs for row in run["rows"]
              if row.get("status") == "ok" and row["max_qps_passed"] is not None]
    if not pooled:
        return None

    params = sorted({k[len("param_"):] for row in pooled for k in row
                     if k.startswith("param_")})
    varying = [p for p in params
               if len({row.get(f"param_{p}") for row in pooled}) > 1]
    if not varying:
        return None

    cols = min(3, len(varying))
    rows_n = (len(varying) + cols - 1) // cols
    fig, axes = plt.subplots(rows_n, cols, figsize=(4.2 * cols, 3.2 * rows_n),
                             squeeze=False)

    for idx, name in enumerate(varying):
        ax = axes[idx // cols][idx % cols]
        groups = {}
        for row in pooled:
            groups.setdefault(row.get(f"param_{name}"), []).append(row["max_qps_passed"])
        keys = sorted(groups, key=lambda k: (len(str(k)), str(k)))
        ax.boxplot([groups[k] for k in keys], labels=[str(k) for k in keys],
                   widths=0.55)
        for i, key in enumerate(keys, start=1):
            ax.scatter([i] * len(groups[key]), groups[key], s=14,
                       color="#4C72B0", alpha=0.5, zorder=3)
        ax.set_title(name, fontsize=10)
        ax.grid(alpha=0.25, axis="y")
        ax.tick_params(labelsize=8)

    for idx in range(len(varying), rows_n * cols):
        axes[idx // cols][idx % cols].axis("off")

    fig.suptitle("What actually mattered: QPS by parameter value (all runs pooled)")
    fig.tight_layout()
    return _save(fig, out_dir, "parameter_effects.pdf")


def _save(fig, out_dir, filename):
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, filename)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    log.info("wrote %s", path)
    return path


def plot_all(runs, out_dir, noise_floor=None):
    if not HAVE_MPL:
        log.warning("matplotlib is not installed; skipping figures "
                    "(pip install -r config_tuner/requirements.txt)")
        return []
    made = [
        plot_anytime(runs, out_dir, noise_floor, "evals"),
        plot_anytime(runs, out_dir, noise_floor, "walltime"),
        plot_scatter(runs, out_dir),
        plot_parameter_effects(runs, out_dir, noise_floor),
    ]
    return [p for p in made if p]
