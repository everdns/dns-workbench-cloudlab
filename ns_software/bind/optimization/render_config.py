#!/usr/bin/env python3
"""Turn a validated candidate dict into BIND / OS configuration artifacts.

This is the single implementation of the schema contract. It runs in two places
and must behave identically in both:

  * client-side, inside config_tuner, to canonicalize, hash, and preview a
    candidate before it is shipped;
  * root-side, inside apply_candidate.sh, to render the artifacts that are
    actually installed -- from the *root-owned* copy of tunables.yaml, so the
    name server never trusts the workstation's validation.

The security property this module carries is that a malformed or hostile
candidate is unrepresentable rather than merely filtered:

  * every key must appear in the schema's ``params`` (closed world);
  * every value is int or enum -- there is no free-text parameter type;
  * an enum value is used only as a lookup key into the schema's own ``values``
    list, so the string that reaches a config file always originates in this
    repo, never in the caller's input;
  * ints are coerced with ``int()`` and range/step checked, then emitted with
    ``str(int(v))``;
  * as a backstop, every rendered token must match ``_SAFE_TOKEN``. Only the
    ``invariants`` block may contain braces, semicolons, or quotes, and it is
    read from this repo.

CLI
---
    render_config.py --hash < candidate.json
    render_config.py --schema tunables.yaml --candidate candidate.json \\
                     --out-dir /run/dns-tuner/render [--facts facts.json] \\
                     [--iface enp94s0f1]
"""

import argparse
import hashlib
import json
import os
import re
import sys
from collections import OrderedDict

try:
    import yaml
except ImportError:  # pragma: no cover - install_tuner.sh apt-installs python3-yaml
    sys.stderr.write("render_config.py requires PyYAML (apt install python3-yaml)\n")
    raise

DEFAULT_SCHEMA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tunables.yaml")

# Backstop on every token this module emits into a config file. Deliberately
# excludes { } ; " ' newline and whitespace, so nothing a caller supplies can
# terminate a directive or open a new block.
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_.\-]+$")


class SchemaError(ValueError):
    """A candidate violated the schema. The message is safe to show a caller."""


# --------------------------------------------------------------------- loading


def load_schema(path=None):
    """Load and structurally validate tunables.yaml."""
    path = path or DEFAULT_SCHEMA
    with open(path) as f:
        schema = yaml.safe_load(f)

    for required in ("version", "targets", "params"):
        if required not in schema:
            raise SchemaError(f"schema is missing the '{required}' section")

    seen = set()
    for param in schema["params"]:
        name = param.get("name")
        if not name:
            raise SchemaError("every param needs a 'name'")
        if name in seen:
            raise SchemaError(f"duplicate param '{name}'")
        seen.add(name)
        if param.get("target") not in schema["targets"]:
            raise SchemaError(
                f"param '{name}' targets unknown '{param.get('target')}'"
            )
        if param.get("type") not in ("int", "enum"):
            # Guards the core safety property: adding a free-text knob must be a
            # deliberate change to this module, not just a YAML edit.
            raise SchemaError(
                f"param '{name}' has type '{param.get('type')}'; "
                "only 'int' and 'enum' are permitted"
            )
    return schema


def schema_sha(path=None):
    """sha256 of the schema file, recorded in every manifest and ledger row."""
    path = path or DEFAULT_SCHEMA
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def params_by_name(schema):
    return OrderedDict((p["name"], p) for p in schema["params"])


def effective_max(param, facts=None):
    """The param's max, clamped by its fact_cap when host facts are available.

    ``fact_cap`` names a host fact (nproc, ethtool_rx_max, ...). Without facts we
    fall back to the declared max; the root-side apply always passes facts, so a
    value that slips past a client-side check is still rejected on the host.
    """
    declared = param["max"]
    cap_name = param.get("fact_cap")
    if not cap_name or not facts:
        return declared
    cap = facts.get(cap_name)
    if cap is None:
        return declared
    return min(declared, int(cap))


# ------------------------------------------------------------------- coercion


