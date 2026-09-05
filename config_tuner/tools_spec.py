#!/usr/bin/env python3
"""The five tools the agent gets, and the handlers behind them.

This list *is* the model's entire causal footprint on the world. There are no
built-in tools -- no shell, no file read or write, no way to reach the schema,
the sudoers file, or its own budget. That is deliberate, and it is why this
project uses the plain Anthropic SDK rather than a batteries-included agent
framework: with ``client.messages.create`` the tool list is exactly what you
pass, so the closed world is structural rather than something configured by
subtracting capabilities from a larger default set.

Only one tool mutates anything, and its arguments are validated three times
before they can reach ``/etc``: by the API (``strict: true`` against a schema
generated from ``tunables.yaml``), by this process, and again by the name server
against its own root-owned copy of the schema.

Failure modes are split on purpose. An apply failure, a conformance failure, a
zero score, or a generator-limited measurement all come back with
``is_error: false`` -- they are legitimate observations the model should reason
about. Only harness faults use ``is_error: true``.
"""

import json

from tuner import schema as schema_mod


def build_tools(schema, space=None, facts=None):
    """Tool definitions, with keys sorted so the prompt cache prefix is stable.

    Tools render before the system prompt in the cache prefix, so any instability
    here -- a timestamp, an unsorted dict -- silently invalidates the cache on
    every turn and multiplies the cost of a campaign.
    """
    return [
        {
            "name": "evaluate_config",
            "description": (
                "Apply a BIND configuration to the authoritative name server and "
                "measure its maximum sustainable QPS. This is the only tool that "
                "changes anything, and the only one that costs budget: one "
                "evaluation, unless this exact configuration was measured before, "
                "in which case the cached result is returned for free. Takes "
                "roughly 25 minutes. Every parameter must be specified."
            ),
            "strict": True,
            "input_schema": schema_mod.to_json_schema(schema, space, facts),
        },
        {
            "name": "compare_configs",
            "description": (
                "Put two or more already-measured configurations side by side, "
                "highlighting which parameters differ and what each scored. Free, "
                "and the way to turn a set of measurements into an ablation "
                "rather than re-measuring something you have already seen."
            ),
            "strict": True,
            "input_schema": {
                "type": "object",
                "properties": {
                    "candidate_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 2,
                        "maxItems": 8,
                        "description": "candidate_id values from earlier results.",
                    },
                },
                "required": ["candidate_ids"],
                "additionalProperties": False,
            },
        },
        {
            "name": "record_hypothesis",
            "description": (
                "State what you expect an evaluation to show, before you spend it. "
                "Free. Call this before each evaluate_config. It commits you to a "
                "falsifiable prediction, and the record of predicted-versus-measured "
                "is a result in its own right."
            ),
            "strict": True,
            "input_schema": {
                "type": "object",
                "properties": {
                    "hypothesis": {
                        "type": "string",
                        "maxLength": 500,
                        "description": "What you believe and why.",
                    },
                    "params_involved": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Parameter names this prediction is about.",
                    },
                    "predicted_direction": {
                        "type": "string",
                        "enum": ["increase", "decrease", "no_effect"],
                    },
                    "predicted_magnitude_qps": {
                        "type": "integer",
                        "description": "Expected absolute change in QPS. Compare "
                                       "this against the noise floor before predicting.",
                    },
                    "confidence": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 1,
                    },
                },
                "required": ["hypothesis", "params_involved", "predicted_direction",
                             "predicted_magnitude_qps", "confidence"],
                "additionalProperties": False,
            },
        },
        {
            "name": "get_search_state",
            "description": (
                "Everything measured so far, plus remaining budget. Free. The same "
                "information is pushed to you after each evaluation, so you only "
                "need this to look further back than the recent history."
            ),
            "strict": True,
            "input_schema": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        },
        {
            "name": "finish",
            "description": (
                "End the campaign and report your conclusion. Call this when "
                "further evaluations would not be a good use of the remaining "
                "budget. Finishing deliberately is a better outcome than being cut "
                "off, and the difference is recorded."
            ),
            "strict": True,
            "input_schema": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "maxLength": 2000,
                        "description": "What you found, what mattered, what did not, "
                                       "and what you would test next.",
                    },
                    "best_candidate_id": {
                        "type": "string",
                        "description": "The configuration you recommend.",
                    },
                },
                "required": ["summary", "best_candidate_id"],
                "additionalProperties": False,
            },
        },
    ]


