#!/usr/bin/env python3
"""Optimizer 2: an LLM proposes configurations, measures them, and iterates.

Shares every component with ``grid_search.py`` -- the same schema, applier,
evaluator, ledger, and budget guard. The only difference is how the next
candidate gets chosen, which is what makes the comparison a comparison of search
strategies rather than of two different harnesses.

    python3 config_tuner/agent_search.py \\
        --space config_tuner/configs/space_bind_small.yaml --space-mode grid \\
        --run-dir ~/tuner_runs/agent-20260902 --max-evals 25

Offline, against the synthetic surface (real API calls, fake testbed):

    python3 config_tuner/agent_search.py --simulate --run-dir /tmp/agent-sim \\
        --space config_tuner/configs/space_bind_small.yaml --max-evals 8

Design notes worth keeping in view:

* **A hand-written loop, not an agent framework.** The tool list passed to
  ``client.messages.create`` is exactly the model's capability set, so the closed
  world is structural. Owning the loop also lets budget be checked between every
  turn and lets the countdown be injected without disturbing the prompt cache.
* **Budget updates go in as mid-conversation system messages**, appended to
  ``messages``. Putting a changing countdown in the top-level ``system`` block
  would invalidate the cached prefix on every single turn.
* **Parallel tool use is disabled.** The name server is one shared resource; two
  concurrent evaluations would race ``/etc/bind`` and interleave their load tests.
"""

import argparse
import atexit
import json
import logging
import os
import signal
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tools_spec  # noqa: E402
from tuner import schema as schema_mod  # noqa: E402
from tuner import space as space_mod  # noqa: E402
from tuner.apply import Applier, SimulatedApplier  # noqa: E402
from tuner.budget import BudgetGuard  # noqa: E402
from tuner.evaluate import EvalConfig, Evaluator  # noqa: E402
from tuner.ledger import Ledger  # noqa: E402
from tuner.simulate import DEFAULT_SURFACE, ResponseSurface  # noqa: E402

log = logging.getLogger("agent_search")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))


def build_parser():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--tuner-config", default=os.path.join(HERE, "configs", "tuner.yaml"))
    p.add_argument("--eval-config")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--space", help="restrict the agent to a grid space")
    p.add_argument("--space-mode", choices=["grid", "full"], default="full",
                   help="grid narrows the tool schema to exactly the grid's values, "
                        "so both optimizers search an identical set; full gives the "
                        "agent the whole schema range")
    p.add_argument("--max-evals", type=int)
    p.add_argument("--max-hours", type=float)
    p.add_argument("--model")
    p.add_argument("--seed-label", default="", help="tag for this trajectory, so "
                                                    "repeated agent runs are distinguishable")
    p.add_argument("--simulate", nargs="?", const="__default__", metavar="SURFACE_YAML")
    p.add_argument("--no-baseline", action="store_true",
                   help="skip the automatic baseline evaluation (it normally runs first)")
    return p


def load_tuner_config(path):
    import yaml
    with open(os.path.expanduser(path)) as f:
        return yaml.safe_load(f) or {}


def render_system_prompt(schema, cfg, eval_cfg, space, facts, budget, noise_floor):
    with open(os.path.join(HERE, "prompts", "system.md")) as f:
        template = f.read()

    msq = eval_cfg.msq
    min_step = int(msq.get("min_qps_step", 10000))
    if noise_floor:
        noise_guidance = (
            f"Repeated measurements of an identical configuration differ by about "
            f"+/-{noise_floor} QPS (one standard deviation), measured on this "
            f"testbed. Treat any difference smaller than that as no difference."
        )
    else:
        noise_guidance = (
            f"The noise floor has not been calibrated on this testbed yet. Until it "
            f"is, treat the {min_step} QPS measurement resolution as the smallest "
            f"difference you can believe, and be sceptical of anything close to it."
        )

    facts = facts or {}
    values = {
        "min_passes": msq.get("min_passes", 4),
        "num_trials": msq.get("num_trials", 5),
        "answer_rate_threshold": msq.get("answer_rate_threshold", 99.0),
        "trial_duration": msq.get("trial_duration", 10),
        "min_qps_step": min_step,
        "nproc": facts.get("nproc", "an unknown number of"),
        "bind_version": facts.get("bind_version", "BIND 9"),
        "num_clients": len(cfg.get("hosts", {}).get("clients", [])) or 1,
        "tool": eval_cfg.tool,
        "tunables_table": schema_mod.describe_table(schema, space, facts),
        "max_evals": budget.max_evals,
        "max_minutes": int(budget.max_wall_clock_s / 60),
        "noise_guidance": noise_guidance,
    }
    for key, value in values.items():
        template = template.replace("{" + key + "}", str(value))
    return template


