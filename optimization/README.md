# Configuration search

These scripts look for the BIND options and name-server host settings that give the highest **max sustainable QPS**. Each configuration they test goes through the same steps:

1. The configuration is installed on the name server.
2. The server is measured with `max_sustainable_qps.py`.
3. The base configuration is restored.
4. The result is recorded.

| Script | How it picks the next configuration | Config file |
|---|---|---|
| `grid_search.py` | Every combination (`grid`), or one parameter at a time (`coordinate-descent`) | `grid_search.yaml` |
| `agent_search.py` | A Claude agent runs the whole search: what to measure, what to repeat, what to inspect on the server, and when to stop | `agent_search.yaml` |

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

A Claude agent runs the search from start to finish. The script connects it to the name server through a few tools, and the agent decides how to use them:

| Tool | What it does |
|---|---|
| `measure_configuration` | Installs a configuration, measures it, restores the base, and returns the score and per-level answer rates. Uses one measurement from the budget. With `repeat=true` it re-measures a configuration that was already tested, to check noise |
| `get_results` | Lists every measurement so far, with the budget left and the best result |
| `get_run_details` | Shows per-level and per-trial results for one measurement |
| `inspect_server` | Runs a fixed read-only diagnostic on the name server: `udp_counters`, `softnet_stat`, `sysctls`, `firewall`, `nic`, `cpu`, `named`, or `memory`. In bounded mode the agent cannot run any other command; in open mode it can run any command |
| `finish` | Ends the search with a recommended configuration and a written report |

The agent chooses the order of measurements, including whether to start from the baseline (every parameter at its first listed value). It decides when a result needs a repeat, when to look at drop counters, and when to stop. The script sets the limits: every value must be inside the `agent:` bounds in `agent_search.yaml`, and the run stops at `--max-configs` measurements. A rejected call goes back to the agent as an error and never reaches the server.

Claude can be reached in two ways. Pick one with `--agent-backend`, or set `agent.backend` in the YAML.

**`claude-code`: uses your Claude Pro/Max subscription.** The whole search is one Claude Code session (`claude -p`), which counts against your plan's usage limits, not API credits. Claude Code reaches the tools through an MCP server, `agent_mcp_proxy.py`, which it starts itself. You don't need to install anything besides Claude Code. That session gets only the search tools; Claude Code's own Bash, Edit, Write and web tools are turned off. Install Claude Code and log in once:

```bash
curl -fsSL https://claude.ai/install.sh | bash
claude        # sign in with your Claude account, then exit
```

**`api` (default): uses the Anthropic API, billed from Console credits.** A Pro subscription does not include API credits.

```bash
pip install anthropic
export ANTHROPIC_API_KEY=...
export ANTHROPIC_WORKSPACE_ID=wrkspc_...   # only if the key is not scoped to a workspace
```

Run:

```bash
cd optimization

# Let the agent pick its first configuration and show what would be applied, without changing the server
python3 agent_search.py --agent-backend claude-code --dry-run

# Allow at most 15 measurements
python3 agent_search.py --agent-backend claude-code --max-configs 15
```

The log shows the agent's messages and every tool call as they happen. At the end the script prints the agent's report.

Useful options:

| Option | Meaning |
|---|---|
| `--mode bounded\|open` | Search only the YAML parameters within their bounds, or let Claude change anything on the name server (default: `agent.mode`, else `bounded`) |
| `--max-configs N` | Most measurements (default: `agent.max_configs` in the YAML, else 20). Failed, repeated and resumed measurements all count toward this limit |
| `--agent-backend api\|claude-code` | How to reach Claude (default `api`) |
| `--agent-model`, `--agent-effort` | Claude model and effort. The model defaults to `claude-opus-5` for `api` and to Claude Code's own default for `claude-code`; effort defaults to `high` |
| `--agent-max-turns N` | Most agent turns before the run is stopped (default: `agent.max_turns`, else 8 per measurement + 20, or 12 in open mode) |
| `--claude-bin PATH` | Location of the `claude` executable, if it is not on your `PATH` |
| `--resume` | Give the agent the measurements from an earlier agent run and continue up to `--max-configs` |
| `--point-timeout`, `--server`, `--dns-service`, `--output-dir` | Same as `grid_search.py` |

