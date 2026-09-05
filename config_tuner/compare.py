#!/usr/bin/env python3
"""Put optimization runs side by side on identical axes.

    python3 config_tuner/compare.py ~/tuner_runs/grid-* ~/tuner_runs/agent-* \\
        --out-dir ~/tuner_runs/comparison --noise-floor 12000

Writes ``comparison_summary.csv`` (one tidy row per evaluation, every run
concatenated) and the figures from ``tuner/plots.py``.

The headline number is **evaluations to reach 95% of the best known score**,
not the best score itself. Both optimizers pay the same ~25 minutes per
evaluation, so that is the axis on which one can actually beat the other; a
grid given unlimited evaluations will eventually match any searcher.

Two cautions this script enforces rather than leaves to the reader:

* A ceiling-limited result is a lower bound and a generator-limited result
  measures the load generator, so neither is ever reported as a best score.
* Differences below the noise floor are reported as ties. Pass ``--noise-floor``
  with the standard deviation measured from repeated baseline evaluations; with
  no value, the measurement quantum is used and the caveat is printed.
"""

import argparse
import csv
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tuner import plots  # noqa: E402

log = logging.getLogger("compare")


def build_parser():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("run_dirs", nargs="+", help="run directories to compare")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--noise-floor", type=float,
                   help="1 sigma of repeated baseline evaluations, in QPS")
    p.add_argument("--quantum", type=float, default=10000,
                   help="measurement resolution, used when no noise floor is given")
    p.add_argument("--reach-fraction", type=float, default=0.95,
                   help="fraction of the best known score to measure 'evaluations to reach'")
    return p


def summarize(run, best_known, reach_fraction, floor):
    rows = run["rows"]
    spent = 0
    best = None
    evals_to_reach = None
    target = best_known * reach_fraction if best_known else None
    failures = 0
    cached = 0
    semantic_best = False

    for row in rows:
        if row["cached"]:
            cached += 1
        else:
            spent += 1
        if row.get("status") in ("apply_failed", "infra_error", "timeout"):
            failures += 1
        score = row["max_qps_passed"]
        usable = (row.get("status") == "ok" and score is not None
                  and not row["hit_max_qps_ceiling"])
        if usable and (best is None or score > best):
            best = score
            semantic_best = row["changes_semantics"]
        if target and best is not None and best >= target and evals_to_reach is None:
            evals_to_reach = spent

    baseline = plots._baseline_of(run)
    wall = max((r["elapsed_s"] or 0) for r in rows) if rows else 0
    return {
        "run_id": run["run_id"],
        "optimizer": run["optimizer"],
        "evaluations_spent": spent,
        "cache_hits": cached,
        "failures": failures,
        "baseline_qps": baseline,
        "best_qps": best,
        "uplift_qps": (best - baseline) if (best and baseline) else None,
        "uplift_pct": round(100.0 * (best - baseline) / baseline, 2)
                      if (best and baseline) else None,
        "uplift_is_within_noise": (abs(best - baseline) < floor)
                                  if (best and baseline) else None,
        "best_changes_semantics": semantic_best,
        f"evals_to_{int(reach_fraction * 100)}pct": evals_to_reach,
        "wall_clock_min": round(wall / 60.0, 1) if wall else None,
    }


def main(argv=None):
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    runs = plots.load_runs(args.run_dirs)
    if not runs:
        raise SystemExit("no readable runs -- each directory needs a ledger.csv")

    out_dir = os.path.abspath(os.path.expanduser(args.out_dir))
    os.makedirs(out_dir, exist_ok=True)
    floor = args.noise_floor or args.quantum

    # Best known across every run, excluding censored results.
    best_known = None
    for run in runs:
        for row in run["rows"]:
            score = row["max_qps_passed"]
            if (row.get("status") == "ok" and score is not None
                    and not row["hit_max_qps_ceiling"]):
                best_known = score if best_known is None else max(best_known, score)

    # ------------------------------------------------------------ tidy export
    tidy_path = os.path.join(out_dir, "comparison_summary.csv")
    all_rows = []
    for run in runs:
        spent = 0
        for i, row in enumerate(run["rows"]):
            if not row["cached"]:
                spent += 1
            record = dict(row)
            record["evaluations_spent"] = spent
            record["eval_ordinal"] = i
            all_rows.append(record)
    keys = list(dict.fromkeys(k for r in all_rows for k in r))
    with open(tidy_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(all_rows)
    log.info("wrote %s (%d evaluations across %d runs)",
             tidy_path, len(all_rows), len(runs))

    # -------------------------------------------------------------- summaries
    summaries = [summarize(r, best_known, args.reach_fraction, floor) for r in runs]
    summary_path = os.path.join(out_dir, "optimizer_summary.csv")
    with open(summary_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(summaries[0]))
        writer.writeheader()
        writer.writerows(summaries)

    # ---------------------------------------------------------------- figures
    plots.plot_all(runs, out_dir, args.noise_floor)

    # ----------------------------------------------------------------- report
    reach_key = f"evals_to_{int(args.reach_fraction * 100)}pct"
    print(f"\n=== comparison over {len(runs)} run(s) ===")
    if not args.noise_floor:
        print(f"  NOTE: no --noise-floor given; using the {args.quantum:,.0f} QPS "
              "measurement quantum. Calibrate by evaluating the baseline 3-5 times\n"
              "        and passing the standard deviation, or differences here may "
              "be noise.")
    print(f"  best known score across all runs: "
          f"{best_known:,.0f} QPS\n" if best_known else "  no usable scores\n")

    header = (f"{'run':<28} {'opt':<6} {'evals':>6} {'best QPS':>11} "
              f"{'uplift':>10} {reach_key:>16} {'wall min':>9}")
    print(header)
    print("-" * len(header))
    for s in summaries:
        uplift = f"{s['uplift_pct']:+.1f}%" if s["uplift_pct"] is not None else "-"
        if s["uplift_is_within_noise"]:
            uplift += "*"
        best = f"{s['best_qps']:,.0f}" if s["best_qps"] else "-"
        # Explicit None checks: 0 is a meaningful value here. It means the
        # target was reached entirely from cached measurements, at no cost.
        reached = "-" if s[reach_key] is None else str(s[reach_key])
        wall = "-" if s["wall_clock_min"] is None else str(s["wall_clock_min"])
        print(f"{s['run_id'][:28]:<28} {s['optimizer'][:6]:<6} "
              f"{s['evaluations_spent']:>6} {best:>11} {uplift:>10} "
              f"{reached:>16} {wall:>9}")

    if any(s["uplift_is_within_noise"] for s in summaries):
        print("\n  * this run's uplift is smaller than the noise floor: not a "
              "demonstrated improvement.")
    if any(s["best_changes_semantics"] for s in summaries):
        print("  ! a best configuration changes protocol semantics (minimal-responses,"
              "\n    answer-cookie, or dnssec-validation). Report that separately from "
              "free wins:\n    part of the gain is a change in what the server answers.")

    print(f"\n  wrote {summary_path}")
    print(f"  figures in {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
