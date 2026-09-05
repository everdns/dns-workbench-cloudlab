"""T1: the agent's tool surface and handlers, with no API calls and no testbed.

Exercises the paths that are expensive to discover on real hardware: a duplicate
proposal, an out-of-schema proposal, an apply failure, a budget running out, and
the hypothesis/compare bookkeeping. A scripted fake stands in for the model, so
the loop, the budget guard, and the ledger are all covered for free.

    python3 config_tuner/tests/test_agent_loop.py
"""

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, PKG)

import tools_spec  # noqa: E402
from tuner import schema as schema_mod  # noqa: E402
from tuner import space as space_mod  # noqa: E402
from tuner.apply import SimulatedApplier  # noqa: E402
from tuner.budget import BudgetGuard  # noqa: E402
from tuner.ledger import Ledger  # noqa: E402
from tuner.simulate import DEFAULT_SURFACE, ResponseSurface  # noqa: E402

SCHEMA = schema_mod.load_schema()


class FakeEvaluator:
    """Stands in for Evaluator: scores from the surface, no subprocess at all.

    The real Evaluator is covered end-to-end by the simulated grid run, which
    does spawn the genuine max_sustainable_qps.py. This stub exists so the agent
    loop's own bookkeeping can be tested in milliseconds.
    """

    class _Cfg:
        tool = "dnsperf"
        msq = {"min_qps_step": 10000}

    def __init__(self, surface, applier, fail_status=None):
        self.surface = surface
        self.applier = applier
        self.cfg = self._Cfg()
        self.calls = 0
        self.fail_status = fail_status

    def evaluate(self, params, eval_index, best_so_far=None, simulate_score=None):
        self.calls += 1
        apply_result = self.applier.apply(SCHEMA, params)
        if apply_result["status"] != "ok":
            return {"status": "apply_failed", "max_qps_passed": None,
                    "apply": apply_result, "cached": False}
        if self.fail_status:
            return {"status": self.fail_status, "max_qps_passed": None,
                    "apply": apply_result, "cached": False}
        score = simulate_score if simulate_score is not None else self.surface.score(params)
        return {"status": "ok", "max_qps_passed": score, "apply": apply_result,
                "cached": False, "eval_seconds": 1500}


def make_ctx(tmpdir, max_evals=5, noise_floor=None, fail_status=None):
    surface = ResponseSurface(DEFAULT_SURFACE)
    applier = SimulatedApplier(fail_on=surface.fail_to_start)
    ledger = Ledger(tmpdir, "test-run", "agent")
    budget = BudgetGuard(tmpdir, max_evals=max_evals, max_wall_clock_minutes=60)
    evaluator = FakeEvaluator(surface, applier, fail_status)
    return tools_spec.ToolContext(SCHEMA, evaluator, ledger, budget,
                                  noise_floor=noise_floor, surface=surface)


def call(name, payload, ctx):
    content, is_error = tools_spec.handle(name, payload, ctx)
    return json.loads(content), is_error


def _raises(fn, *args, **kwargs):
    try:
        result = fn(*args, **kwargs)
    except Exception as e:  # noqa: BLE001
        return e
    raise AssertionError(f"expected an exception, got {result!r}")


# ------------------------------------------------------------- tool definitions


def test_exactly_five_tools_and_no_shell():
    tools = tools_spec.build_tools(SCHEMA)
    names = {t["name"] for t in tools}
    assert names == {"evaluate_config", "compare_configs", "record_hypothesis",
                     "get_search_state", "finish"}
    # The containment property: nothing that could reach a shell or the filesystem.
    for forbidden in ("bash", "shell", "read", "write", "edit", "python", "exec"):
        assert forbidden not in names


def test_tool_schemas_are_strict_and_closed():
    for tool in tools_spec.build_tools(SCHEMA):
        assert tool["strict"] is True, tool["name"]
        schema = tool["input_schema"]
        assert schema["additionalProperties"] is False, tool["name"]
        assert "required" in schema, tool["name"]


def test_evaluate_config_schema_mirrors_tunables():
    tool = tools_spec.build_tools(SCHEMA)[0]
    props = tool["input_schema"]["properties"]["params"]["properties"]
    assert set(props) == set(schema_mod.params_by_name(SCHEMA))
    # Every parameter required: strict demands it, and it removes the
    # "does omitted mean default?" ambiguity.
    assert set(tool["input_schema"]["properties"]["params"]["required"]) == set(props)
    assert props["minimal_responses"]["enum"] == ["no", "no-auth",
                                                  "no-auth-recursive", "yes"]
    assert props["tcp_clients"]["minimum"] == 10
    assert props["tcp_clients"]["maximum"] == 10000


def test_grid_space_mode_narrows_the_schema():
    space_path = os.path.join(PKG, "configs", "space_bind_small.yaml")
    space, _ = space_mod.load_space(space_path, SCHEMA)
    tool = tools_spec.build_tools(SCHEMA, space)[0]
    props = tool["input_schema"]["properties"]["params"]["properties"]
    # Only the grid's axes are exposed, at exactly the grid's bounds -- so the
    # two optimizers search an identical set.
    assert set(props) == set(space)
    assert props["querylog"]["enum"] == ["yes", "no"]
    assert props["named_threads"]["minimum"] == 8
    assert props["named_threads"]["maximum"] == 32