class ToolContext:
    """Everything the handlers need. The model can reach none of it directly."""

    def __init__(self, schema, evaluator, ledger, budget, facts=None,
                 noise_floor=None, surface=None, min_qps_step=10000):
        self.schema = schema
        self.evaluator = evaluator
        self.ledger = ledger
        self.budget = budget
        self.facts = facts
        self.noise_floor = noise_floor
        self.surface = surface
        self.min_qps_step = min_qps_step
        self.eval_index = 0
        self.results = {}         # candidate_id -> compact record
        self.baseline_score = None
        self.best_score = None
        self.best_id = None
        self.finished = None
        self.pending_hypothesis = None
        self.seen_proposals = set()


def handle(name, tool_input, ctx):
    """Dispatch one tool call. Returns (content string, is_error)."""
    handler = {
        "evaluate_config": _evaluate_config,
        "compare_configs": _compare_configs,
        "record_hypothesis": _record_hypothesis,
        "get_search_state": _get_search_state,
        "finish": _finish,
    }.get(name)
    if handler is None:
        return json.dumps({"error": f"no such tool: {name}"}), True
    return handler(tool_input, ctx)


def _evaluate_config(tool_input, ctx):
    params = tool_input.get("params", {})
    rationale = tool_input.get("rationale", "")

    # Validate before spending anything. A schema violation is a correctable
    # mistake, not a wasted evaluation, so it costs no budget.
    try:
        canon = schema_mod.canonical(ctx.schema, params, ctx.facts)
        cid = schema_mod.candidate_id(ctx.schema, canon, ctx.facts)
    except schema_mod.SchemaError as e:
        return json.dumps({
            "status": "invalid_config",
            "error": str(e),
            "note": "No budget was spent. Correct the values and try again.",
            "evals_remaining": ctx.budget.evals_remaining,
        }), False

    reason = ctx.budget.why_stopped()
    if reason:
        return json.dumps({
            "status": "budget_exhausted", "reason": reason,
            "note": "No further evaluations are possible. Call finish.",
        }), False

    if cid in ctx.seen_proposals:
        ctx.budget.record_duplicate()
    ctx.seen_proposals.add(cid)

    ctx.ledger.in_flight(ctx.eval_index, cid, dict(canon), rationale)

    simulate_score = None
    if ctx.surface is not None:
        simulate_score = ctx.surface.score(canon)

    result = ctx.evaluator.evaluate(dict(canon), ctx.eval_index,
                                    ctx.best_score, simulate_score)
    apply_result = result.get("apply", {})
    ctx.budget.record_evaluation(result.get("status"), result.get("cached", False),
                                 apply_result.get("exit_code"))

    ctx.ledger.evaluation(
        eval_index=ctx.eval_index, candidate_id=cid, params=dict(canon),
        apply_result=apply_result,
        eval_result={k: v for k, v in result.items() if k != "apply"},
        budget_snapshot=ctx.budget.snapshot(),
        provenance={"tool": ctx.evaluator.cfg.tool,
                    "simulated": ctx.surface is not None},
        rationale=rationale,
        cached=result.get("cached", False),
        changes_semantics=schema_mod.touches_semantics(ctx.schema, canon),
    )
    ctx.ledger.export_csv()
    ctx.eval_index += 1

    score = result.get("max_qps_passed")
    usable = (result.get("status") == "ok" and score is not None
              and not result.get("hit_max_qps_ceiling")
              and not result.get("generator_limited"))

    if ctx.baseline_score is None and usable:
        defaults = schema_mod.defaults(ctx.schema)
        if schema_mod.candidate_id(ctx.schema, defaults, ctx.facts) == cid:
            ctx.baseline_score = score
    if usable and (ctx.best_score is None or score > ctx.best_score):
        ctx.best_score, ctx.best_id = score, cid

    ctx.results[cid] = {
        "candidate_id": cid, "params": dict(canon), "score": score,
        "status": result.get("status"),
        "changes_semantics": schema_mod.touches_semantics(ctx.schema, canon),
    }

    # Harness faults are errors the model cannot act on; everything else is a
    # measurement it should reason about.
    is_error = result.get("status") in ("infra_error", "timeout", "parse_error",
                                        "tuner_bug", "fatal")

    payload = {
        "status": result.get("status"),
        "candidate_id": cid,
        "max_qps_passed": score,
        "cached": result.get("cached", False),
        "eval_seconds": result.get("eval_seconds"),
        "evals_used": ctx.budget.evals_used,
        "evals_remaining": ctx.budget.evals_remaining,
        "minutes_remaining": round(ctx.budget.minutes_remaining),
    }
    if score is not None and ctx.best_score is not None:
        payload["delta_vs_best"] = score - ctx.best_score
    if score is not None and ctx.baseline_score is not None:
        payload["delta_vs_baseline"] = score - ctx.baseline_score
    if ctx.noise_floor:
        payload["noise_floor_qps"] = ctx.noise_floor

    notes = []
    if result.get("hit_max_qps_ceiling"):
        notes.append("Hit the search ceiling: this score is a LOWER BOUND, not a "
                     "maximum. It cannot be ranked against uncensored results.")
    if result.get("generator_limited"):
        notes.append(f"The load generator only sent "
                     f"{result.get('qps_fidelity_pct')}% of the requested queries. "
                     "This measures the generator, not the server; do not treat it "
                     "as a good configuration.")
    if result.get("status") == "no_passing_level":
        notes.append("A genuine zero: the server sustained no tested QPS level. "
                     "This is a real measurement, not a malfunction.")
    if result.get("status") == "apply_failed":
        notes.append(f"The configuration was rejected or would not start: "
                     f"{apply_result.get('description')}. The name server rolled "
                     "itself back and is healthy.")
    if result.get("cached"):
        notes.append("Already measured earlier; returned from cache at no cost.")
    if ctx.pending_hypothesis:
        payload["hypothesis_under_test"] = ctx.pending_hypothesis.get("hypothesis")
        ctx.pending_hypothesis = None
    if notes:
        payload["notes"] = notes

    return json.dumps(payload, default=str), is_error