Claude may measure any value within each parameter's `agent:` bounds in `agent_search.yaml`, for example:

```yaml
- name: rmem
  values: [default, 16777216, 67108864]   # suggestions; the first one is the starting value
  agent: {type: int, min: 212992, max: 268435456, description: "net.core.rmem_max + rmem_default"}
```

A parameter's `type` sets what Claude can choose:
- `int`: any whole number between `min` and `max`.
- `pair`: a value of the form `"<a>/<b>"`, with each part inside its own bounds.
- `enum`: only the listed choices.

### Open mode: no bounds

With `--mode open` (or `agent.mode: open`, the setting in `agent_search.yaml`), Claude is not limited to the parameter list. Each measurement can contain:

- `named_options`: any `named.conf.options` statements. A statement with the same keyword as one in the base file replaces it, and `named-checkconf` validates the result. Only the protected statements (`listen-on`, `listen-on-v6`, `allow-query`, `recursion`, `rate-limit`, `directory`) are refused.
- `host_commands`: any shell commands, run as root on the name server before the measurement. This covers sysctls, ethtool, IRQ affinity, RPS/XPS, CPU governor and C-states, services, systemd drop-ins for `named`, and kernel modules.
- `revert_commands`: any shell commands, run as root after the measurement.
- `reboot_after_apply`: reboot after `host_commands`, for kernel command-line changes made through `/etc/default/grub` and `update-grub`.

`inspect_server` accepts any command, as well as the named probes. The parameters in the YAML are shown to Claude only as ideas.

Before the search starts, `host_state.py` takes a snapshot of the name server. It records:
- every writable sysctl
- the tuning files in sysfs: IRQ affinity, RPS/XPS, NIC queue settings, CPU online state, governors, C-states, THP, and module parameters
- the per-NIC ethtool state: rings, channels, coalescing, offloads, RSS, ntuple rules and flow hashing
- the root qdisc, running services, iptables rules and loaded modules
- the config files under grub, sysctl.d, modprobe.d, systemd, limits, bind, ufw, udev, netplan and tuned. These are also saved to `/var/tmp/agent_baseline.tgz` on the server.

After every measurement, the script writes back whatever differs from the snapshot. If the kernel command line changed, that includes `update-grub` and a reboot. It then reports anything still different to Claude as `residual_drift`.

The server is reset with the snapshot, not by reinstalling it, so a change outside what the snapshot covers needs the agent's `revert_commands`. The agent is told not to break SSH or the experiment network, but the script does not prevent it. The name server must be a different machine from the one running `agent_search.py`, because reboots are refused otherwise.

Open-mode runs write `agent_open_results.csv`, `host_baseline.json`, `applied.json` in each run directory, and `recommended_apply.sh` with the commands for the winning change.

## Results

Both scripts write to `results/grid_search/`:

| File | Contents |
|---|---|
| `grid_results.csv`, `coordinate_descent_results.csv`, `agent_results.csv` | One row per measurement: parameter values, `status`, `max_qps_passed`, and whether the QPS ceiling was hit. Agent runs also record the agent's `hypothesis`, and `repeat_of` for repeated measurements (their `point_id` ends in `__rep2`, `__rep3`, ...) |
| `agent_report.json` | The agent's recommended configuration, its score, and the agent's written report |
| `agent_transcript.jsonl` | The agent's whole session: prompt, responses, and every tool call and result |
| `runs/<point>/` | Per-trial output for each configuration |

When a run ends or is interrupted, the scripts restore the name server's base configuration. If that restore fails, the script exits with code 3 and says what to reinstall or reboot.