def test_fact_cap_reaches_the_tool_schema():
    tool = tools_spec.build_tools(SCHEMA, None, {"nproc": 40})[0]
    props = tool["input_schema"]["properties"]["params"]["properties"]
    assert props["named_threads"]["maximum"] == 40


def test_json_schema_is_byte_stable():
    # Prompt caching renders tools before the system prompt; any instability here
    # invalidates the cache on every turn and multiplies campaign cost.
    a = json.dumps(tools_spec.build_tools(SCHEMA), sort_keys=True)
    b = json.dumps(tools_spec.build_tools(SCHEMA), sort_keys=True)
    assert a == b


# ------------------------------------------------------------------- handlers


def test_evaluate_config_measures_and_charges_budget():
    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp)
        out, is_error = call("evaluate_config", {
            "params": {"querylog": "no"}, "rationale": "baseline-ish"}, ctx)
        assert is_error is False
        assert out["status"] == "ok"
        assert out["max_qps_passed"] > 0
        assert ctx.budget.evals_used == 1
        assert out["evals_remaining"] == 4


def test_invalid_config_costs_no_budget():
    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp)
        out, is_error = call("evaluate_config", {
            "params": {"minimal_responses": "no; }; options { recursion yes; };"},
            "rationale": "hostile"}, ctx)
        assert out["status"] == "invalid_config"
        # A correctable mistake, not a wasted evaluation -- and not an error the
        # model should treat as a harness fault.
        assert is_error is False
        assert ctx.budget.evals_used == 0


def test_unknown_parameter_rejected_without_charge():
    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp)
        out, _ = call("evaluate_config", {
            "params": {"not_a_knob": 5}, "rationale": "?"}, ctx)
        assert out["status"] == "invalid_config"
        assert ctx.budget.evals_used == 0


def test_duplicate_proposals_are_counted():
    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, max_evals=10)
        for _ in range(3):
            call("evaluate_config", {"params": {"querylog": "no"},
                                     "rationale": "again"}, ctx)
        assert ctx.budget.duplicate_proposals == 2


def test_apply_failure_is_data_not_an_error():
    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp)
        # DEFAULT_SURFACE declares this combination as failing to start.
        out, is_error = call("evaluate_config", {
            "params": {"named_threads": 64},
            "rationale": "probing the edge"}, ctx)
        assert out["status"] == "apply_failed"
        assert is_error is False, "an apply failure is a measurement, not a fault"
        assert any("rolled" in n for n in out.get("notes", []))
        assert ctx.budget.consecutive_apply_failures == 1


def test_infra_error_is_flagged_as_an_error():
    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, fail_status="infra_error")
        _, is_error = call("evaluate_config", {
            "params": {"querylog": "no"}, "rationale": "x"}, ctx)
        assert is_error is True


def test_budget_exhaustion_blocks_further_evaluations():
    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, max_evals=2)
        for threads in (8, 16, 32):
            out, _ = call("evaluate_config", {
                "params": {"named_threads": threads}, "rationale": "sweep"}, ctx)
        assert out["status"] == "budget_exhausted"
        assert ctx.budget.evals_used == 2
        assert ctx.evaluator.calls == 2, "no evaluation may run past the budget"


def test_ceiling_and_generator_limits_are_explained():
    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp)
        ctx.evaluator.evaluate = lambda p, i, b=None, s=None: {
            "status": "ok", "max_qps_passed": 1000000, "hit_max_qps_ceiling": True,
            "apply": {"status": "ok", "exit_code": 0}, "cached": False}
        out, _ = call("evaluate_config", {"params": {}, "rationale": "x"}, ctx)
        assert any("LOWER BOUND" in n for n in out["notes"])
        # A censored result must not become the incumbent best.
        assert ctx.best_score is None


def test_best_tracks_only_usable_measurements():
    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, max_evals=10)
        call("evaluate_config", {"params": {"named_threads": 16},
                                 "rationale": "a"}, ctx)
        first = ctx.best_score
        assert first is not None
        # A generator-limited result measures the load generator, not the server.
        ctx.evaluator.evaluate = lambda p, i, b=None, s=None: {
            "status": "ok", "max_qps_passed": first + 500000,
            "generator_limited": True, "qps_fidelity_pct": 71.2,
            "apply": {"status": "ok", "exit_code": 0}, "cached": False}
        out, _ = call("evaluate_config", {"params": {"named_threads": 32},
                                          "rationale": "b"}, ctx)
        assert any("generator" in n for n in out["notes"])
        assert ctx.best_score == first, "a generator-limited score must not win"


