#!/usr/bin/env python3
"""Optimizer 1: exhaustive grid search over a declared BIND configuration space.

This is the baseline the agentic optimizer is measured against. It shares every
component with the agent -- the same schema, applier, evaluator, ledger, and
budget guard -- so the only difference between the two is how the next candidate
gets chosen. That is what makes "evaluations spent to reach a given QPS" an
honest comparison rather than a comparison of two different harnesses.

    python3 config_tuner/grid_search.py \\
        --space config_tuner/configs/space_bind_small.yaml \\
        --tuner-config config_tuner/configs/tuner.yaml \\
        --run-dir ~/tuner_runs/grid-20260902 --max-evals 27 --max-hours 14

Offline, against the synthetic surface, with no testbed at all:

    python3 config_tuner/grid_search.py --space ... --simulate \\
        --run-dir /tmp/grid-sim --max-evals 40
"""

import argparse
import atexit
import json
import logging
import os
import signal
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tuner import schema as schema_mod  # noqa: E402
from tuner import space as space_mod  # noqa: E402
from tuner.apply import Applier, SimulatedApplier  # noqa: E402
from tuner.budget import BudgetGuard  # noqa: E402
from tuner.evaluate import EvalConfig, Evaluator  # noqa: E402
from tuner.ledger import Ledger  # noqa: E402
from tuner.simulate import DEFAULT_SURFACE, ResponseSurface  # noqa: E402

log = logging.getLogger("grid_search")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def build_parser():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--space", required=True, help="grid space YAML")
    p.add_argument("--tuner-config", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "configs", "tuner.yaml"))
    p.add_argument("--eval-config", help="override the max_sustainable_qps config")
    p.add_argument("--run-dir", required=True, help="where the ledger and artifacts go")
    p.add_argument("--strategy", choices=["full", "staged"], default="full",
                   help="full factorial, or coordinate descent carrying the winner forward")
    p.add_argument("--order", choices=["shuffled", "lexicographic"], default="shuffled",
                   help="shuffled (default) keeps a truncated grid an unbiased sample")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--max-evals", type=int)
    p.add_argument("--max-hours", type=float)
    p.add_argument("--resume", action="store_true",
                   help="skip candidates the ledger shows as already measured")
    p.add_argument("--force", action="store_true",
                   help="resume even though the manifest hashes changed")
    p.add_argument("--simulate", nargs="?", const="__default__", metavar="SURFACE_YAML",
                   help="run offline against a synthetic response surface")
    p.add_argument("--dry-run", action="store_true",
                   help="print the expanded grid and the time estimate, then exit")
    p.add_argument("--yes", action="store_true",
                   help="proceed even if the estimate exceeds --max-hours")
    return p


def load_tuner_config(path):
    import yaml
    with open(os.path.expanduser(path)) as f:
        return yaml.safe_load(f) or {}


