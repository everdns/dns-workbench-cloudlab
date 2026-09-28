#!/usr/bin/env python3
"""Agent-driven search over BIND options and host tuning, scored by max
sustainable QPS.

Instead of walking a fixed grid, each next configuration is chosen by Claude:

  1. Pass the search space and every result recorded so far to the agent.
  2. The agent proposes the configuration it expects to do best (or stops).
     Proposals outside the bounds, that fail to render, or that were already
     measured are sent back to it to correct.
  3. Install, measure and restore the point exactly as grid_search.py does
     (search.Evaluator), record the row with the agent's reasoning, and repeat
     until --max-configs configurations have been tested.

The run starts from one seed point (every parameter at its first value) unless
--agent-seed none. The seed, config_failed and timed-out points all count
toward --max-configs, as do rows kept by --resume.

agent_search.yaml holds the base search config (hosts, tool, dns service,
max_sustainable_qps), the ``agent`` settings, and the parameters with the
bounds the agent may choose within (see agent_proposer.py).

Requires the Anthropic SDK and credentials:

    pip install anthropic
    export ANTHROPIC_API_KEY=...

    python3 agent_search.py --max-configs 15
    python3 agent_search.py --dry-run          # show one proposal, measure nothing
"""
import argparse
import logging
import os
import sys
import time

import system_config
from agent_proposer import (
    DEFAULT_MODEL,
    AgentProposalError,
    AgentProposer,
    build_search_space,
)
from bind_config import install_options, load_base_options, render_options
from search import (
    OPTIMIZATION_DIR,
    SCRIPT_NAME,
    Evaluator,
    load_grid,
    point_id,
    previous_rows,
    restore_or_exit,
    row_qps,
    split_overrides,
)
from system_config import SystemConfigError

log = logging.getLogger("agent_search")

DEFAULT_CONFIG = os.path.join(OPTIMIZATION_DIR, "agent_search.yaml")
RESULTS_NAME = "agent_results"
DEFAULT_MAX_CONFIGS = 20
EFFORTS = ["low", "medium", "high", "xhigh", "max"]

# Settings from the base config that tell the agent how points are scored.
CONTEXT_KEYS = ["dns_service", "tool", "threads", "ports_per_thread",
                "max_sustainable_qps"]


def best_of(rows):
    """(point_id, qps) of the highest-scoring row, or None."""
    best = None
    for row in rows:
        qps = row_qps(row)
        if qps is not None and (best is None or qps > best[1]):
            best = (row["point_id"], qps)
    return best


def run_agent(evaluator, proposer, parameters, max_configs, seed=True):
    """Measure agent-proposed points until ``max_configs`` configurations have
    been tested or the agent stops. Returns (point_id, qps) of the best, or
    None if no point produced a usable result."""

    def tested():
        return len(evaluator.rows)

    if seed and tested() < max_configs:
        start = {p["name"]: p["values"][0] for p in parameters}
        evaluator.evaluate(start, f"[seed {tested() + 1}/{max_configs}]", {
            "iteration": 0,
            "agent_reasoning": "seed: every parameter at its first listed value",
        })

    iteration = 0
    while tested() < max_configs:
        iteration += 1
        rows = list(evaluator.rows.values())
        best = best_of(rows)
        remaining = max_configs - tested()
        log.info("=== Asking agent for configuration %d/%d (best so far: %s) ===",
                 tested() + 1, max_configs, best)
        try:
            config, reasoning, stop = proposer.propose(rows, best, remaining)
        except AgentProposalError as e:
            log.error("Stopping: %s", e)
            break
        log.info("Agent: %s", reasoning)
        if stop:
            log.info("Agent chose to stop with %d configuration(s) left", remaining)
            break

        before = tested()
        evaluator.evaluate(config, f"[agent {before + 1}/{max_configs}]", {
            "iteration": iteration,
            "agent_reasoning": reasoning,
        })
        if tested() == before:
            # The proposer rejects measured points, so this should not happen;
            # stop rather than loop without spending the budget.
            log.error("Proposal %s was already recorded; stopping", point_id(config))
            break

    return best_of(evaluator.rows.values())