def test_compare_configs_reports_only_differences():
    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, max_evals=10)
        a, _ = call("evaluate_config", {"params": {"named_threads": 8},
                                        "rationale": "a"}, ctx)
        b, _ = call("evaluate_config", {"params": {"named_threads": 32},
                                        "rationale": "b"}, ctx)
        out, _ = call("compare_configs", {
            "candidate_ids": [a["candidate_id"], b["candidate_id"]]}, ctx)
        assert out["differing_parameters"] == ["named_threads"]
        assert len(out["configs"]) == 2
        assert "querylog" in out["identical_parameters"]


def test_compare_configs_needs_two_known_ids():
    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp)
        out, _ = call("compare_configs", {"candidate_ids": ["nope", "also-nope"]}, ctx)
        assert "error" in out
        assert out["unknown_ids"] == ["nope", "also-nope"]


def test_hypothesis_warns_below_the_noise_floor():
    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, noise_floor=12000)
        out, _ = call("record_hypothesis", {
            "hypothesis": "tcp_clients barely matters for a UDP load",
            "params_involved": ["tcp_clients"],
            "predicted_direction": "increase",
            "predicted_magnitude_qps": 3000,
            "confidence": 0.4}, ctx)
        assert "warning" in out, "a sub-noise prediction is unmeasurable; say so"
        assert "12000" in out["warning"]

        out, _ = call("record_hypothesis", {
            "hypothesis": "more worker threads help a lot",
            "params_involved": ["named_threads"],
            "predicted_direction": "increase",
            "predicted_magnitude_qps": 80000,
            "confidence": 0.8}, ctx)
        assert "warning" not in out


def test_search_state_is_bounded():
    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, max_evals=30)
        for value in [100, 200, 300, 400, 500, 600, 700, 800, 900, 1000, 1100, 1200]:
            call("evaluate_config", {"params": {"tcp_clients": value},
                                     "rationale": "sweep"}, ctx)
        out, _ = call("get_search_state", {}, ctx)
        # Bounded by construction, so a long campaign cannot blow up the context.
        assert len(out["top_10"]) <= 10
        assert len(out["most_recent_10"]) <= 10
        assert out["total_measured"] == 12


def test_finish_records_the_conclusion():
    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, max_evals=10)
        res, _ = call("evaluate_config", {"params": {"named_threads": 16},
                                          "rationale": "a"}, ctx)
        out, _ = call("finish", {"summary": "threads dominate",
                                 "best_candidate_id": res["candidate_id"]}, ctx)
        assert out["acknowledged"] is True
        assert ctx.finished["summary"] == "threads dominate"


def test_unknown_tool_is_an_error():
    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp)
        out, is_error = call("run_bash", {"cmd": "rm -rf /"}, ctx)
        assert is_error is True
        assert "no such tool" in out["error"]


# --------------------------------------------------------------------- ledger


def test_ledger_records_every_evaluation_with_rationale():
    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, max_evals=10)
        call("evaluate_config", {"params": {"named_threads": 16},
                                 "rationale": "worker threads should dominate"}, ctx)
        rows = ctx.ledger.evaluations()
        assert len(rows) == 1
        # The audit trail for an unattended campaign is *why*, not just what.
        assert rows[0]["rationale"] == "worker threads should dominate"
        assert rows[0]["eval"]["max_qps_passed"] > 0


def test_ledger_marks_semantic_candidates():
    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, max_evals=10)
        call("evaluate_config", {"params": {"minimal_responses": "yes"},
                                 "rationale": "semantic knob"}, ctx)
        call("evaluate_config", {"params": {"querylog": "yes"},
                                 "rationale": "free knob"}, ctx)
        rows = ctx.ledger.evaluations()
        assert rows[0]["changes_semantics"] is True
        assert rows[1]["changes_semantics"] is False


def test_ledger_resume_ignores_in_flight_rows():
    with tempfile.TemporaryDirectory() as tmp:
        ledger = Ledger(tmp, "r", "grid")
        ledger.in_flight(0, "aaa", {})
        # A crash between apply and measure leaves only the in_flight row; that
        # candidate must be retried, not silently treated as done.
        assert ledger.completed() == {}
        ledger.evaluation(0, "aaa", {}, {"status": "ok", "exit_code": 0},
                          {"status": "ok", "max_qps_passed": 500000}, {}, {})
        assert "aaa" in ledger.completed()
        assert ledger.evals_spent() == 1


def test_ledger_best_skips_censored_results():
    with tempfile.TemporaryDirectory() as tmp:
        ledger = Ledger(tmp, "r", "grid")
        ledger.evaluation(0, "aaa", {}, {"status": "ok"},
                          {"status": "ok", "max_qps_passed": 500000}, {}, {})
        ledger.evaluation(1, "bbb", {}, {"status": "ok"},
                          {"status": "ok", "max_qps_passed": 900000,
                           "hit_max_qps_ceiling": True}, {}, {})
        assert ledger.best()["candidate_id"] == "aaa"


if __name__ == "__main__":
    tests = [(n, o) for n, o in sorted(globals().items())
             if n.startswith("test_") and callable(o)]
    failures = []
    for name, fn in tests:
        try:
            fn()
            print(f"  ok    {name}")
        except Exception as e:  # noqa: BLE001
            failures.append((name, e))
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