def coerce_value(param, raw, facts=None):
    """Coerce and domain-check one value. Raises SchemaError on any violation."""
    name = param["name"]

    if param["type"] == "enum":
        # Identity selection: `raw` is only ever a lookup key. The value we
        # return is the schema's own string object, so nothing the caller wrote
        # can reach the rendered file.
        if isinstance(raw, bool):
            raise SchemaError(f"{name}: booleans are not enum values; use a quoted string")
        for allowed in param["values"]:
            if raw == allowed:
                return allowed
        raise SchemaError(
            f"{name}: {raw!r} is not one of {param['values']}"
        )

    # int
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        raise SchemaError(f"{name}: expected an integer, got {type(raw).__name__}")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise SchemaError(f"{name}: {raw!r} is not an integer")
    if isinstance(raw, float) and value != raw:
        raise SchemaError(f"{name}: {raw!r} is not a whole number")
    if isinstance(raw, str) and str(value) != raw.strip():
        raise SchemaError(f"{name}: {raw!r} is not a bare integer")

    low = param["min"]
    high = effective_max(param, facts)
    omit = param.get("omit_when")
    # `omit_when` (usually 0) is a legal sentinel meaning "leave this alone",
    # and is allowed to sit outside [min, max].
    if omit is not None and value == omit:
        return value
    if value < low or value > high:
        cap = param.get("fact_cap")
        suffix = f" (max clamped to {high} by host fact '{cap}')" if cap and high != param["max"] else ""
        raise SchemaError(f"{name}: {value} outside [{low}, {high}]{suffix}")

    step = param.get("step")
    if step and (value - low) % step != 0:
        raise SchemaError(f"{name}: {value} is not {low} plus a multiple of {step}")
    return value


def check_constraints(schema, values):
    """Evaluate the schema's cross-parameter constraints against a full dict."""
    for constraint in schema.get("constraints", []):
        expr = constraint["expr"]
        try:
            # Constraints come from this repo, not from any caller. The empty
            # builtins namespace keeps a typo from reaching the interpreter's
            # wider surface even so.
            ok = eval(expr, {"__builtins__": {}}, dict(values))  # noqa: S307
        except Exception as e:
            raise SchemaError(f"constraint '{constraint.get('name', expr)}' failed to evaluate: {e}")
        if not ok:
            raise SchemaError(
                f"constraint '{constraint.get('name', expr)}' violated: "
                f"{constraint.get('message', expr).strip()}"
            )


def canonical(schema, values, facts=None):
    """Validate a candidate and return it with defaults filled, in schema order.

    Canonical form is what gets hashed, so an empty dict and an explicit
    spelling of every default collide to the same candidate_id -- which is what
    stops an optimizer from re-measuring the baseline at full cost.
    """
    if not isinstance(values, dict):
        raise SchemaError("candidate must be a JSON object")

    known = params_by_name(schema)
    unknown = sorted(set(values) - set(known))
    if unknown:
        raise SchemaError(
            f"unknown parameter(s): {', '.join(unknown)}. "
            f"Permitted: {', '.join(known)}"
        )

    out = OrderedDict()
    for name, param in known.items():
        raw = values[name] if name in values else param.get("default")
        if raw is None:
            raise SchemaError(f"{name}: no value supplied and no default declared")
        out[name] = coerce_value(param, raw, facts)

    check_constraints(schema, out)
    return out


