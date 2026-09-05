"""Expand a declared grid space into a list of candidates to evaluate.

Every value is checked against ``tunables.yaml`` at load time, so a typo in a
space file fails in milliseconds rather than on evaluation thirty, twelve hours
into a campaign.

Two design choices here shape whether the grid-versus-agent comparison means
anything:

  * **Shuffled order by default.** Under a wall-clock budget a grid rarely
    finishes, and in lexicographic order the surviving prefix is a biased
    sample: whichever knob is declared first gets fully explored and the rest
    not at all. The best-so-far curve would then describe declaration order
    rather than the optimizer. Shuffling with a recorded seed makes any prefix
    an unbiased sample, and decorrelates thermal drift from any single
    parameter.

  * **Staged (coordinate-descent) mode.** A full factorial is the honest
    baseline but scales badly at ~25 minutes per point: 3^4 is roughly 34 hours.
    Staged mode sweeps one parameter at a time carrying the winner forward,
    turning that same space into about a dozen evaluations.
"""

import itertools
import os
import random

import yaml

from tuner import schema as schema_mod


class SpaceError(ValueError):
    """The space file is inconsistent with the tuning schema."""


def load_space(path, schema, facts=None):
    """Read and validate a space file. Returns (space dict, raw spec)."""
    with open(os.path.expanduser(path)) as f:
        spec = yaml.safe_load(f) or {}

    raw = spec.get("space") or {}
    if not raw:
        raise SpaceError(f"{path}: the 'space' section is empty")

    known = schema_mod.params_by_name(schema)
    space = {}
    for name, values in raw.items():
        if name not in known:
            raise SpaceError(
                f"{path}: '{name}' is not a tunable parameter. "
                f"Permitted: {', '.join(known)}"
            )
        values = _expand_values(values)
        if not values:
            raise SpaceError(f"{path}: '{name}' has no values")
        for value in values:
            # Validate each value on its own, against the real schema, so a bad
            # one is named precisely rather than surfacing as a product failure.
            try:
                schema_mod.canonical(schema, {name: value}, facts)
            except schema_mod.SchemaError as e:
                raise SpaceError(f"{path}: {e}")
        space[name] = values
    return space, spec


def _expand_values(values):
    """Accept a plain list, or a {start, stop, num} range shorthand."""
    if isinstance(values, list):
        return values
    if isinstance(values, dict) and "start" in values:
        start, stop = int(values["start"]), int(values["stop"])
        num = int(values.get("num", 3))
        if num < 2:
            return [start]
        step = (stop - start) / (num - 1)
        return [int(round(start + i * step)) for i in range(num)]
    raise SpaceError(f"cannot interpret values: {values!r}")


def enumerate_full(space, schema, spec=None, facts=None):
    """Full factorial, filtered by schema constraints and space exclusions."""
    spec = spec or {}
    names = list(space)
    excluded = spec.get("exclude", []) or []

    out, seen = [], set()
    for combo in itertools.product(*(space[n] for n in names)):
        params = dict(zip(names, combo))
        if _is_excluded(params, excluded):
            continue
        try:
            canon = schema_mod.canonical(schema, params, facts)
        except schema_mod.SchemaError:
            # A cross-parameter constraint ruled this point out. Skipping it
            # silently is right: the space file declared the axes, and the
            # schema declared which combinations are not worth measuring.
            continue
        cid = schema_mod.candidate_id(schema, canon, facts)
        if cid in seen:
            continue
        seen.add(cid)
        out.append(dict(canon))
    return out


def _is_excluded(params, expressions):
    for expr in expressions:
        try:
            if eval(expr, {"__builtins__": {}}, dict(params)):  # noqa: S307
                return True
        except Exception:
            # An exclusion that references a parameter outside this space simply
            # does not apply to this point.
            continue
    return False


def enumerate_staged(space, schema, spec=None, facts=None):
    """Coordinate descent: sweep one parameter at a time, in declaration order.

    Returns the *first* stage only; ``next_stage`` produces subsequent ones once
    the previous stage's winner is known. The driver interleaves them so a
    staged run can still resume from the ledger.
    """
    spec = spec or {}
    base = spec.get("baseline") or {}
    canon = schema_mod.canonical(schema, base, facts)
    names = list(space)
    if not names:
        return [], None
    return _stage_candidates(space, schema, canon, names[0], facts), names


def next_stage(space, schema, incumbent, name, facts=None):
    """Candidates for one coordinate sweep, holding everything else at incumbent."""
    return _stage_candidates(space, schema, incumbent, name, facts)


def _stage_candidates(space, schema, incumbent, name, facts):
    out, seen = [], set()
    for value in space[name]:
        params = dict(incumbent)
        params[name] = value
        try:
            canon = schema_mod.canonical(schema, params, facts)
        except schema_mod.SchemaError:
            continue
        cid = schema_mod.candidate_id(schema, canon, facts)
        if cid in seen:
            continue
        seen.add(cid)
        out.append(dict(canon))
    return out


def order(candidates, strategy="shuffled", seed=7):
    """Order the candidates. See the module docstring on why shuffled is default."""
    if strategy == "lexicographic":
        return list(candidates)
    if strategy == "shuffled":
        out = list(candidates)
        random.Random(seed).shuffle(out)
        return out
    raise SpaceError(f"unknown ordering strategy '{strategy}'")


def size(space):
    total = 1
    for values in space.values():
        total *= len(values)
    return total
