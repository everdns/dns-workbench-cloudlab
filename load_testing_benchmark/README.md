# DNS Load Testing Benchmark Framework

A Python framework for benchmarking and comparing DNS load testing tools in a controlled, reproducible two-host environment.

## Tools Under Test

| Tool | Binary | Reports Latency |
|------|--------|-----------------|
| dnsperf | `dnsperf` | Yes |
| dnsperf-dnsworkbench (slice) | `dnsperf-dnsworkbench` | Yes |
| dnsperf-dnsworkbench (lencse) | `dnsperf-dnsworkbench` | Yes |
| dnspyre | `dnspyre` | Yes |
| dnspyre-dnsworkbench | `dnspyre-dnsworkbench` | Yes |
| dns64perf++ | `dns64perf++` | Yes |
| dns64perfpp-dnsworkbench | `dns64perfpp-dnsworkbench` | Yes |
| kxdpgun | `kxdpgun` | No |
| kxdpgun-dnsworkbench | `kxdpgun-dnsworkbench` | Yes |

## Setup

### Requirements

- Python 3.10+
- SSH key-based authentication between client and server hosts
- Tool binaries installed on the client host
- `dns_responder` installed on the server host

```sh
pip install -r requirements.txt
```

### Configuration

Edit `config.yaml` with your environment:

```yaml
hosts:
  server: user@server-host    # SSH target for the server
  clients:                    # one or more load-generation hosts (or localhost).
    - localhost               # Target QPS is split evenly across all clients.
    # - user@client-2

resolver: "10.0.0.1"          # IP address tools send DNS queries to
server_interface: eth0         # Network interface for dns_responder
client_interface: eth0         # Network interface for kxdpgun

input_files:
  dnsperf: /path/to/dnsperf_input
  dnspyre: /path/to/dnspyre_input
```

All parameters can also be overridden via CLI flags (see `--help` on each script).

## Scripts

### Script 1: Maximum Throughput Discovery

Determines the maximum sustainable QPS for each tool by ramping up the target QPS and measuring achieved QPS via `dns_responder` on the server.

```sh
python3 scripts/max_throughput.py --server user@server --resolver 10.0.0.1
```

Key options:

```
--start-qps N       Starting QPS (default: 200000)
--qps-step N        QPS increment per step (default: 10000)
--max-qps N         Maximum QPS to test (default: 5000000)
--trials N          Trials per QPS level (default: 1)
--recieve-only      Run dns_responder in receive-only mode (no responses sent)
```

**Output:** CSV/JSON with requested vs. achieved QPS per tool, plus a chart.

### Script 2: QPS Accuracy Evaluation

Measures how accurately each tool achieves a specified QPS using round-robin scheduling and `dns_responder` per-packet timestamps.

```sh
python3 scripts/qps_accuracy.py --server user@server --resolver 10.0.0.1
```

Key options:

```
--accuracy-min-qps N    Minimum QPS (default: 100000)
--accuracy-max-qps N    Maximum QPS (default: 2000000)
--accuracy-step N       QPS step size (default: 50000)
--trials N              Trials per QPS per tool (default: 10)
```

Accuracy is computed at three granularities: **1s**, **100ms**, and **10ms** intervals. For each interval, the framework reports mean QPS, standard deviation, and maximum deviation from the target.

**Output:** CSV/JSON with per-interval accuracy metrics, plus charts for mean, stddev, and max deviation.

### Script 3: Load Generator Impact Analysis

Evaluates how load generator choice affects DNS benchmarking results by running all tools against real DNS server implementations.

```sh
python3 scripts/load_impact.py --server user@server --resolver 10.0.0.1
```

Key options:

```
--impact-min-qps N      Minimum QPS (default: 100000)
--impact-max-qps N      Maximum QPS (default: 2000000)
--impact-qps-step N     QPS step size (default: 50000)
--impact-trials N       Trials per test (default: 3)
--dns-services NAME...  DNS services to test (default: from config.yaml)
--collectl            Run collectl on the DNS server during each tool invocation and save the trail (disabled by default)
--no-collectl         Explicitly disable collectl monitoring
```

