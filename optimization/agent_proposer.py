"""Claude-backed proposer for agent_search.py.

Each call to AgentProposer.propose() sends the search space and every result
recorded so far to Claude and gets back the next configuration to measure, as
JSON constrained by a schema built from the search space. Proposals are
checked against the parameter bounds and dry-rendered through the same
renderers bind_config.py and system_config.py use, so a bad value is sent back
to the agent to fix instead of reaching the name server.

Search space: each entry in the config's ``parameters`` list may carry an
``agent:`` block describing what the agent may propose.

    - name: rmem
      values: [default, 16777216]            # shown to the agent as hints
      agent: {type: int, min: 212992, max: 268435456, allow_default: true}
    - name: netdev-budget
      values: [default, "600/4000"]
      agent: {type: pair, min: [1, 1], max: [100000, 1000000], allow_default: true}

``type`` is one of int, pair ("<a>/<b>", each part bounded), or enum (the
default when there is no ``agent:`` block; choices are ``values`` unless
``choices`` is given). ``allow_default`` defaults to whether "default" is in
``values``.
"""
import json
import logging
import os
import time

import bind_config
import system_config
from bind_config import BindConfigError
from search import point_id
from system_config import SystemConfigError

log = logging.getLogger("agent_proposer")

DEFAULT = system_config.DEFAULT
DEFAULT_MODEL = "claude-opus-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# Per-row fields shown to the agent, besides the parameter values.
RESULT_FIELDS = [
    "status", "error", "max_qps_passed", "max_qps_tested",
    "hit_max_qps_ceiling", "min_qps_fidelity_pct", "agent_reasoning",
]
MAX_TEXT = 600

SYSTEM_PROMPT = """\
You are tuning an authoritative DNS name server (BIND plus Linux host network \
settings) for the highest max sustainable QPS: the highest query rate at which \
the server still answers nearly every query, as measured by a load generator \
on separate client machines.

Every configuration you propose is installed on the real server and measured, \
which takes many minutes, so the number of measurements is small and fixed. \
Use them well: form hypotheses about what limits throughput (socket buffers, \
packet drops in conntrack or the backlog, NAPI budget, file descriptors, EDNS \
size), test the ones that could matter most, and exploit what the results \
show. Parameters interact, so changing several at once is fine when you have a \
reason to.

Reading the results:
- max_qps_passed is the score. Higher is better.
- hit_max_qps_ceiling=True means the search hit its QPS ceiling, so the score \
is a lower bound rather than the server's limit.
- A min_qps_fidelity_pct below the configured threshold means the load \
generator could not send the target rate, so that score reflects the client, \
not the server.
- status config_failed means the configuration could not be applied (see \
error); timeout or failed means the measurement did not finish.
- Runs are noisy. Differences of a few percent may not be real.

Never propose a configuration that has already been measured. Set stop=true \
only if you are confident further measurements cannot beat the best result; \
still fill in configuration with your best guess when stopping."""


class AgentProposalError(Exception):
    pass


# ---------------------------------------------------------------------------
# Search space
# ---------------------------------------------------------------------------

def _is_default(value):
    return isinstance(value, str) and value.strip().lower() == DEFAULT


def build_search_space(parameters):
    """Per-parameter specs from the config's ``parameters`` list."""
    space = []
    for p in parameters:
        agent = dict(p.get("agent") or {})
        kind = agent.get("type", "enum")
        spec = {
            "name": p["name"],
            "type": kind,
            "hints": list(p.get("values") or []),
            "description": agent.get("description", ""),
        }
        if kind == "enum":
            spec["choices"] = list(agent.get("choices") or p["values"])
        elif kind in ("int", "pair"):
            if "min" not in agent or "max" not in agent:
                raise ValueError(f"parameter '{p['name']}': agent type {kind} "
                                 "needs min and max")
            spec["min"], spec["max"] = agent["min"], agent["max"]
            spec["allow_default"] = agent.get(
                "allow_default", any(_is_default(v) for v in spec["hints"]))
            if kind == "pair" and not (len(spec["min"]) == len(spec["max"]) == 2):
                raise ValueError(f"parameter '{p['name']}': pair min/max must "
                                 "be two-element lists")
        else:
            raise ValueError(f"parameter '{p['name']}': unknown agent type {kind!r}")
        space.append(spec)
    return space