def candidate_id(schema, values, facts=None):
    """Stable 16-hex identity for a candidate, over its canonical form."""
    canon = canonical(schema, values, facts)
    payload = json.dumps(canon, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


# -------------------------------------------------------------------- emission


def _token(value):
    """Render one scalar and assert it cannot escape its directive."""
    text = str(value)
    if not _SAFE_TOKEN.match(text):
        raise SchemaError(f"refusing to emit unsafe token {text!r}")
    return text


def _emit_options(schema, canon, cid):
    target = schema["targets"]["options"]
    lines = [
        target.get("header", "// GENERATED -- do not edit by hand"),
        f"// candidate_id: {_token(cid)}   schema_version: {_token(schema['version'])}",
        "options {",
    ]
    for directive, literal in schema.get("invariants", {}).get("options", {}).items():
        # Invariants are the only strings allowed to carry braces and quotes,
        # and they come from the repo file rather than from any caller.
        lines.append(f"    {directive} {literal};" + " " * 4 + "// invariant")
    for param in schema["params"]:
        if param["target"] != "options":
            continue
        value = canon[param["name"]]
        if param.get("omit_when") is not None and value == param["omit_when"]:
            continue
        lines.append(f"    {param['directive']} {_token(value)};")
    lines.append("};")
    return "\n".join(lines) + "\n"


def _emit_startup(schema, canon, cid):
    target = schema["targets"]["startup"]
    flags = list(target.get("prefix_flags", []))
    for param in schema["params"]:
        if param["target"] != "startup":
            continue
        value = canon[param["name"]]
        if param.get("omit_when") is not None and value == param["omit_when"]:
            continue
        flags.append(f"{param['flag']} {_token(value)}")

    if target.get("kind") == "systemd_dropin":
        # Fallback path when named.service does not source /etc/default/named
        # (see verify_environment.sh). Only the target block changes.
        return (
            f"# GENERATED BY config_tuner -- candidate_id: {_token(cid)}\n"
            "[Service]\n"
            "ExecStart=\n"
            f"ExecStart=/usr/sbin/named -f {' '.join(flags)}\n"
        )

    return (
        f"# GENERATED BY config_tuner -- candidate_id: {_token(cid)}\n"
        "RESOLVCONF=no\n"
        f'{target.get("var", "OPTIONS")}="{" ".join(flags)}"\n'
    )


def _emit_sysctl(schema, canon, cid):
    lines = [f"# GENERATED BY config_tuner -- candidate_id: {_token(cid)}"]
    for param in schema["params"]:
        if param["target"] != "sysctl":
            continue
        value = canon[param["name"]]
        if param.get("omit_when") is not None and value == param["omit_when"]:
            continue
        lines.append(f"{param['key']} = {_token(value)}")
    return "\n".join(lines) + "\n"


def _emit_nic(schema, canon, cid, iface):
    """Shell-sourceable ethtool plan for the ONE pinned interface.

    The interface is never carried in the candidate. It is supplied by the
    caller from the host's root-owned nic_allowlist.conf, and apply_candidate.sh
    re-checks it against that allowlist before running ethtool.
    """
    ops = {}
    for param in schema["params"]:
        if param["target"] != "nic":
            continue
        value = canon[param["name"]]
        if param.get("omit_when") is not None and value == param["omit_when"]:
            continue
        ops.setdefault(param["op"], {})[param["field"]] = value

    lines = [f"# GENERATED BY config_tuner -- candidate_id: {_token(cid)}"]
    if iface is None:
        if ops:
            raise SchemaError(
                "candidate changes NIC settings but no pinned interface was supplied"
            )
        lines.append("# no NIC changes in this candidate")
        return "\n".join(lines) + "\n", ops
    lines.append(f"TUNER_IFACE={_token(iface)}")
    for op, fields in sorted(ops.items()):
        for field, value in sorted(fields.items()):
            lines.append(f"{op.upper()}_{field.upper()}={_token(value)}")
    return "\n".join(lines) + "\n", ops


def render(schema, values, facts=None, iface=None):
    """Validate a candidate and render every artifact it implies."""
    canon = canonical(schema, values, facts)
    cid = candidate_id(schema, canon, facts)
    nic_text, nic_ops = _emit_nic(schema, canon, cid, iface)
    return {
        "candidate_id": cid,
        "canonical": canon,
        "options": _emit_options(schema, canon, cid),
        "startup": _emit_startup(schema, canon, cid),
        "sysctl": _emit_sysctl(schema, canon, cid),
        "nic": nic_text,
        "nic_ops": nic_ops,
    }


# ------------------------------------------------------------------------- CLI


ARTIFACT_FILENAMES = {
    "options": "named.conf.options",
    "startup": "default-named",
    "sysctl": "99-dns-tuner.conf",
    "nic": "nic.env",
}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--schema", default=DEFAULT_SCHEMA)
    parser.add_argument("--candidate", help="candidate JSON file (default: stdin)")
    parser.add_argument("--facts", help="host facts JSON, for fact_cap clamping")
    parser.add_argument("--iface", help="pinned NIC, from the host allowlist")
    parser.add_argument("--out-dir", help="write rendered artifacts here")
    parser.add_argument("--hash", action="store_true",
                        help="print the candidate_id and exit")
    args = parser.parse_args(argv)

    try:
        schema = load_schema(args.schema)
        source = open(args.candidate) if args.candidate else sys.stdin
        with source as f:
            values = json.load(f)
        facts = None
        if args.facts:
            with open(args.facts) as f:
                facts = json.load(f)

        if args.hash:
            print(candidate_id(schema, values, facts))
            return 0

        result = render(schema, values, facts, args.iface)
    except SchemaError as e:
        sys.stderr.write(f"invalid candidate: {e}\n")
        return 2
    except (OSError, json.JSONDecodeError, yaml.YAMLError) as e:
        sys.stderr.write(f"could not render: {e}\n")
        return 2

    if not args.out_dir:
        sys.stdout.write(result["options"])
        return 0

    os.makedirs(args.out_dir, exist_ok=True)
    for key, filename in ARTIFACT_FILENAMES.items():
        path = os.path.join(args.out_dir, filename)
        with open(path, "w") as f:
            f.write(result[key])
        os.chmod(path, 0o644)
    with open(os.path.join(args.out_dir, "manifest.json"), "w") as f:
        json.dump({
            "candidate_id": result["candidate_id"],
            "schema_version": schema["version"],
            "schema_sha": schema_sha(args.schema),
            "canonical": result["canonical"],
            "nic_ops": result["nic_ops"],
            "iface": args.iface,
        }, f, indent=2, sort_keys=True)
    print(result["candidate_id"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
