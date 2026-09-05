# config_tuner — optimizing BIND configuration for max sustainable QPS

Two optimizers that change the authoritative name server's configuration and
measure the effect, plus the machinery that lets an LLM do so unattended without
being able to damage the testbed.

- **`grid_search.py`** — exhaustive (or coordinate-descent) search over a
  declared space. The baseline.
- **`agent_search.py`** — an LLM proposes configurations, measures them, and
  iterates on the results.

Both maximize the same metric through the same evaluation path and share every
component except the choice of the next candidate, so *evaluations spent to
reach a given QPS* is an honest comparison.

The objective is `max_qps_passed` from
`load_testing_benchmark/scripts/max_sustainable_qps.py`.

## Why an LLM can be trusted to edit /etc/bind unattended

Not because it is asked nicely. Because an unsafe configuration is
**unrepresentable**:

1. **A closed-world schema.** `ns_software/bind/optimization/tunables.yaml` lists
   every knob. Every parameter is `int` or `enum` — there is deliberately no
   free-text type. An enum value is only ever a *lookup key* into the schema's
   own list, so the string that lands in a config file always originates in this
   repo. Injection is not filtered; it cannot be expressed.
2. **The boundary carries a typed dict, not config text.** The workstation ships
   `{param: value}` as JSON; the name server renders it **as root, from its own
   copy of the schema**. A tampered-with workstation cannot hand the host a
   config that `named-checkconf` happens to accept — and it does accept
   `recursion yes; allow-transfer { any; };`.
3. **Invariants are re-applied every time.** Listen address, `recursion no`,
   `allow-query`, `allow-recursion` come from the repo on every candidate and
   cannot be dropped by omission.
4. **A deadman rollback.** Before the first mutation, a systemd timer is armed to
   restore the previous generation in 180 seconds. If a candidate wedges the host
   or drops the network — which reconfiguring NIC queues can do to the tuner's
   own SSH session — it self-heals with nobody watching.
5. **A conformance probe, not just a health check.** After every apply the server
   must answer the test zone correctly, with the AA bit, return NXDOMAIN for an
   absent name, and refuse recursion. This is what catches a candidate that
   raises QPS by answering *differently*.
6. **The NIC is pinned.** A candidate carries ring sizes and queue counts but
   never an interface name. The interface comes from root-owned
   `nic_allowlist.conf` and is re-checked before every `ethtool` call, so the
   management NIC cannot be touched.
7. **Five tools, no shell.** The agent gets `evaluate_config`,
   `compare_configs`, `record_hypothesis`, `get_search_state`, `finish`. No Bash,
   no file access, no way to reach its own budget.
8. **Budgets enforced outside the model**, plus a `touch <run-dir>/STOP` kill
   switch that works from any node.

**Honest scoping:** CloudLab grants the experiment user blanket passwordless
sudo, and this does not remove it. The narrowed sudoers and forced-command key
bound what the *tuner* can do, not what a determined local user can do. That is
the intended threat model — containing an LLM's mistakes, not an attacker.

## Setup

On the name server (`10.10.1.2`), once per experiment:

```sh
# 1. Check what this BIND and this NIC actually support, and prune tunables.yaml
bash /local/repository/ns_software/bind/optimization/verify_environment.sh <iface>

# 2. Install the root-owned helper, sudoers, and the NIC allowlist
sudo bash /local/repository/ns_software/bind/optimization/install_tuner.sh <iface>

# 3. Prove the safety path. Run this before every session.
bash /local/repository/config_tuner/smoke_test.sh --deadman
```

On the workstation:

```sh
pip install -r config_tuner/requirements.txt
scp 10.10.1.2:/usr/local/lib/dns-tuner/facts.json ~/.dns-tuner-facts.json
```

## Before spending a campaign

Two measurements gate everything else.

**The noise floor.** Evaluate the baseline 3–5 times and take the standard
deviation. `max_qps_passed` is quantized to 10,000 QPS, so anything finer is
invisible regardless. Without this number both optimizers will chase noise and
neither result is publishable. Put it in `configs/tuner.yaml` as
`agent.noise_floor_qps` — the agent is told about it explicitly.