def _value_schema(spec):
    if spec["type"] == "enum":
        return {"type": "string", "enum": [str(c) for c in spec["choices"]]}
    base = {"type": "integer"} if spec["type"] == "int" else {"type": "string"}
    if spec.get("allow_default"):
        return {"anyOf": [base, {"type": "string", "enum": [DEFAULT]}]}
    return base


def response_schema(space):
    names = [spec["name"] for spec in space]
    return {
        "type": "object",
        "properties": {
            "reasoning": {"type": "string"},
            "stop": {"type": "boolean"},
            "configuration": {
                "type": "object",
                "properties": {spec["name"]: _value_schema(spec) for spec in space},
                "required": names,
                "additionalProperties": False,
            },
        },
        "required": ["reasoning", "stop", "configuration"],
        "additionalProperties": False,
    }


def describe_space(space):
    """The search space as the agent sees it."""
    out = []
    for spec in space:
        entry = {"name": spec["name"], "type": spec["type"]}
        if spec["description"]:
            entry["description"] = spec["description"]
        if spec["type"] == "enum":
            entry["choices"] = spec["choices"]
        else:
            entry["min"], entry["max"] = spec["min"], spec["max"]
            entry["default_allowed"] = spec["allow_default"]
            if spec["type"] == "pair":
                entry["format"] = "'<first>/<second>', each part within its min/max"
            entry["suggested_values"] = spec["hints"]
        out.append(entry)
    return out


def _check_int(name, value, low, high):
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name}: expected an integer, got {value!r}")
    if isinstance(value, float) and value != number:
        raise ValueError(f"{name}: expected an integer, got {value!r}")
    if not low <= number <= high:
        raise ValueError(f"{name}: {number} is outside [{low}, {high}]")
    return number


def _normalize(spec, value):
    name = spec["name"]
    if spec["type"] == "enum":
        for choice in spec["choices"]:
            if str(choice) == str(value):
                return choice
        raise ValueError(f"{name}: {value!r} is not one of {spec['choices']}")
    if _is_default(value):
        if not spec["allow_default"]:
            raise ValueError(f"{name}: '{DEFAULT}' is not allowed")
        return DEFAULT
    if spec["type"] == "int":
        return _check_int(name, value, spec["min"], spec["max"])
    parts = str(value).split("/")
    if len(parts) != 2:
        raise ValueError(f"{name}: expected '<first>/<second>', got {value!r}")
    first, second = (_check_int(name, part.strip(), low, high)
                     for part, low, high in zip(parts, spec["min"], spec["max"]))
    return f"{first}/{second}"


def validate_proposal(space, configuration):
    """Return (normalized configuration, errors). The configuration is ordered
    like the search space; errors is empty when it is usable."""
    errors = []
    normalized = {}
    names = {spec["name"] for spec in space}
    for extra in sorted(set(configuration) - names):
        errors.append(f"{extra}: not a parameter of this search")
    for spec in space:
        name = spec["name"]
        if name not in configuration:
            errors.append(f"{name}: missing")
            continue
        try:
            value = _normalize(spec, configuration[name])
        except ValueError as e:
            errors.append(str(e))
            continue
        # Dry render: the same code that builds the real change on the server.
        try:
            if system_config.is_system_parameter(name):
                system_config.render_commands({name: value})
            else:
                bind_config.render_statement(name, value)
        except (BindConfigError, SystemConfigError) as e:
            errors.append(str(e))
            continue
        normalized[name] = value
    return normalized, errors


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

def _clip(value):
    text = str(value)
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + "..."


def format_history(space, rows):
    """One JSON object per measured point, oldest first."""
    lines = []
    for row in rows:
        entry = {"configuration": {spec["name"]: row.get(spec["name"], "")
                                   for spec in space}}
        for field in RESULT_FIELDS:
            value = row.get(field, "")
            if value not in ("", None):
                entry[field] = _clip(value)
        lines.append(json.dumps(entry))
    return "\n".join(lines) if lines else "(nothing measured yet)"