def git_rev():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO,
                              capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def build_manifest(args, schema_path, space_path, eval_config_path, space, simulate):
    import hashlib

    def sha(path):
        try:
            with open(path, "rb") as f:
                return hashlib.sha256(f.read()).hexdigest()
        except OSError:
            return None

    return {
        "argv": sys.argv,
        "git_rev": git_rev(),
        "schema_sha": sha(schema_path),
        "space_sha": sha(space_path),
        "eval_config_sha": sha(eval_config_path) if eval_config_path else None,
        "space": {k: list(v) for k, v in space.items()},
        "strategy": args.strategy,
        "order": args.order,
        "seed": args.seed,
        "simulated": bool(simulate),
        "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def check_manifest_drift(ledger, manifest, force):
    """Refuse to resume onto a changed space or schema unless told to."""
    for row in ledger.read():
        if row.get("event") != "run_start":
            continue
        old = row.get("manifest", {})
        drifted = [k for k in ("schema_sha", "space_sha", "eval_config_sha")
                   if old.get(k) and old.get(k) != manifest.get(k)]
        if drifted and not force:
            raise SystemExit(
                f"refusing to resume: {', '.join(drifted)} changed since this run "
                "started. Results would not be comparable. Re-run with --force to "
                "override (it is recorded in the ledger)."
            )
        if drifted:
            ledger.append("manifest_drift", changed=drifted,
                          old={k: old.get(k) for k in drifted},
                          new={k: manifest.get(k) for k in drifted})
        return


def main(argv=None):
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    cfg = load_tuner_config(args.tuner_config)
    simulate = args.simulate is not None

    schema = schema_mod.load_schema()
    facts = schema_mod.load_facts(cfg.get("paths", {}).get("facts"))

    space, spec = space_mod.load_space(args.space, schema, facts)

    eval_config_path = args.eval_config or cfg.get("paths", {}).get("eval_config")
    if not simulate and not eval_config_path:
        raise SystemExit("no --eval-config and none set in the tuner config")

    run_dir = os.path.abspath(os.path.expanduser(args.run_dir))
    run_id = os.path.basename(run_dir)
    ledger = Ledger(run_dir, run_id, "grid")

    manifest = build_manifest(args, schema_mod.DEFAULT_SCHEMA_PATH, args.space,
                              eval_config_path, space, simulate)

    # --------------------------------------------------------------- enumerate
    # The baseline is always measured, and always first: every later result is
    # reported as a delta against it, and the noise floor is measured on it.
    baseline = dict(schema_mod.defaults(schema))
    baseline_id = schema_mod.candidate_id(schema, baseline, facts)

    if args.strategy == "full":
        candidates = space_mod.enumerate_full(space, schema, spec, facts)
        candidates = space_mod.order(candidates, args.order, args.seed)
        if not any(schema_mod.candidate_id(schema, c, facts) == baseline_id
                   for c in candidates):
            candidates.insert(0, baseline)
        else:
            candidates.sort(
                key=lambda c: schema_mod.candidate_id(schema, c, facts) != baseline_id)
        total_expected = len(candidates)
        candidate_source = candidates
    else:
        # Coordinate descent has to be generated adaptively: each sweep holds
        # every other parameter at the winner of the sweeps before it, so the
        # candidates for stage N are not known until stage N-1 has been measured.
        total_expected = 1 + sum(len(v) for v in space.values())
        candidate_source = None  # built below, once best_row exists

    budget_cfg = dict(cfg.get("budget", {}))
    if args.max_evals is not None:
        budget_cfg["max_evals"] = args.max_evals
    if args.max_hours is not None:
        budget_cfg["max_wall_clock_minutes"] = int(args.max_hours * 60)
    budget_cfg.setdefault("max_evals", total_expected)
    budget = BudgetGuard(run_dir, **budget_cfg)

    # ---------------------------------------------------------------- dry run
    eval_cfg_kwargs = dict(cfg.get("evaluation", {}))
    per_eval_minutes = float(eval_cfg_kwargs.pop("estimated_minutes", 25))
    estimate_h = total_expected * per_eval_minutes / 60.0

    log.info("Space: %d axes, %d points in the product, %d candidates under "
             "strategy '%s'", len(space), space_mod.size(space), total_expected,
             args.strategy)
    log.info("Estimated wall clock: %.1f h at %.0f min per evaluation",
             estimate_h, per_eval_minutes)

    if args.dry_run:
        if args.strategy == "full":
            for i, candidate in enumerate(candidates):
                cid = schema_mod.candidate_id(schema, candidate, facts)
                deltas = {k: v for k, v in candidate.items() if v != baseline.get(k)}
                print(f"  {i:3d}  {cid}  {deltas or '(baseline)'}")
        else:
            print("  staged: baseline, then one sweep per axis, each holding the "
                  "previous winners fixed")
            for name in space:
                print(f"    {name}: {space[name]}")
        print(f"\n{total_expected} candidates, ~{estimate_h:.1f} h")
        return 0

    if args.max_hours and estimate_h > args.max_hours and not args.yes:
        raise SystemExit(
            f"estimated {estimate_h:.1f} h exceeds --max-hours {args.max_hours}. "
            "Shrink the space, use --strategy staged, or pass --yes."
        )

    # ------------------------------------------------------------------- setup
    if simulate:
        surface_spec = DEFAULT_SURFACE
        if args.simulate != "__default__":
            surface = ResponseSurface.from_file(args.simulate)
        else:
            surface = ResponseSurface(surface_spec)
        applier = SimulatedApplier(fail_on=surface.fail_to_start)
        eval_config_path = eval_config_path or os.path.join(
            REPO, "load_testing_benchmark", "configs", "binary_testing.yaml")
    else:
        surface = None
        applier = Applier(
            cfg["hosts"]["server"],
            staging_remote=cfg.get("paths", {}).get(
                "staging_remote", "/var/lib/dns-tuner/staging/candidate.json"),
            apply_cmd=cfg.get("paths", {}).get(
                "apply_cmd", "sudo /usr/local/sbin/dns_tuner_apply"),
        )

    eval_cfg = EvalConfig(
        eval_config_path,
        server=cfg.get("hosts", {}).get("server"),
        clients=cfg.get("hosts", {}).get("clients", []),
        **eval_cfg_kwargs,
    )
    evaluator = Evaluator(applier, eval_cfg, run_dir, schema, facts)

    done = ledger.completed() if args.resume else {}
    if args.resume:
        check_manifest_drift(ledger, manifest, args.force)
        budget.resume_from(ledger)
        log.info("Resuming: %d candidates already measured, %d evaluations spent",
                 len(done), budget.evals_used)

    ledger.run_start(manifest)

    # Leaving the testbed tuned after an interrupted run would silently bias the
    # next one, so restore the baseline on every exit path.
    def restore_baseline():
        if not simulate:
            try:
                applier.baseline()
            except Exception as e:  # noqa: BLE001
                log.warning("Baseline restore on exit failed: %s", e)

    atexit.register(restore_baseline)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: sys.exit(130))

    # ------------------------------------------------------------------ search
    best_score, best_row = None, None
    if args.resume:
        prior = ledger.best()
        if prior:
            best_score = (prior.get("eval") or {}).get("max_qps_passed")
            best_row = prior.get("params")

    stop_reason = "completed"
    eval_index = len(ledger.evaluations())

    def staged_candidates():
        """Sweep one axis at a time, each sweep holding the winners so far fixed.

        A generator, because the driver must measure everything this yields
        before the next stage's candidates can be determined.
        """
        incumbent = dict(schema_mod.canonical(schema, spec.get("baseline") or {}, facts))
        yield dict(incumbent)
        for axis in space:
            log.info("--- staged sweep of '%s' over %s, holding %s ---",
                     axis, space[axis],
                     {k: v for k, v in incumbent.items() if k in space and k != axis})
            for candidate in space_mod.next_stage(space, schema, incumbent, axis, facts):
                yield candidate
            if best_row:
                # Carry the best configuration found so far into the next sweep.
                incumbent = dict(best_row)

    if args.strategy == "staged":
        candidate_source = staged_candidates()

    for candidate in candidate_source:
        cid = schema_mod.candidate_id(schema, candidate, facts)
        if cid in done:
            log.info("Skipping %s -- already in the ledger", cid)
            continue
        if not budget.may_continue():
            stop_reason = budget.stopped_reason
            log.warning("Stopping: %s", stop_reason)
            break

        ledger.in_flight(eval_index, cid, candidate)
        simulate_score = None
        if simulate:
            simulate_score = surface.score(candidate)

        result = evaluator.evaluate(candidate, eval_index, best_score, simulate_score)
        apply_result = result.get("apply", {})
        budget.record_evaluation(result.get("status"), result.get("cached", False),
                                 apply_result.get("exit_code"))

        ledger.evaluation(
            eval_index=eval_index, candidate_id=cid, params=candidate,
            apply_result=apply_result,
            eval_result={k: v for k, v in result.items() if k not in ("apply",)},
            budget_snapshot=budget.snapshot(),
            provenance={
                "schema_sha": manifest["schema_sha"],
                "eval_config_sha": manifest["eval_config_sha"],
                "git_rev": manifest["git_rev"],
                "tool": eval_cfg.tool,
                "simulated": simulate,
            },
            cached=result.get("cached", False),
            changes_semantics=schema_mod.touches_semantics(schema, candidate),
        )
        ledger.export_csv()

        score = result.get("max_qps_passed")
        if result.get("status") == "ok" and score is not None \
                and not result.get("hit_max_qps_ceiling") \
                and not result.get("generator_limited"):
            if best_score is None or score > best_score:
                best_score, best_row = score, dict(candidate)
                log.info("New best: %d QPS", score)

        log.info("[%d/%d] %s -> %s (%s) | best=%s | %d/%d evals, %.0f min left",
                 eval_index + 1, total_expected, cid,
                 score if score is not None else "-", result.get("status"),
                 best_score, budget.evals_used, budget.max_evals,
                 budget.minutes_remaining)
        eval_index += 1

        if budget.fatal:
            stop_reason = budget.stopped_reason
            log.error("HALTING: %s", stop_reason)
            break

    ledger.run_end(stop_reason, best={"params": best_row, "max_qps_passed": best_score})
    csv_path = ledger.export_csv()

    log.info("=== grid search finished: %s ===", stop_reason)
    log.info("Best: %s QPS with %s", best_score, best_row)
    log.info("Evaluations spent: %d | wall clock: %.1f min",
             budget.evals_used, budget.elapsed_s / 60)
    log.info("Ledger: %s", ledger.path)
    if csv_path:
        log.info("CSV: %s", csv_path)

    if simulate and surface:
        truth, truth_score = surface.true_optimum(candidates)
        log.info("Surface optimum (noise-free): %s QPS", truth_score)
        if best_score is not None:
            log.info("Found %.1f%% of the true optimum",
                     100.0 * best_score / truth_score if truth_score else 0.0)

    return 0


if __name__ == "__main__":
    sys.exit(main())
