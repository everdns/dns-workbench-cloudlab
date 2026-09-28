# Configuration search

These scripts look for the BIND options and name-server host settings that give the highest **max sustainable QPS**. Each configuration they test goes through the same steps:

1. The configuration is installed on the name server.
2. The server is measured with `max_sustainable_qps.py`.
3. The base configuration is restored.
4. The result is recorded.

| Script | How it picks the next configuration | Config file |
|---|---|---|
| `grid_search.py` | Every combination (`grid`), or one parameter at a time (`coordinate-descent`) | `grid_search.yaml` |
| `agent_search.py` | Claude reads the results so far and proposes the next configuration | `agent_search.yaml` |

Both scripts use the shared code in `search.py`.

## Before you start

- Run the scripts from the load-generator (client) node. It must be able to SSH to the name server and use `sudo` there without a password.
- Install BIND on the name server (`ns_software/bind/install.sh`) and load the zone data (`optimization_setup.sh`).
- Check `hosts`, `client_interface`, `input_file` and `tool` at the top of the YAML file for your testbed.

Every point runs a full QPS search, which usually takes several minutes. Use `--dry-run` first to see what would be applied without touching the server.

## grid_search.py

The parameters and their candidate values are in `grid_search.yaml` under `parameters:`. Remove values or parameters there to shrink the search.

```bash
cd optimization

# Preview the configurations without applying anything
python3 grid_search.py --dry-run

# Measure every combination
python3 grid_search.py

# Coordinate descent: start at each parameter's first value, then tune one parameter at a time
python3 grid_search.py --mode coordinate-descent --max-passes 3
```

Useful options:

| Option | Meaning |
|---|---|
| `--mode grid\|coordinate-descent` | Search strategy (default `grid`) |
| `--max-passes N` | Coordinate descent: most passes over all parameters (default 3) |
| `--min-improvement-pct P` | Coordinate descent: how much better, in percent, a value must be to replace the current one (default 1.0) |
| `--resume` | Skip points already recorded by an earlier run of the same mode |
| `--point-timeout S` | Seconds allowed for one point (default 21600) |
| `--server HOST`, `--dns-service NAME` | Override the config file |
| `--output-dir DIR` | Where results go (default `results`) |

## agent_search.py

Each round, Claude gets the search space and every result so far, and proposes the configuration it expects to do best. The script checks that proposal against the bounds in `agent_search.yaml` before applying it, and sends invalid or repeated proposals back to Claude to fix.

Setup:

```bash
pip install anthropic
export ANTHROPIC_API_KEY=...
export ANTHROPIC_WORKSPACE_ID=wrkspc_...   # only if the key is not scoped to a workspace
```

Run:

```bash
cd optimization

# Ask for one proposal and print it, without applying anything
python3 agent_search.py --dry-run

# Test at most 15 configurations
python3 agent_search.py --max-configs 15
```

Useful options:

| Option | Meaning |
|---|---|
| `--max-configs N` | Most configurations to test (default: `agent.max_configs` in the YAML, else 20). The first point, failed points and resumed points all count toward this limit |
| `--agent-model`, `--agent-effort` | Claude model and effort (default `claude-opus-5`, `high`) |
| `--agent-seed first\|none` | `first` (the default) measures every parameter at its first listed value before asking Claude. `none` lets Claude choose the first point too |
| `--resume` | Keep the points from an earlier agent run and continue up to `--max-configs` |
| `--point-timeout`, `--server`, `--dns-service`, `--output-dir` | Same as `grid_search.py` |

Claude may propose any value within each parameter's `agent:` bounds in `agent_search.yaml`, for example:

```yaml
- name: rmem
  values: [default, 16777216, 67108864]   # suggestions; the first one is the starting value
  agent: {type: int, min: 212992, max: 268435456, description: "net.core.rmem_max + rmem_default"}
```

A parameter's `type` sets what Claude can propose:
- `int`: any whole number between `min` and `max`.
- `pair`: a value of the form `"<a>/<b>"`, with each part inside its own bounds.
- `enum`: only the listed choices.

## Results

Both scripts write to `results/grid_search/`:

| File | Contents |
|---|---|
| `grid_results.csv`, `coordinate_descent_results.csv`, `agent_results.csv` | One row per configuration: parameter values, `status`, `max_qps_passed`, and whether the QPS ceiling was hit. Agent runs also record the agent's reasoning |
| `agent_transcript.jsonl` | Every prompt sent to Claude and every response |
| `runs/<point>/` | Per-trial output for each configuration |

When a run ends or is interrupted, the scripts restore the name server's base configuration. If that restore fails, the script exits with code 3 and says what to reinstall or reboot.
