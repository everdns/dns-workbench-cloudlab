# DNS Load Testing Benchmark Framework

A Python framework for benchmarking and comparing DNS load testing tools in a controlled, reproducible two-host environment.

## Tools Under Test

| Tool | Binary | Reports Latency |
|------|--------|-----------------|
| dnsperf | `dnsperf` | Yes |
| dnsperf-workbench (slice) | `dnsperf-workbench` | Yes |
| dnsperf-workbench (lencse) | `dnsperf-workbench` | Yes |
| dnspyre | `dnspyre` | Yes |
| dnspyre-workbench | `dnspyre-dnsworkbench` | Yes |
| dns64perf++ | `dns64perf++` | Yes |
| dns64perfpp-workbench | `dns64perfpp-workbench` | Yes |
| kxdpgun | `kxdpgun` | No |

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
  client: localhost            # SSH target for the client (or localhost)

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

**Output:** CSV/JSON with requested vs. achieved QPS per tool, plus a chart. When `--trials N` is used with N > 1, each data point shows mean ± stddev error bars across trials.

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
```

DNS services are managed via `start_dns_service.sh` / `stop_dns_service.sh` on the server host. Available services: `bind-resolver`, `powerdns-resolver`, `knot-resolver`, `nsd-ns`, `unbound-resolver`.

**Output:** CSV/JSON with latency, answer rate, and QPS data per tool per DNS server, plus comparative charts and a 99.99% answer rate threshold summary.

## Common Options

All scripts share these flags:

```
--config FILE            Path to config YAML (default: config.yaml)
--server USER@HOST       Server host for SSH
--client USER@HOST       Client host (default: localhost)
--resolver IP            DNS resolver IP
--tools TOOL [TOOL ...]  Subset of tools to test
--output-dir DIR         Output directory (default: results/)
--runtime N              Test duration in seconds (default: 10)
--threads N              Number of threads (default: 20)
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
│       └── requested_vs_achieved.png
├── qps_accuracy/
│   ├── raw/
│   ├── timestamps/              # dns_responder timestamp files
│   ├── results.csv
│   ├── results.json
│   └── charts/
│       ├── accuracy_mean_1s.png
│       ├── accuracy_stddev_100ms.png
│       └── ...
└── load_impact/
    ├── raw/
    ├── results.csv
    ├── results.json
    └── charts/
        ├── bind-resolver_answer_rate.png
        ├── bind-resolver_latency.png
        └── threshold_summary.txt
```

## Architecture

```
benchmark/
├── config.py          # YAML + CLI config loading
├── remote.py          # SSH execution & SCP file transfer
├── dns_responder.py   # dns_responder lifecycle management
├── dns_servers.py     # DNS server start/stop
├── results.py         # Dataclasses, CSV/JSON export, timestamp analysis
├── charts.py          # matplotlib chart generation
└── tools/
    ├── base.py        # Abstract tool adapter
    ├── dnsperf.py
    ├── dnsperf_workbench.py   # slice + lencse variants
    ├── dnspyre.py
    ├── dnspyre_workbench.py
    ├── dns64perfpp.py
    ├── dns64perfpp_workbench.py
    └── kxdpgun.py
```

Each tool adapter implements `build_command()` (generates the shell command for a given config and target QPS) and `parse_output()` (extracts structured metrics from stdout).

## Tool Names for --tools

```
dnsperf
dnsperf-workbench-slice
dnsperf-workbench-lencse
dnspyre
dnspyre-workbench
dns64perf++
dns64perfpp-workbench
kxdpgun
```
