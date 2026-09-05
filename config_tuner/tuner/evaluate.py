"""Measure one candidate: apply it, run the QPS search, return a single number.

``max_sustainable_qps.py`` is invoked as a subprocess rather than imported. It
calls ``logging.basicConfig``, does module-level ``sys.path`` injection, and is
built around ``sys.exit(main())`` -- importing it into a long-lived agent process
would leak logging configuration and give no isolation. A subprocess preserves
its exit-code contract verbatim, gives every evaluation its own output
directory, and allows a hard timeout on a process group.

That timeout is the sharp edge in this module. When we SIGKILL the process
group, the evaluator's own ``finally:`` never runs, so it never stops the DNS
service and never reaps the load generators. This module must therefore always
perform that cleanup itself -- see ``_cleanup_after_timeout``.
"""

import hashlib
import json
import logging
import math
import os
import signal
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LTB = os.path.join(REPO, "load_testing_benchmark")
if LTB not in sys.path:
    sys.path.insert(0, LTB)

MSQ_SCRIPT = os.path.join(LTB, "scripts", "max_sustainable_qps.py")

log = logging.getLogger(__name__)


class EvalConfig:
    """Everything an evaluation needs that is not the candidate itself."""

    def __init__(self, eval_config_path, dns_service="ns_bind", tool="dnsperf",
                 server=None, clients=None, collectl=False, settle_seconds=15,
                 ramp_seed_fraction=0.5, timeout_slack=1.5, cache_dir=None,
                 stop_script="/local/repository/stop_dns_service.sh"):
        self.eval_config_path = os.path.abspath(os.path.expanduser(eval_config_path))
        self.dns_service = dns_service
        self.tool = tool
        self.server = server
        self.clients = clients or []
        self.collectl = collectl
        self.settle_seconds = settle_seconds
        self.ramp_seed_fraction = ramp_seed_fraction
        self.timeout_slack = timeout_slack
        self.cache_dir = os.path.expanduser(cache_dir) if cache_dir else None
        self.stop_script = stop_script
        self._yaml = None

    @property
    def yaml(self):
        if self._yaml is None:
            import yaml
            with open(self.eval_config_path) as f:
                self._yaml = yaml.safe_load(f) or {}
        return self._yaml

    @property
    def msq(self):
        return self.yaml.get("max_sustainable_qps", {}) or {}

    def sha(self):
        with open(self.eval_config_path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()

    def compute_timeout(self, initial_qps):
        """Derive a defensible wall-clock ceiling instead of guessing one.

        Logged with every evaluation so a timeout is diagnosable rather than
        mysterious.
        """
        msq = self.msq
        min_step = int(msq.get("min_qps_step", 10000))
        max_qps = int(msq.get("max_qps", 1000000))
        num_trials = int(msq.get("num_trials", 5))
        trial_duration = int(msq.get("trial_duration", 10))
        margin = int(msq.get("collectl_margin", 5)) if self.collectl else 0
        pause = int(self.yaml.get("pause_between_runs", 5))

        initial_qps = max(initial_qps, min_step)
        ramp = max(1, math.ceil(math.log2(max(2, max_qps / initial_qps))))
        bisect = max(1, math.ceil(math.log2(max(2, initial_qps / min_step))))
        levels = ramp + bisect + 2
        per_trial = trial_duration + 2 * margin + pause + 30
        return int(self.timeout_slack * levels * num_trials * per_trial) + 300


class EvalResult(dict):
    """A measurement outcome. ``max_qps_passed`` is None when nothing was measured."""

    @property
    def score(self):
        return self.get("max_qps_passed")

    @property
    def usable(self):
        """True only for a real, uncensored, generator-unlimited measurement."""
        return (self.get("status") == "ok"
                and self.get("max_qps_passed") is not None
                and not self.get("hit_max_qps_ceiling")
                and not self.get("generator_limited"))


class Evaluator:
    """Applies a candidate and measures its max sustainable QPS."""

    def __init__(self, applier, cfg, run_dir, schema, facts=None, dry_run=False):
        self.applier = applier
        self.cfg = cfg
        self.run_dir = os.path.abspath(os.path.expanduser(run_dir))
        self.schema = schema
        self.facts = facts
        self.dry_run = dry_run
        self._cache = self._load_cache()

    # ------------------------------------------------------------------- cache

    def _cache_path(self):
        if not self.cfg.cache_dir:
            return None
        os.makedirs(self.cfg.cache_dir, exist_ok=True)
        return os.path.join(self.cfg.cache_dir, "measurements.json")

    def _cache_key(self, candidate_id):
        # Everything that could change what the number means is in the key, so a
        # hit is genuinely the same measurement and not a coincidence of names.
        parts = [candidate_id, self.cfg.sha(), self.cfg.dns_service, self.cfg.tool]
        from tuner import schema as schema_mod
        parts.append(schema_mod.schema_sha())
        return hashlib.sha256("|".join(parts).encode()).hexdigest()[:24]

    def _load_cache(self):
        path = self._cache_path()
        if not path or not os.path.exists(path):
            return {}
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_cache(self):
        path = self._cache_path()
        if not path:
            return
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self._cache, f, indent=2, default=str)
        os.replace(tmp, path)

    # -------------------------------------------------------------- the measure

    def evaluate(self, params, eval_index, best_so_far=None, simulate_score=None):
        """Apply, measure, and return an EvalResult. Never raises."""
        from tuner import schema as schema_mod
        try:
            canon = schema_mod.canonical(self.schema, params, self.facts)
            cid = schema_mod.candidate_id(self.schema, canon, self.facts)
        except schema_mod.SchemaError as e:
            return EvalResult(status="invalid_candidate", max_qps_passed=None,
                              message=str(e), candidate_id=None,
                              apply={"status": "invalid_candidate", "exit_code": 2,
                                     "description": str(e)})

        key = self._cache_key(cid)
        if key in self._cache:
            cached = EvalResult(self._cache[key])
            cached["cached"] = True
            cached["candidate_id"] = cid
            log.info("Cache hit for %s -> %s QPS (no budget spent)",
                     cid, cached.get("max_qps_passed"))
            return cached

        apply_result = self.applier.apply(self.schema, canon, self.facts, stop_after=True)
        if apply_result["status"] != "ok":
            return EvalResult(
                status=("fatal" if apply_result["exit_code"] == 6 else "apply_failed"),
                max_qps_passed=None, candidate_id=cid, cached=False,
                apply=apply_result, message=apply_result["description"],
            )

        if self.cfg.settle_seconds and not self.dry_run and simulate_score is None:
            # Consecutive evaluations are not independent -- page cache, CPU
            # frequency, and NIC ring state all carry over. Let the host settle.
            time.sleep(self.cfg.settle_seconds)

        eval_dir = os.path.join(self.run_dir, "evals", f"{eval_index:04d}-{cid}")
        os.makedirs(eval_dir, exist_ok=True)
        result = self._run_search(eval_dir, best_so_far, simulate_score)
        result["candidate_id"] = cid
        result["cached"] = False
        result["apply"] = apply_result

        if result.get("status") in ("ok", "no_passing_level"):
            self._cache[key] = dict(result)
            self._save_cache()
        return result

    def _seed_qps(self, best_so_far):
        """Start the ramp near the incumbent instead of from the config default.

        Phase 1 of the search doubles from ``initial_qps``; starting at 50k when
        the answer is ~700k wastes four levels. Binary search converges to the
        same boundary regardless of where the ramp began, so this costs nothing
        in accuracy. Seeding *above* the incumbent would be worse than not
        seeding at all -- phase 1 would fail immediately and phase 2 would then
        bisect the entire range.
        """
        msq = self.cfg.msq
        min_step = int(msq.get("min_qps_step", 10000))
        default = int(msq.get("initial_qps", 50000))
        if not best_so_far:
            return default
        seed = int(best_so_far * self.cfg.ramp_seed_fraction)
        seed = max(min_step, (seed // min_step) * min_step)
        return max(min_step, min(seed, best_so_far - min_step) if seed >= best_so_far else seed)

    def _run_search(self, eval_dir, best_so_far, simulate_score):
        seed = self._seed_qps(best_so_far)
        timeout = self.cfg.compute_timeout(seed)

        cmd = [
            sys.executable, MSQ_SCRIPT,
            "--config", self.cfg.eval_config_path,
            "--dns-service", self.cfg.dns_service,
            "--tool", self.cfg.tool,
            "--output-dir", eval_dir,
            "--initial-qps", str(seed),
            "--collectl" if self.cfg.collectl else "--no-collectl",
        ]
        if simulate_score is not None:
            # Offline mode still runs the REAL evaluator, which performs its real
            # two-phase search and writes real CSV/JSON artifacts. That keeps
            # argv construction, timeout handling, exit codes, parsing, and the
            # directory layout all under test with zero remote execution.
            cmd += ["--simulate-max-qps", str(int(simulate_score))]
        if self.dry_run:
            cmd.append("--dry-run")

        log.info("Evaluating: seed=%d QPS, timeout=%ds -> %s", seed, timeout, eval_dir)
        started = time.time()
        # Popen rather than subprocess.run: on timeout, run() kills only the
        # direct child, leaving the evaluator's own ssh and dnsperf descendants
        # alive to interfere with the next evaluation. start_new_session puts
        # the whole tree in its own process group so killpg can take all of it.
        proc = subprocess.Popen(
            cmd, cwd=LTB, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, start_new_session=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            returncode, timed_out = proc.returncode, False
        except subprocess.TimeoutExpired:
            timed_out, returncode = True, None
            kill_process_group(proc.pid)
            try:
                stdout, stderr = proc.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, stderr = "", ""
            stderr = (stderr or "") + f"\nevaluation exceeded {timeout}s"
            self._cleanup_after_timeout()
        elapsed = round(time.time() - started, 1)

        with open(os.path.join(eval_dir, "evaluator.log"), "w") as f:
            f.write(f"=== argv ===\n{' '.join(cmd)}\n")
            f.write(f"=== seed_qps={seed} timeout_s={timeout} rc={returncode} ===\n")
            f.write(f"=== STDOUT ===\n{stdout}\n=== STDERR ===\n{stderr}\n")

        if timed_out:
            return EvalResult(status="timeout", max_qps_passed=None,
                              eval_seconds=elapsed, seed_qps=seed,
                              timeout_s=timeout, message=f"exceeded {timeout}s")
        if returncode == 2:
            # The evaluator rejected its own configuration. That is our bug, not
            # a property of the candidate, and must never cost budget.
            return EvalResult(status="tuner_bug", max_qps_passed=None,
                              eval_seconds=elapsed, seed_qps=seed,
                              message="max_sustainable_qps.py rejected its config "
                                      f"(exit 2): {stderr.strip()[-500:]}")
        if returncode == 1:
            return EvalResult(status="infra_error", max_qps_passed=None,
                              eval_seconds=elapsed, seed_qps=seed,
                              message="the DNS service failed to start")
        if returncode != 0:
            return EvalResult(status="error", max_qps_passed=None,
                              eval_seconds=elapsed, seed_qps=seed,
                              message=f"exit {returncode}: {stderr.strip()[-500:]}")

        return self._parse(eval_dir, elapsed, seed)

    def _parse(self, eval_dir, elapsed, seed):
        summary_path = os.path.join(eval_dir, "max_sustainable_qps", "search_summary.json")
        if not os.path.exists(summary_path):
            # Reachable even after exit 0: ResultStore.export_json writes nothing
            # when it holds no rows.
            return EvalResult(status="parse_error", max_qps_passed=None,
                              eval_seconds=elapsed, seed_qps=seed,
                              message="search_summary.json was not written")
        try:
            with open(summary_path) as f:
                rows = json.load(f)
            summary = rows[0]
        except (json.JSONDecodeError, IndexError, KeyError) as e:
            return EvalResult(status="parse_error", max_qps_passed=None,
                              eval_seconds=elapsed, seed_qps=seed,
                              message=f"unreadable search_summary.json: {e}")

        score = int(summary.get("max_qps_passed", 0))
        ceiling = bool(summary.get("hit_max_qps_ceiling"))
        fidelity, generator_limited = self._check_fidelity(eval_dir, score)

        if score == 0:
            # A legitimate zero: the server could not sustain even one step.
            # This is data, not an error, and conflating the two would poison a
            # proposer's model of the space.
            status = "no_passing_level"
        else:
            status = "ok"

        result = EvalResult(
            status=status,
            max_qps_passed=score,
            max_qps_tested=summary.get("max_qps_tested"),
            hit_max_qps_ceiling=ceiling,
            generator_limited=generator_limited,
            qps_fidelity_pct=fidelity,
            num_qps_values_tested=summary.get("num_qps_values_tested"),
            total_trials_run=summary.get("total_trials_run"),
            search_duration_s=summary.get("search_duration_s"),
            eval_seconds=elapsed,
            seed_qps=seed,
            output_dir=os.path.relpath(eval_dir, self.run_dir),
        )
        if ceiling:
            result["message"] = (
                f"hit the {summary.get('max_qps_ceiling')} QPS ceiling -- "
                "this score is a LOWER BOUND, not a maximum"
            )
        if generator_limited:
            result["message"] = (
                f"load generator only sent {fidelity}% of the requested queries -- "
                "this measures the generator, not the server"
            )
        return result

    def _check_fidelity(self, eval_dir, score):
        """Did the load generator actually send what was asked at the top level?

        If it did not, the number describes dnsperf rather than BIND, and it
        must never be allowed to become the incumbent best.
        """
        import csv as _csv
        path = os.path.join(eval_dir, "max_sustainable_qps", "trial_results.csv")
        if not os.path.exists(path) or not score:
            return None, False
        best_rows = []
        try:
            with open(path) as f:
                for row in _csv.DictReader(f):
                    if row.get("passed") in ("True", "true", "1") and \
                            row.get("target_qps") and int(float(row["target_qps"])) == score:
                        val = row.get("qps_fidelity_pct")
                        if val not in (None, ""):
                            best_rows.append(float(val))
        except (OSError, ValueError, KeyError):
            return None, False
        if not best_rows:
            return None, False
        fidelity = round(sum(best_rows) / len(best_rows), 3)
        threshold = float(self.cfg.msq.get("min_qps_fidelity_pct", 99.0))
        return fidelity, fidelity < threshold

    def _cleanup_after_timeout(self):
        """Undo what a SIGKILLed evaluator left behind.

        The evaluator's ``finally:`` block never ran, so the DNS service is still
        up under the candidate config and load generators may still be firing.
        Neither of those can be left for the next evaluation to inherit.
        """
        log.warning("Evaluation timed out; running cleanup the evaluator skipped")
        try:
            from benchmark.remote import ssh_run, ssh_run_many
            if self.cfg.server:
                ssh_run(self.cfg.server, f"{self.cfg.stop_script} {self.cfg.dns_service}",
                        timeout=60)
            if self.cfg.clients:
                ssh_run_many(
                    {c: "pkill -f dnsperf; pkill -f kxdpgun; pkill -f dnspyre; true"
                     for c in self.cfg.clients}, timeout=60,
                )
        except Exception as e:  # noqa: BLE001 - cleanup must never mask the timeout
            log.warning("Post-timeout cleanup reported: %s", e)
        try:
            self.applier.baseline()
        except Exception as e:  # noqa: BLE001
            log.warning("Post-timeout baseline restore reported: %s", e)


def kill_process_group(pid, sig=signal.SIGKILL):
    """Kill a whole process group, tolerating an already-dead group."""
    try:
        os.killpg(os.getpgid(pid), sig)
    except (ProcessLookupError, PermissionError):
        pass