def build_prompt(space, rows, best, remaining, context):
    best_text = (f"{best[0]} at {best[1]} QPS" if best
                 else "none yet (no point has produced a usable result)")
    return (
        "<measurement_setup>\n"
        f"{json.dumps(context, indent=2, default=str)}\n"
        "</measurement_setup>\n\n"
        "<search_space>\n"
        f"{json.dumps(describe_space(space), indent=2, default=str)}\n"
        "</search_space>\n\n"
        f"<results count=\"{len(rows)}\">\n{format_history(space, rows)}\n</results>\n\n"
        f"Best so far: {best_text}.\n"
        f"Measurements remaining after this one: {remaining - 1}.\n\n"
        "Propose the next configuration to measure: the one you expect to "
        "raise max_qps_passed the most, or that best tests a hypothesis you "
        "need to settle. Explain your reasoning briefly."
    )


# ---------------------------------------------------------------------------
# Proposer
# ---------------------------------------------------------------------------

class AgentProposer:
    def __init__(self, space, context, model=DEFAULT_MODEL, effort="high",
                 max_retries=3, transcript_path=None, client=None):
        self.space = space
        self.context = context
        self.model = model
        self.effort = effort
        self.max_retries = max_retries
        self.transcript_path = transcript_path
        self.schema = response_schema(space)
        if client is None:
            import anthropic
            client = anthropic.Anthropic()
        self.client = client

    def _record(self, entry):
        if not self.transcript_path:
            return
        os.makedirs(os.path.dirname(self.transcript_path), exist_ok=True)
        with open(self.transcript_path, "a") as f:
            f.write(json.dumps({"time": time.time(), **entry}, default=str) + "\n")

    def _call(self, messages):
        import anthropic
        try:
            with self.client.beta.messages.stream(
                model=self.model,
                max_tokens=32000,
                system=SYSTEM_PROMPT,
                messages=messages,
                thinking={"type": "adaptive"},
                output_config={
                    "effort": self.effort,
                    "format": {"type": "json_schema", "schema": self.schema},
                },
                betas=[FALLBACK_BETA],
                extra_body={"fallbacks": "default"},
            ) as stream:
                return stream.get_final_message()
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
            raise AgentProposalError(f"Anthropic API credentials rejected: {e}") from e
        except anthropic.BadRequestError as e:
            raise AgentProposalError(f"Anthropic API rejected the request: {e}") from e
        except anthropic.RateLimitError as e:
            raise AgentProposalError(f"Anthropic API rate limit (after retries): {e}") from e
        except anthropic.APIStatusError as e:
            raise AgentProposalError(f"Anthropic API error {e.status_code}: {e}") from e
        except anthropic.APIConnectionError as e:
            raise AgentProposalError(f"Could not reach the Anthropic API: {e}") from e

    def propose(self, rows, best, remaining):
        """Return (configuration, reasoning, stop) for the next point.

        ``rows`` is every recorded row so far; a configuration whose point id
        is among them is rejected as a duplicate and the agent is asked again.
        """
        measured = {row["point_id"] for row in rows if row.get("point_id")}
        prompt = build_prompt(self.space, rows, best, remaining, self.context)
        messages = [{"role": "user", "content": prompt}]
        self._record({"event": "prompt", "prompt": prompt})

        for attempt in range(1, self.max_retries + 1):
            response = self._call(messages)
            usage = getattr(response, "usage", None)
            text = next((b.text for b in response.content if b.type == "text"), "")
            self._record({"event": "response", "attempt": attempt,
                          "stop_reason": response.stop_reason, "text": text,
                          "usage": usage.to_dict() if hasattr(usage, "to_dict") else None})

            if response.stop_reason == "refusal":
                raise AgentProposalError(f"agent refused: {response.stop_details}")
            if response.stop_reason == "max_tokens":
                raise AgentProposalError("agent response was cut off at max_tokens")

            try:
                answer = json.loads(text)
                reasoning = answer["reasoning"]
                stop = bool(answer["stop"])
                configuration = answer["configuration"]
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                errors = [f"response is not the expected JSON object: {e}"]
            else:
                if stop:
                    return None, reasoning, True
                configuration, errors = validate_proposal(self.space, configuration)
                if not errors and point_id(configuration) in measured:
                    errors = ["this configuration has already been measured; "
                              "propose a different one"]
                if not errors:
                    return configuration, reasoning, False

            log.warning("Agent proposal rejected (attempt %d/%d): %s",
                        attempt, self.max_retries, "; ".join(errors))
            self._record({"event": "rejected", "attempt": attempt, "errors": errors})
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content":
                             "That proposal cannot be used:\n- " + "\n- ".join(errors)
                             + "\nPropose a corrected configuration."})

        raise AgentProposalError(
            f"no usable proposal after {self.max_retries} attempts")