def main(argv=None):
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    try:
        import anthropic
    except ImportError:
        raise SystemExit(
            "the agent optimizer needs the Anthropic SDK:\n"
            "    pip install -r config_tuner/requirements.txt"
        )

    cfg = load_tuner_config(args.tuner_config)
    simulate = args.simulate is not None

    schema = schema_mod.load_schema()
    facts = schema_mod.load_facts(cfg.get("paths", {}).get("facts"))

    space = None
    if args.space and args.space_mode == "grid":
        # Narrowing the tool schema to exactly the grid's values is what makes
        # "the agent needed fewer evaluations" a claim about search rather than
        # about one optimizer having had a bigger space.
        space, _ = space_mod.load_space(args.space, schema, facts)
        log.info("Space mode 'grid': the agent is restricted to %d axes, "
                 "identical to the grid baseline", len(space))

    eval_config_path = args.eval_config or cfg.get("paths", {}).get("eval_config")
    if simulate and not eval_config_path:
        eval_config_path = os.path.join(REPO, "load_testing_benchmark", "configs",
                                        "binary_testing.yaml")
    if not eval_config_path:
        raise SystemExit("no --eval-config and none set in the tuner config")

    run_dir = os.path.abspath(os.path.expanduser(args.run_dir))
    run_id = os.path.basename(run_dir)
    ledger = Ledger(run_dir, run_id, "agent")

    budget_cfg = dict(cfg.get("budget", {}))
    if args.max_evals is not None:
        budget_cfg["max_evals"] = args.max_evals
    if args.max_hours is not None:
        budget_cfg["max_wall_clock_minutes"] = int(args.max_hours * 60)
    budget = BudgetGuard(run_dir, **budget_cfg)

    surface = None
    if simulate:
        surface = (ResponseSurface(DEFAULT_SURFACE) if args.simulate == "__default__"
                   else ResponseSurface.from_file(args.simulate))
        applier = SimulatedApplier(fail_on=surface.fail_to_start)
    else:
        applier = Applier(
            cfg["hosts"]["server"],
            staging_remote=cfg.get("paths", {}).get(
                "staging_remote", "/var/lib/dns-tuner/staging/candidate.json"),
            apply_cmd=cfg.get("paths", {}).get(
                "apply_cmd", "sudo /usr/local/sbin/dns_tuner_apply"),
        )

    eval_kwargs = dict(cfg.get("evaluation", {}))
    eval_kwargs.pop("estimated_minutes", None)
    eval_cfg = EvalConfig(eval_config_path,
                          server=cfg.get("hosts", {}).get("server"),
                          clients=cfg.get("hosts", {}).get("clients", []),
                          **eval_kwargs)
    evaluator = Evaluator(applier, eval_cfg, run_dir, schema, facts)

    agent_cfg = cfg.get("agent", {})
    model = args.model or agent_cfg.get("model", "claude-opus-5")
    noise_floor = agent_cfg.get("noise_floor_qps")
    min_step = int(eval_cfg.msq.get("min_qps_step", 10000))

    ctx = tools_spec.ToolContext(schema, evaluator, ledger, budget, facts,
                                 noise_floor, surface, min_step)
    tools = tools_spec.build_tools(schema, space, facts)
    system_prompt = render_system_prompt(schema, cfg, eval_cfg, space, facts,
                                         budget, noise_floor)

    ledger.run_start({
        "argv": sys.argv, "model": model, "space_mode": args.space_mode,
        "space": {k: list(v) for k, v in space.items()} if space else None,
        "simulated": simulate, "seed_label": args.seed_label,
        "schema_sha": schema_mod.schema_sha(),
        "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })

    def restore_baseline():
        if not simulate:
            try:
                applier.baseline()
            except Exception as e:  # noqa: BLE001
                log.warning("Baseline restore on exit failed: %s", e)

    atexit.register(restore_baseline)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: sys.exit(130))

    # ------------------------------------------------------- measure the baseline
    # Always first, and charged to the agent's budget exactly as it is to the
    # grid's. Every later result is reported as a delta against it, and without
    # it the agent has no reference point for its first proposal.
    if not args.no_baseline:
        log.info("Measuring the baseline before handing control to the model")
        defaults = dict(schema_mod.defaults(schema))
        payload, _ = tools_spec.handle("evaluate_config", {
            "params": defaults,
            "rationale": "Baseline: every parameter at its schema default. "
                         "Measured by the harness before the campaign begins.",
        }, ctx)
        log.info("Baseline: %s", payload)

    client = anthropic.Anthropic()
    transcript_path = os.path.join(run_dir, "transcript.jsonl")
    messages = [{
        "role": "user",
        "content": (
            "Begin. Your search state, including the measured baseline, is below.\n\n"
            + tools_spec.handle("get_search_state", {}, ctx)[0]
        ),
    }]

    stop_reason = "completed"

    while True:
        if not budget.may_continue():
            stop_reason = budget.stopped_reason
            log.warning("Stopping: %s", stop_reason)
            break

        try:
            response = client.messages.create(
                model=model,
                max_tokens=agent_cfg.get("max_tokens", 16000),
                system=[{"type": "text", "text": system_prompt,
                         "cache_control": {"type": "ephemeral"}}],
                thinking={"type": "adaptive", "display": "summarized"},
                output_config={"effort": agent_cfg.get("effort", "high")},
                tools=tools,
                # One evaluation at a time: the name server is a single shared
                # resource and concurrent applies would race each other.
                tool_choice={"type": "auto", "disable_parallel_tool_use": True},
                messages=messages,
            )
        except anthropic.APIStatusError as e:
            log.error("API error (%s): %s", getattr(e, "status_code", "?"), e)
            stop_reason = f"api_error: {e}"
            break
        except anthropic.APIConnectionError as e:
            log.error("API connection failed: %s", e)
            stop_reason = "api_connection_error"
            break

        budget.record_model_turn(response.usage)
        with open(transcript_path, "a") as f:
            f.write(json.dumps({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "stop_reason": response.stop_reason,
                "content": [b.model_dump() for b in response.content],
                "usage": response.usage.model_dump(),
            }, default=str) + "\n")

        tool_names = [b.name for b in response.content if b.type == "tool_use"]
        ledger.model_turn(stop_reason=response.stop_reason, tools=tool_names,
                          usage=response.usage.model_dump(),
                          cost_usd=round(budget.api_cost_usd, 4))

        for block in response.content:
            if block.type == "text" and block.text.strip():
                log.info("model: %s", block.text.strip()[:400])

        # Echo the assistant turn back unchanged, thinking blocks included --
        # the API needs them intact to continue the same reasoning thread.
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            log.info("Model ended its turn without a tool call; prompting once more")
            if ctx.finished:
                break
            messages.append({
                "role": "user",
                "content": "Continue, or call finish if you are done.",
            })
            continue

        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            log.info("tool: %s", block.name)
            content, is_error = tools_spec.handle(block.name, block.input, ctx)
            results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": content,
                "is_error": is_error,
            })

        # All tool results go back in a single user message.
        messages.append({"role": "user", "content": results})

        if ctx.finished:
            stop_reason = "model called finish"
            log.info("Model finished: %s", ctx.finished.get("summary", "")[:500])
            break

        # The countdown goes here rather than in the system block, so the cached
        # prefix survives. Placement rule: a mid-conversation system message must
        # follow a user turn and be the last entry, which it is.
        messages.append({
            "role": "system",
            "content": (f"{budget.evals_remaining} evaluations and "
                        f"{round(budget.minutes_remaining)} minutes remain."),
        })

        if budget.fatal:
            stop_reason = budget.stopped_reason
            log.error("HALTING: %s", stop_reason)
            break

    ledger.run_end(stop_reason, best={
        "params": ctx.results.get(ctx.best_id, {}).get("params") if ctx.best_id else None,
        "max_qps_passed": ctx.best_score,
        "candidate_id": ctx.best_id,
        "model_summary": (ctx.finished or {}).get("summary"),
    })
    csv_path = ledger.export_csv()

    log.info("=== agent search finished: %s ===", stop_reason)
    log.info("Baseline: %s QPS | Best: %s QPS", ctx.baseline_score, ctx.best_score)
    if ctx.baseline_score and ctx.best_score:
        log.info("Uplift: %+d QPS (%.1f%%)", ctx.best_score - ctx.baseline_score,
                 100.0 * (ctx.best_score - ctx.baseline_score) / ctx.baseline_score)
    log.info("Evaluations spent: %d | turns: %d | API cost: $%.2f | wall clock: %.1f min",
             budget.evals_used, budget.model_turns, budget.api_cost_usd,
             budget.elapsed_s / 60)
    log.info("Ledger: %s", ledger.path)
    if csv_path:
        log.info("CSV: %s", csv_path)

    if simulate and surface and space:
        candidates = space_mod.enumerate_full(space, schema, {}, facts)
        _, truth = surface.true_optimum(candidates)
        log.info("Surface optimum (noise-free): %s QPS", truth)
        if ctx.best_score:
            log.info("Found %.1f%% of the true optimum", 100.0 * ctx.best_score / truth)

    return 0


if __name__ == "__main__":
    sys.exit(main())
