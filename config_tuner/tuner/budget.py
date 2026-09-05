"""Campaign limits, enforced by the harness rather than by the optimizer.

Every cap here lives outside whatever is proposing candidates. The agent has no
tool that takes a budget argument and no way to read or raise one; the grid
driver is subject to exactly the same object. That separation is the point --
a budget the proposer can negotiate with is not a budget.

Three stop conditions are worth calling out:

  * ``max_consecutive_apply_failures`` catches a proposer that has wandered into
    a region where nothing starts, before it burns the clock discovering that
    one candidate at a time.
  * ``fatal`` is tripped by exit code 6 from the apply script -- rollback itself
    failed, so the host is in an unknown state and no further evaluation may run
    under any circumstances.
  * The ``STOP`` file lets a human halt a run from any shell on any node. The
    run directory lives on the NFS-shared home, so ``touch <run-dir>/STOP`` works
    from the workstation, the name server, or a load generator.
"""

import os
import time


class BudgetExceeded(Exception):
    """Raised when a run must stop. The message names the specific limit."""


class BudgetGuard:
    """Tracks and enforces the limits of one optimization run."""

    def __init__(self, run_dir, max_evals=25, max_wall_clock_minutes=240,
                 max_consecutive_apply_failures=3, max_consecutive_infra_errors=2,
                 max_duplicate_proposals=5, max_model_turns=80,
                 max_api_cost_usd=50.0, started=None):
        self.run_dir = os.path.abspath(os.path.expanduser(run_dir))
        self.max_evals = max_evals
        self.max_wall_clock_s = max_wall_clock_minutes * 60
        self.max_consecutive_apply_failures = max_consecutive_apply_failures
        self.max_consecutive_infra_errors = max_consecutive_infra_errors
        self.max_duplicate_proposals = max_duplicate_proposals
        self.max_model_turns = max_model_turns
        self.max_api_cost_usd = max_api_cost_usd

        self.started = started if started is not None else time.time()
        self.evals_used = 0
        self.model_turns = 0
        self.api_cost_usd = 0.0
        self.consecutive_apply_failures = 0
        self.consecutive_infra_errors = 0
        self.duplicate_proposals = 0
        self.stopped_reason = None
        self.fatal = False

    # ------------------------------------------------------------------ state

    @property
    def stop_file(self):
        return os.path.join(self.run_dir, "STOP")

    @property
    def elapsed_s(self):
        return time.time() - self.started

    @property
    def evals_remaining(self):
        return max(0, self.max_evals - self.evals_used)

    @property
    def minutes_remaining(self):
        return max(0.0, (self.max_wall_clock_s - self.elapsed_s) / 60.0)

    def snapshot(self):
        return {
            "evals_used": self.evals_used,
            "evals_max": self.max_evals,
            "elapsed_s": round(self.elapsed_s, 1),
            "wall_max_s": self.max_wall_clock_s,
            "model_turns": self.model_turns,
            "api_cost_usd": round(self.api_cost_usd, 4),
        }

    def resume_from(self, ledger):
        """Re-establish spend from an existing ledger, for --resume."""
        self.evals_used = ledger.evals_spent()
        return self.evals_used

    # ---------------------------------------------------------------- checking

    def why_stopped(self):
        """The reason this run must stop, or None if it may continue.

        Checked before every evaluation and, for the agent, after every model
        turn. Returns rather than raises so callers can record the reason and
        exit cleanly instead of unwinding through an exception.
        """
        if self.fatal:
            return self.stopped_reason or "fatal: rollback failed on the name server"
        if os.path.exists(self.stop_file):
            return "STOP file present in the run directory"
        if self.evals_used >= self.max_evals:
            return f"evaluation budget exhausted ({self.max_evals})"
        if self.elapsed_s >= self.max_wall_clock_s:
            return f"wall-clock budget exhausted ({self.max_wall_clock_s / 60:.0f} min)"
        if self.consecutive_apply_failures >= self.max_consecutive_apply_failures:
            return (f"{self.consecutive_apply_failures} consecutive apply failures "
                    "-- the proposer is in a region where nothing starts")
        if self.consecutive_infra_errors >= self.max_consecutive_infra_errors:
            return f"{self.consecutive_infra_errors} consecutive infrastructure errors"
        if self.duplicate_proposals >= self.max_duplicate_proposals:
            return f"{self.duplicate_proposals} duplicate proposals -- the search has stalled"
        if self.model_turns >= self.max_model_turns:
            return f"model turn limit reached ({self.max_model_turns})"
        if self.api_cost_usd >= self.max_api_cost_usd:
            return f"API cost cap reached (${self.api_cost_usd:.2f})"
        return None

    def check(self):
        reason = self.why_stopped()
        if reason:
            self.stopped_reason = reason
            raise BudgetExceeded(reason)

    def may_continue(self):
        reason = self.why_stopped()
        if reason:
            self.stopped_reason = reason
            return False
        return True

    # ---------------------------------------------------------------- accounting

    def record_evaluation(self, status, cached=False, apply_exit_code=None):
        """Book one evaluation's outcome and update the failure streaks."""
        if apply_exit_code == 6:
            # Rollback failed on the host. Nothing else may run.
            self.fatal = True
            self.stopped_reason = (
                "fatal: the name server could not roll back a candidate; "
                "inspect /var/log/dns-tuner/audit.jsonl before running anything else"
            )

        if not cached:
            self.evals_used += 1

        if status == "apply_failed":
            self.consecutive_apply_failures += 1
        elif status in ("infra_error", "timeout", "parse_error"):
            self.consecutive_infra_errors += 1
        else:
            # A measured result -- including a legitimate zero -- clears both
            # streaks. `no_passing_level` means the server really could not
            # sustain the lowest level, which is data, not a malfunction.
            self.consecutive_apply_failures = 0
            self.consecutive_infra_errors = 0

    def record_duplicate(self):
        self.duplicate_proposals += 1

    def record_model_turn(self, usage=None, input_price=5.0, output_price=25.0):
        """Count a turn and accrue its cost from the API's own usage figures."""
        self.model_turns += 1
        if not usage:
            return
        get = (lambda k: getattr(usage, k, None)) if not isinstance(usage, dict) else usage.get
        inp = (get("input_tokens") or 0)
        cache_read = (get("cache_read_input_tokens") or 0)
        cache_write = (get("cache_creation_input_tokens") or 0)
        out = (get("output_tokens") or 0)
        self.api_cost_usd += (
            inp * input_price / 1_000_000
            + cache_read * input_price * 0.1 / 1_000_000
            + cache_write * input_price * 1.25 / 1_000_000
            + out * output_price / 1_000_000
        )