DNS services are managed via `start_dns_service.sh` / `stop_dns_service.sh` on the server host.

**Output:** CSV/JSON with latency, answer rate, and QPS data per tool per DNS server, plus comparative charts and a 99.99% answer rate threshold summary.

#### Server resource monitoring with collectl

When enabled, the load impact test samples the **DNS server host** with `collectl` during every tool run, so CPU, memory, and network usage can be correlated with the latency/answer-rate results. This makes it possible to tell whether a DNS server is CPU-bound, saturating its NIC, etc., at a given offered QPS.

Enable it in `config.yaml` under `script3`:

```yaml
script3:
  collectl: true        # enable per-run collectl sampling on the server
  collectl_margin: 5    # seconds of warm-up/cool-down padding around each run
```

How it works:

- For each `(dns_service, tool, qps, trial)` run, `collectl -scndm --plot` is started on the server over SSH for `runtime + 2 * collectl_margin` seconds.
- The framework waits `collectl_margin` seconds so sampling is warm before the load tool starts, then runs the tool, then collects the trail file back via SCP.
- During parsing, the first and last `collectl_margin` samples (1 sample/sec) are dropped so only the steady-state window is aggregated.
- For each metric the **median** and **peak** are recorded. `collectl` runs only on the server host (no client-side agent) and must be installed there.

Metrics captured per run (subsystems `-scndm`: CPU, network, disk, memory):

| Group | Metrics |
|-------|---------|
| CPU | total %, user %, sys % |
| Memory | used, total, free, cached (MB) |
| Network | RX/TX KB/s (and combined), RX/TX packets/s |

If `collectl` fails to start, isn't installed, or a trail file can't be parsed, the run continues without resource data (a warning is logged) — the latency/answer-rate results are unaffected.

**Output:** the median/peak columns are appended to `results.csv` / `results.json`, raw per-run trail files are saved under `load_impact/collectl/`, and per-service CPU/memory/network resource charts are generated alongside the latency/answer-rate charts.

### Max Sustainable QPS Evaluation

Determines the highest QPS at which a DNS server still satisfies a configurable answer-rate threshold. Unlike Script 1/3, which sweep a fixed QPS ladder, this converges on the knee and reduces the ammount of QPS values that need to be tested.

```sh
python3 scripts/max_sustainable_qps.py --config configs/config_resolver.yaml \
  --dns-service resolver_unbound --tool kxdpgun-dnsworkbench
```

Exactly **one** DNS service and **one** load-generation tool per invocation. The service is started before the search and stopped afterwards, as in Script 3.

The search runs in two phases over QPS *indices*, so every QPS tested is an exact integer multiple of `min_qps_step`:

1. **Exponential ramp** — start at `initial_qps` and double until a level fails, establishing an upper bound. Levels are clamped to `max_qps`; if the ceiling itself passes, the search finishes early and the result is reported as a **lower bound**.
2. **Binary search** — narrow between the last passing and first failing level until they meet.

Levels already resolved in phase 1 are never re-tested in phase 2.

A **level** passes when `min_passes` of up to `num_trials` trials each reach the answer-rate threshold. Each level terminates as soon as its outcome is determined: with `num_trials: 10` and `min_passes: 9`, a level passes on the 9th passing trial and fails on the 2nd failing one.

Configure it under `max_sustainable_qps`:

```yaml
max_sustainable_qps:
  initial_qps: 100000          # starting point for the ramp; must divide by min_qps_step
  min_qps_step: 10000          # search resolution
  max_qps: 3000000             # ceiling the search never exceeds
  num_trials: 10               # max trials per level
  min_passes: 9                # passing trials needed for the level to pass
  trial_duration: 60           # seconds per trial
  answer_rate_threshold: 99.9  # percent of queries that must be answered NOERROR
  min_qps_fidelity_pct: 99.0   # warn if the tool sends less than this share of the requested load
  clear_cache: false           # clear the resolver cache before every trial
  warmup_cache: false          # pre-populate the cache before every trial
  collectl: false              # sample the server during every trial
  collectl_margin: 5
```