**Client headroom.** If the load generator saturates before BIND does, every
candidate scores identically and no tuning shows anything. `configs/tuning.yaml`
uses both test hosts for this reason; confirm `qps_fidelity_pct` has room on the
baseline before continuing.

## Running

```sh
# What would this cost?
python3 config_tuner/grid_search.py --space config_tuner/configs/space_bind_small.yaml \
    --run-dir ~/tuner_runs/grid-1 --dry-run

# Exhaustive baseline (~13 h for 33 points at full fidelity)
python3 config_tuner/grid_search.py --space config_tuner/configs/space_bind_small.yaml \
    --run-dir ~/tuner_runs/grid-1 --max-hours 14

# Coordinate descent instead: same space, ~11 evaluations
python3 config_tuner/grid_search.py --space config_tuner/configs/space_bind_small.yaml \
    --run-dir ~/tuner_runs/grid-staged --strategy staged

# The agent, restricted to exactly the grid's values for a fair head-to-head
python3 config_tuner/agent_search.py --space config_tuner/configs/space_bind_small.yaml \
    --space-mode grid --run-dir ~/tuner_runs/agent-1 --max-evals 25

# The agent with the full schema: can it beat the grid by leaving the grid?
python3 config_tuner/agent_search.py --space-mode full \
    --run-dir ~/tuner_runs/agent-full --max-evals 25

python3 config_tuner/compare.py ~/tuner_runs/grid-1 ~/tuner_runs/agent-1 \
    --out-dir ~/tuner_runs/comparison --noise-floor <measured>
```

Interrupt any run and re-launch with `--resume`; the ledger *is* the resume
state, so there is no checkpoint to fall out of sync. A run refuses to resume if
the schema, space, or eval config changed underneath it.

`--space-mode grid` is what makes the comparison mean something: it narrows the
agent's tool schema to exactly the grid's values, so both search an identical
finite set. Without it, "the agent won" is confounded by a larger space.

## Offline

Everything runs against a synthetic response surface with no testbed at all:

```sh
python3 config_tuner/grid_search.py --space config_tuner/configs/space_bind_small.yaml \
    --run-dir /tmp/grid-sim --simulate config_tuner/configs/surface_bind.yaml
python3 config_tuner/tests/test_schema.py      # 29 tests, incl. injection attempts
python3 config_tuner/tests/test_agent_loop.py  # 25 tests, no API calls
```

`--simulate` does not bypass the evaluator subprocess: the surface score is
handed to the real `max_sustainable_qps.py` as `--simulate-max-qps`, so the real
two-phase search runs and writes real artifacts. Only remote execution is faked.

## Reading the results

`<run-dir>/ledger.jsonl` is append-only, one record per evaluation, identical in
shape for both optimizers. `ledger.csv` is the flattened version.

Three things the tooling refuses to let you misread:

- **Ceiling-limited results are lower bounds** and never become "best".
- **Generator-limited results measure the load generator**, not the server, and
  never become "best".
- **`changes_semantics`** marks a candidate that departs from default on
  `minimal_responses`, `answer_cookie`, or `dnssec_validation`. Those can raise
  QPS by changing what the server *answers*. `compare.py` prints a warning when a
  winning configuration is one of them; report it separately from free wins.

## Layout

```
ns_software/bind/optimization/    # ships to the name server with the repo
  tunables.yaml                   # the closed-world whitelist
  render_config.py                # candidate dict -> config text (one impl,
                                  #   used client-side AND as root)
  apply_candidate.sh              # the single privileged entrypoint
  conformance.sh                  # post-apply correctness assertions
  install_tuner.sh                # root-owned install + sudoers + NIC allowlist
  verify_environment.sh           # Phase 0 checks

config_tuner/
  tuner/schema.py      # validation, canonical hashing, tool-schema projection
  tuner/apply.py       # stage + apply over SSH; SimulatedApplier for offline
  tuner/evaluate.py    # run max_sustainable_qps.py, parse, cache, clean up
  tuner/ledger.py      # append-only JSONL, resume state, CSV export
  tuner/budget.py      # caps and the kill switch, enforced outside the proposer
  tuner/space.py       # grid expansion, constraints, ordering
  tuner/simulate.py    # synthetic response surface
  tuner/plots.py       # anytime curves, parameter effects
```
