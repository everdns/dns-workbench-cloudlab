"""Client-side access to the tuning schema.

The schema itself, and every rule about what a valid candidate is, lives in
``ns_software/bind/optimization/render_config.py`` -- one implementation shared
by this package and by the root-owned copy the name server renders from. This
module only adds the things the workstation needs on top of it: locating the
schema in the repo, loading host facts, and projecting the schema into the JSON
Schema the agent's ``evaluate_config`` tool advertises.

Keeping ``to_json_schema`` here rather than hand-writing the tool definition is
what makes ``tunables.yaml`` genuinely the single source of truth: the renderer,
the grid space validator, and the model's tool contract cannot drift apart,
because all three are derived from the same file.
"""

import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OPTIMIZATION_DIR = os.path.join(REPO, "ns_software", "bind", "optimization")
if OPTIMIZATION_DIR not in sys.path:
    sys.path.insert(0, OPTIMIZATION_DIR)

import render_config as _rc  # noqa: E402

# Re-export the contract so callers never import the renderer directly.
SchemaError = _rc.SchemaError
load_schema = _rc.load_schema
schema_sha = _rc.schema_sha
params_by_name = _rc.params_by_name
canonical = _rc.canonical
candidate_id = _rc.candidate_id
render = _rc.render
effective_max = _rc.effective_max

DEFAULT_SCHEMA_PATH = _rc.DEFAULT_SCHEMA


def load_facts(path=None):
    """Host facts (nproc, ethtool maxima) used to clamp declared maxima.

    Written on the name server by ``install_tuner.sh`` at
    ``/usr/local/lib/dns-tuner/facts.json``. Fetch a copy to the workstation so
    the client rejects an over-cap value immediately instead of paying an SSH
    round trip to be told the same thing.
    """
    if not path:
        return None
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def defaults(schema):
    """The baseline candidate: every parameter at its schema default."""
    return canonical(schema, {})


def semantic_params(schema):
    """Parameters whose value changes what the server *answers*, not just how fast.

    A candidate that touches one of these can raise QPS by measuring different
    protocol behaviour, so results are reported separately from free wins.
    """
    return {p["name"] for p in schema["params"] if p.get("changes_semantics")}


def touches_semantics(schema, params):
    """True if this candidate departs from the default on any semantic knob."""
    canon = canonical(schema, params)
    by_name = params_by_name(schema)
    return any(canon[name] != by_name[name].get("default")
               for name in semantic_params(schema))


def to_json_schema(schema, space=None, facts=None):
    """Project the tuning schema into the agent tool's ``input_schema``.

    With ``strict: true`` the API validates arguments against this before they
    reach our process, so it becomes a second, independent copy of the whitelist
    -- one the model cannot route around by malforming a call.

    Passing ``space`` narrows every domain to exactly the grid's values. That is
    what makes the head-to-head comparison honest: both optimizers then search
    an identical finite set, so "the agent needed fewer evaluations" cannot be
    explained by the agent having had a larger space to work in.
    """
    properties = {}
    required = []
    for param in schema["params"]:
        name = param["name"]
        if space is not None and name not in space:
            continue
        required.append(name)

        if param["type"] == "enum":
            values = param["values"]
            if space is not None:
                values = [v for v in values if v in space[name]]
            properties[name] = {
                "type": "string",
                "enum": values,
                "description": _describe(param),
            }
        else:
            if space is not None:
                # In grid mode the space defines exactly what is permitted, so
                # the bounds are its own. Widening to admit the `omit_when`
                # sentinel here would let the agent propose values the grid
                # cannot reach, and the head-to-head comparison would no longer
                # be over an identical set.
                low, high = min(space[name]), max(space[name])
            else:
                low = param["min"]
                high = effective_max(param, facts)
                # `omit_when` (usually 0) means "leave this alone" and is a legal
                # value even though it sits outside [min, max].
                low = min(low, param.get("omit_when", low))
            properties[name] = {
                "type": "integer",
                "minimum": low,
                "maximum": high,
                "description": _describe(param),
            }

    return {
        "type": "object",
        "properties": {
            "params": {
                "type": "object",
                "properties": properties,
                # `strict` requires both of these, and making every parameter
                # mandatory also removes the "does omitted mean default?"
                # ambiguity -- the canonical dict is exactly what was sent.
                "required": required,
                "additionalProperties": False,
            },
            "rationale": {
                "type": "string",
                "maxLength": 500,
                "description": (
                    "Why this configuration is worth an evaluation. Recorded in "
                    "the run ledger as the audit trail for an unattended campaign."
                ),
            },
        },
        "required": ["params", "rationale"],
        "additionalProperties": False,
    }


def _describe(param):
    doc = " ".join((param.get("doc") or "").split())
    bits = [doc] if doc else []
    if param.get("omit_when") is not None:
        bits.append(f"{param['omit_when']} leaves this setting untouched.")
    if param.get("changes_semantics"):
        bits.append("CHANGES PROTOCOL SEMANTICS: a gain here is partly a change "
                    "in what the server answers, not purely a speedup.")
    if param.get("risky"):
        bits.append("RISKY: applying this bounces the network link.")
    bits.append(f"Default: {param.get('default')!r}.")
    return " ".join(bits)


def describe_table(schema, space=None, facts=None):
    """A plain-text tunables table for the agent's system prompt."""
    lines = [
        f"{'parameter':<22} {'type':<7} {'domain':<34} {'default':<14} notes",
        f"{'-' * 22} {'-' * 7} {'-' * 34} {'-' * 14} {'-' * 5}",
    ]
    for param in schema["params"]:
        name = param["name"]
        if space is not None and name not in space:
            continue
        if param["type"] == "enum":
            values = param["values"] if space is None else space[name]
            domain = "|".join(str(v) for v in values)
        else:
            if space is None:
                domain = f"{param['min']}..{effective_max(param, facts)}"
                if param.get("step"):
                    domain += f" step {param['step']}"
            else:
                domain = ", ".join(str(v) for v in sorted(space[name]))
        notes = []
        if param.get("changes_semantics"):
            notes.append("SEMANTIC")
        if param.get("risky"):
            notes.append("RISKY")
        sentinel = param.get("omit_when")
        # Only advertise the "leave it alone" sentinel when it is actually
        # reachable. In grid mode the space may exclude it, and offering a value
        # the tool schema will reject just wastes a turn.
        if sentinel is not None and (space is None or sentinel in space[name]):
            notes.append(f"{sentinel}=untouched")

        default = param.get("default")
        shown = str(default)
        if space is not None and default not in space[name]:
            shown = f"{default} (n/a)"
            notes.append("default is outside this run's range")

        lines.append(
            f"{name:<22} {param['type']:<7} {domain[:34]:<34} "
            f"{shown:<14} {' '.join(notes)}"
        )
    return "\n".join(lines)
