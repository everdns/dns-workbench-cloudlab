"""Append-only record of everything an optimization run did.

The ledger is the project's single source of truth, and it is deliberately
append-only with an fsync per line. Three things depend on that:

  * **Resume.** The grid driver has no separate checkpoint file -- it re-reads
    the ledger and skips candidates it already measured. A checkpoint that can
    drift out of sync with the results is worse than no checkpoint at all.
  * **Crash evidence.** An evaluation is recorded as ``in_flight`` *before* the
    config is applied, so a power loss still leaves a record of what was on the
    box.
  * **Comparability.** Grid and agent runs write the identical row shape, so
    ``compare.py`` can put them on the same axes without special-casing either.

Rows are never rewritten. An evaluation that completes appends a second row for
the same ``candidate_id``; readers take the last one.
"""

import csv
import json
import os
import time

SCHEMA_VERSION = 1


class Ledger:
    """Append-only JSONL ledger for one optimization run."""

    def __init__(self, run_dir, run_id, optimizer):
        self.run_dir = os.path.abspath(os.path.expanduser(run_dir))
        self.run_id = run_id
        self.optimizer = optimizer
        self.path = os.path.join(self.run_dir, "ledger.jsonl")
        os.makedirs(self.run_dir, exist_ok=True)
        os.makedirs(os.path.join(self.run_dir, "evals"), exist_ok=True)

    # ------------------------------------------------------------------ writing

    def append(self, event, **fields):
        """Append one record. Flushed and fsynced so a crash cannot lose it."""
        row = {
            "schema_version": SCHEMA_VERSION,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "run_id": self.run_id,
            "optimizer": self.optimizer,
            "event": event,
        }
        row.update(fields)
        with open(self.path, "a") as f:
            f.write(json.dumps(row, default=str) + "\n")
            f.flush()
            os.fsync(f.fileno())
        return row

    def run_start(self, manifest):
        return self.append("run_start", manifest=manifest)

    def run_end(self, reason, best=None):
        return self.append("run_end", reason=reason, best=best)

    def in_flight(self, eval_index, candidate_id, params, rationale=None):
        """Record the intent to apply, before anything on the host changes."""
        return self.append(
            "evaluation", eval_index=eval_index, candidate_id=candidate_id,
            params=params, rationale=rationale, status="in_flight",
        )

    def evaluation(self, eval_index, candidate_id, params, apply_result,
                   eval_result, budget_snapshot, provenance,
                   rationale=None, cached=False, changes_semantics=False):
        return self.append(
            "evaluation",
            eval_index=eval_index,
            candidate_id=candidate_id,
            params=params,
            cached=cached,
            changes_semantics=changes_semantics,
            apply=apply_result,
            eval=eval_result,
            budget=budget_snapshot,
            provenance=provenance,
            rationale=rationale,
            status=eval_result.get("status") if eval_result else "apply_failed",
        )

    def hypothesis(self, **fields):
        return self.append("hypothesis", **fields)

    def model_turn(self, **fields):
        return self.append("model_turn", **fields)

    # ------------------------------------------------------------------ reading

    def read(self):
        """All records, oldest first. Tolerates a truncated final line."""
        if not os.path.exists(self.path):
            return []
        rows = []
        with open(self.path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    # A hard kill mid-write can leave a partial last line.
                    continue
        return rows

    def completed(self):
        """candidate_id -> the last completed evaluation record for it.

        ``in_flight`` rows are skipped, so a candidate whose run was interrupted
        is retried rather than silently treated as done.
        """
        out = {}
        for row in self.read():
            if row.get("event") != "evaluation":
                continue
            if row.get("status") == "in_flight":
                continue
            cid = row.get("candidate_id")
            if cid:
                out[cid] = row
        return out

    def evaluations(self):
        """Completed evaluations in order, one per row (cached ones included)."""
        return [r for r in self.read()
                if r.get("event") == "evaluation" and r.get("status") != "in_flight"]

    def best(self):
        """The highest scoring uncensored evaluation, or None.

        A ceiling-hit result is a lower bound rather than a maximum, so it can
        never become 'best' -- ranking it against uncensored results would be
        comparing a floor to a ceiling.
        """
        best = None
        for row in self.evaluations():
            ev = row.get("eval") or {}
            if ev.get("status") != "ok":
                continue
            if ev.get("hit_max_qps_ceiling"):
                continue
            score = ev.get("max_qps_passed")
            if score is None:
                continue
            if best is None or score > (best.get("eval") or {}).get("max_qps_passed", -1):
                best = row
        return best

    def evals_spent(self):
        """Evaluations that actually cost testbed time (cache hits are free)."""
        return sum(1 for r in self.evaluations() if not r.get("cached"))

    # ------------------------------------------------------------------ export

    def export_csv(self, filename="ledger.csv"):
        """Flatten evaluations to a tidy CSV, one row per evaluation."""
        rows = []
        best_so_far = None
        for i, row in enumerate(self.evaluations()):
            ev = row.get("eval") or {}
            ap = row.get("apply") or {}
            score = ev.get("max_qps_passed")
            if (ev.get("status") == "ok" and not ev.get("hit_max_qps_ceiling")
                    and score is not None):
                best_so_far = score if best_so_far is None else max(best_so_far, score)
            flat = {
                "optimizer": row.get("optimizer"),
                "run_id": row.get("run_id"),
                "eval_index": row.get("eval_index", i),
                "candidate_id": row.get("candidate_id"),
                "cached": row.get("cached", False),
                "changes_semantics": row.get("changes_semantics", False),
                "apply_status": ap.get("status"),
                "apply_exit_code": ap.get("exit_code"),
                "status": ev.get("status"),
                "max_qps_passed": score,
                "hit_max_qps_ceiling": ev.get("hit_max_qps_ceiling"),
                "qps_fidelity_pct": ev.get("qps_fidelity_pct"),
                "eval_seconds": ev.get("eval_seconds"),
                "best_so_far": best_so_far,
                "elapsed_s": (row.get("budget") or {}).get("elapsed_s"),
                "rationale": row.get("rationale"),
            }
            for name, value in (row.get("params") or {}).items():
                flat[f"param_{name}"] = value
            rows.append(flat)

        if not rows:
            return None
        keys = list(dict.fromkeys(k for r in rows for k in r))
        path = os.path.join(self.run_dir, filename)
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader()
            writer.writerows(rows)
        return path