def main():
    parser = argparse.ArgumentParser(
        description="Agent-driven search over BIND options and host tuning, "
                    "scored by max sustainable QPS"
    )
    parser.add_argument("--config", default=DEFAULT_CONFIG,
                        help="YAML defining the parameters, agent settings and "
                             "the search config")
    parser.add_argument("--max-configs", type=int,
                        help="Most configurations to test, seed and failed "
                             "points included (default: agent.max_configs in "
                             f"the config, else {DEFAULT_MAX_CONFIGS})")
    parser.add_argument("--agent-model",
                        help=f"Claude model (default: agent.model, else {DEFAULT_MODEL})")
    parser.add_argument("--agent-effort", choices=EFFORTS,
                        help="Claude effort level (default: agent.effort, else high)")
    parser.add_argument("--agent-seed", choices=["first", "none"], default="first",
                        help="first: measure every parameter at its first value "
                             "before asking the agent. none: let the agent "
                             "choose from the start")
    parser.add_argument("--agent-retries", type=int, default=3,
                        help="Attempts per step to get a usable proposal")
    parser.add_argument("--dns-service",
                        help="DNS service to evaluate (overrides the config)")
    parser.add_argument("--server", help="Name server host (overrides the config)")
    parser.add_argument("--output-dir", default="results",
                        help="Output directory for results")
    parser.add_argument("--point-timeout", type=int, default=21600,
                        help="Seconds to allow one evaluation before moving on")
    parser.add_argument("--resume", action="store_true",
                        help="Keep points already recorded by a previous agent "
                             "search; they count toward --max-configs")
    parser.add_argument("--dry-run", action="store_true",
                        help="Ask the agent for one configuration, print it, "
                             "and stop")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = load_grid(args.config)
    agent_settings = config.get("agent") or {}
    max_configs = (args.max_configs if args.max_configs is not None
                   else agent_settings.get("max_configs", DEFAULT_MAX_CONFIGS))
    if max_configs < 1:
        parser.error("--max-configs must be >= 1")
    if args.agent_retries < 1:
        parser.error("--agent-retries must be >= 1")
    model = args.agent_model or agent_settings.get("model", DEFAULT_MODEL)
    effort = args.agent_effort or agent_settings.get("effort", "high")

    parameters = config["parameters"]
    if args.dns_service:
        config["dns_service"] = args.dns_service
    if args.server:
        config.setdefault("hosts", {})["server"] = args.server

    dns_service = config.get("dns_service", "ns_bind")
    search_output_dir = args.output_dir if os.path.isabs(args.output_dir) else os.path.join(OPTIMIZATION_DIR, args.output_dir)

    server = config.get("hosts", {}).get("server")
    if not server:
        log.error("No name server host configured")
        return 2

    try:
        space = build_search_space(parameters)
    except ValueError as e:
        log.error("%s: %s", args.config, e)
        return 2

    log.info("=== Agent search over %d parameter(s), at most %d configuration(s), "
             "model %s (effort %s) ===", len(parameters), max_configs, model, effort)
    for spec in space:
        bounds = (spec["choices"] if spec["type"] == "enum"
                  else f"{spec['type']} [{spec['min']}, {spec['max']}]"
                       + (" or default" if spec["allow_default"] else ""))
        log.info("  %s: %s", spec["name"], bounds)
    log.info("Name server: %s, service: %s", server, dns_service)

    proposer = AgentProposer(
        space,
        context={key: config[key] for key in CONTEXT_KEYS if key in config},
        model=model,
        effort=effort,
        max_retries=args.agent_retries,
        transcript_path=os.path.join(search_output_dir, SCRIPT_NAME,
                                     "agent_transcript.jsonl"),
    )
    base_text = load_base_options()

    if args.dry_run:
        try:
            overrides, reasoning, stop = proposer.propose([], None, max_configs)
        except AgentProposalError as e:
            log.error("%s", e)
            return 1
        log.info("Agent: %s", reasoning)
        if stop:
            log.info("Agent chose to stop before measuring anything")
            return 0
        print(f"--- {point_id(overrides)} ---")
        bind_overrides, system_overrides = split_overrides(overrides)
        install_options(server, render_options(base_text, bind_overrides),
                        dry_run=True)
        system_config.apply(server, system_overrides, dry_run=True)
        return 0

    resumed = previous_rows(search_output_dir, f"{RESULTS_NAME}.csv") if args.resume else []
    if resumed:
        log.info("Resuming: %d point(s) already recorded", len(resumed))

    system_baseline = None
    if any(system_config.is_system_parameter(p["name"]) for p in parameters):
        try:
            system_baseline = system_config.snapshot(server)
        except SystemConfigError as e:
            log.error("Could not snapshot host settings on %s: %s", server, e)
            return 2

    evaluator = Evaluator(config, server, base_text, system_baseline,
                          search_output_dir, RESULTS_NAME, args.point_timeout,
                          resumed)
    started = time.time()

    try:
        best = run_agent(evaluator, proposer, parameters, max_configs,
                         seed=args.agent_seed == "first")
    finally:
        path = evaluator.export()
        if path:
            log.info("Results written to %s", path)
        restore_or_exit(server, base_text, system_baseline)

    log.info("=== Agent search finished in %.1fs: %d configuration(s) tested ===",
             time.time() - started, len(evaluator.rows))
    if best:
        log.info("Best: %s at %d QPS", best[0], best[1])
    else:
        log.warning("No point produced a usable result")
    return 0


if __name__ == "__main__":
    sys.exit(main())