def _compare_configs(tool_input, ctx):
    ids = tool_input.get("candidate_ids", [])
    known = [ctx.results[i] for i in ids if i in ctx.results]
    missing = [i for i in ids if i not in ctx.results]
    if len(known) < 2:
        return json.dumps({
            "error": "need at least two measured configurations",
            "unknown_ids": missing,
            "known_ids": sorted(ctx.results),
        }), False

    # Report only what differs; listing sixteen identical parameters per row
    # buries the one that actually changed.
    all_names = [p["name"] for p in ctx.schema["params"]]
    differing = [n for n in all_names
                 if len({json.dumps(r["params"].get(n)) for r in known}) > 1]

    return json.dumps({
        "differing_parameters": differing,
        "identical_parameters": [n for n in all_names if n not in differing],
        "configs": [{
            "candidate_id": r["candidate_id"],
            "max_qps_passed": r["score"],
            "status": r["status"],
            "changes_semantics": r["changes_semantics"],
            "differs_by": {n: r["params"].get(n) for n in differing},
        } for r in known],
        "unknown_ids": missing,
        "noise_floor_qps": ctx.noise_floor,
        "note": ("Differences smaller than the noise floor are not evidence."
                 if ctx.noise_floor else
                 f"Scores are quantized to {ctx.min_qps_step} QPS; smaller "
                 "differences are not measurable."),
    }, default=str), False


def _record_hypothesis(tool_input, ctx):
    ctx.pending_hypothesis = dict(tool_input)
    ctx.ledger.hypothesis(**tool_input)

    payload = {"recorded": True}
    magnitude = tool_input.get("predicted_magnitude_qps", 0)
    floor = ctx.noise_floor or ctx.min_qps_step
    if (tool_input.get("predicted_direction") != "no_effect"
            and abs(magnitude) < floor):
        payload["warning"] = (
            f"You predicted a {magnitude} QPS change, which is below the "
            f"{floor} QPS resolution of the measurement. Even if you are right, "
            "the experiment cannot show it. Consider a bolder change."
        )
    return json.dumps(payload), False


def _get_search_state(tool_input, ctx):
    rows = sorted(ctx.results.values(),
                  key=lambda r: (r["score"] is None, -(r["score"] or 0)))
    return json.dumps({
        "baseline_qps": ctx.baseline_score,
        "best_qps": ctx.best_score,
        "best_candidate_id": ctx.best_id,
        "noise_floor_qps": ctx.noise_floor,
        "measurement_resolution_qps": ctx.min_qps_step,
        "evals_used": ctx.budget.evals_used,
        "evals_remaining": ctx.budget.evals_remaining,
        "minutes_remaining": round(ctx.budget.minutes_remaining),
        # Bounded so a long campaign cannot blow up the context window.
        "top_10": rows[:10],
        "most_recent_10": list(ctx.results.values())[-10:],
        "total_measured": len(ctx.results),
    }, default=str), False


def _finish(tool_input, ctx):
    ctx.finished = dict(tool_input)
    return json.dumps({
        "acknowledged": True,
        "best_qps": ctx.best_score,
        "best_candidate_id": ctx.best_id,
        "evals_used": ctx.budget.evals_used,
    }), False