Every key can be overridden on the CLI (`--initial-qps`, `--min-qps-step`, `--max-qps`, `--num-trials`, `--min-passes`, `--trial-duration`, `--answer-rate-threshold`, `--clear-cache` / `--no-clear-cache`, `--warmup-cache` / `--no-warmup-cache`, `--collectl` / `--no-collectl`, `--collectl-margin`). Parameters are validated up front, and an invalid combination exits with status 2 before any load is generated.

`--simulate-max-qps N` runs the search with no remote execution at all, treating a level as passing iff its QPS is `<= N`. Useful for checking the search parameters (how many levels a given `initial_qps`/`min_qps_step` will visit, and therefore how long a real run will take) without touching the testbed.

Unlike Script 3, `clear_cache` and `warmup_cache` are independent and apply **per trial**: when both are set the cache is cleared and then warmed before every trial, giving each trial an identical starting state.

**Output:** three CSVs, rewritten after every level so an interrupted run still leaves complete data on disk.

- `trial_results.csv` — one row per trial: `dns_service, tool, target_qps, trial, achieved_qps, queries_sent, queries_completed, queries_lost, answer_rate_pct`, plus `passed`, `qps_fidelity_pct`, `status`, and latency/collectl columns.
- `level_tests.csv` — one row per QPS level: `target_qps, num_trials, num_passes, num_fails, average_achieved_qps, average_answer_rate_pct, passed`. `num_trials` is the number actually run (early termination), with the configured cap in `max_trials`.
- `search_summary.csv` / `.json` — one row: `max_qps_passed, max_qps_tested, num_qps_values_tested`, plus the search parameters, `hit_max_qps_ceiling`, `total_trials_run`, and `search_duration_s`.

`answer_rate_pct` counts only **NOERROR** replies, so SERVFAIL responses count as unanswered. `qps_fidelity_pct` is the share of the requested queries the tool actually sent; when it drops below `min_qps_fidelity_pct` a warning is logged, because a load generator that cannot reach the requested rate will show a high answer rate that overstates server capacity. It is advisory and never affects pass/fail.

## Common Options

All scripts share these flags:

```
--config FILE            Path to config YAML (default: config.yaml)
--server USER@HOST       Server host for SSH
--clients HOST [HOST ...] Load-generation hosts; target QPS split evenly across them (default: localhost)
--resolver IP            DNS resolver IP
--tools TOOL [TOOL ...]  Subset of tools to test
--output-dir DIR         Output directory (default: results/)
--runtime N              Test duration in seconds (default: 10)
--threads N              Number of threads for load testing tool (default: 20)
--dns-responder-batch-size N  Batch size for dns_responder (default: from config.yaml)
--recieve-only           Run dns_responder in receive-only mode (no responses sent)
--dry-run                Print commands without executing
```

## Output Structure

```
results/
├── max_throughput/
│   ├── raw/                     # Raw stdout/stderr per run
│   ├── results.csv
│   ├── results.json
│   └── charts/
├── qps_accuracy/
│   ├── raw/
│   ├── timestamps/              # dns_responder timestamp files
│   ├── results.csv
│   ├── results.json
│   └── charts/
├── load_impact/
│   ├── raw/
│   ├── collectl/                # raw collectl trail files per run (if enabled)
│   ├── results.csv
│   ├── results.json
│   └── charts/
└── max_sustainable_qps/
    ├── raw/
    ├── collectl/
    ├── trial_results.csv        # one row per trial
    ├── level_tests.csv          # one row per QPS level
    ├── search_summary.csv       # one row: the max sustainable QPS
    └── search_summary.json
```

## Tool Names for --tools

```
dnsperf
dnsperf-dnsworkbench-slice
dnsperf-dnsworkbench-lencse
dnspyre
dnspyre-workbench
dns64perf++
dns64perfpp-dnsworkbench
kxdpgun
kxdpgun-dnsworkbench
```