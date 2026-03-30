# AF_XDP DNS Response Generator

High-performance authoritative DNS response generator for benchmarking DNS load-testing tools. Uses AF_XDP for zero-copy packet processing, targeting 3+ million packets per second (Mpps).

## Architecture

### Packet Flow

```
NIC RX → XDP filter (UDP:53) → AF_XDP socket → Userspace worker thread
           ↓                                          ↓
     XDP_PASS (non-DNS)                    Parse DNS question (in-place)
                                                      ↓
                                           Select precomputed answer template
                                                      ↓
                                           Swap L2/L3/L4 headers in-place
                                                      ↓
                                           Append answer, fix lengths/checksums
                                                      ↓
NIC TX ← AF_XDP TX ring ← Submit same UMEM frame (zero-copy)
```

### Threading Model

- **N worker threads**: one per NIC RX queue, CPU-pinned to match IRQ affinity
- **Main thread**: handles signals (SIGINT/SIGTERM), manages duration timer
- Each worker thread owns its own UMEM, AF_XDP socket, and stats counters — **zero cross-thread synchronization**

### Counter Design

Per-thread `struct thread_stats` (cache-line aligned, 128 bytes):
- `rx_packets`, `tx_packets`, `rx_bytes`, `tx_bytes`
- `rx_drops`, `parse_errors`
- Per-type: `type_a`, `type_aaaa`, `type_cname`, `type_mx`, `type_https`, `type_other`

All counters are plain `uint64_t` — no atomics, no locks. Aggregated after threads join at end of run.

### Supported Query Types

| Type  | Response                                   |
|-------|--------------------------------------------|
| A     | `127.0.0.1`                                |
| AAAA  | `::1`                                      |
| CNAME | `test.local.`                              |
| MX    | `mail.local.` (priority 10)                |
| HTTPS | SvcPriority=1, TargetName="." (RFC 9460)   |
| Other | RCODE=NOTIMP (not implemented)             |

## Build

### Prerequisites

Ubuntu 22.04+ with kernel 5.15+:

```sh
sudo apt install clang llvm gcc make pkg-config \
    libbpf-dev libxdp-dev libelf-dev zlib1g-dev \
    linux-headers-$(uname -r)
```

### Compile

```sh
make        # builds dns_responder binary + xdp/xdp_dns_redirect.o
make clean  # remove build artifacts
```

Or use the install script on CloudLab:

```sh
./install.sh
```

## Usage

```
sudo ./dns_responder -i <interface> [options]
```

### Options

| Flag | Description | Default |
|------|-------------|---------|
| `-i, --interface` | Network interface (required) | — |
| `-q, --queues N` | Number of RX queues/threads | auto-detect |
| `-d, --duration N` | Run duration in seconds | 0 (until SIGINT) |
| `-o, --output FILE` | Write stats to file | stdout only |
| `-z, --zerocopy` | Force zero-copy mode | — |
| `-Z, --no-zerocopy` | Force copy mode | — |
| `-b, --batch-size N` | RX/TX batch size | 64 |
| `-f, --frame-count N` | UMEM frames per queue | 4096 |
| `-t, --timestamps FILE` | Write per-packet RX timestamps (ns) to file | — |
| `-T, --ts-range` | Track min/max RX timestamps, report actual QPS | — |
| `-N, --nxdomain` | Always return NXDOMAIN (fast path, minimal stats)| — |
| `-C, --count-only` | Count packets only, don't respond (ultra-fast mode) | — |
| `-x, --xdp-prog FILE` | Path to XDP object file | `./xdp/xdp_dns_redirect.o` |
| `-v, --verbose` | Print per-thread breakdown | off |

### Examples

Basic usage (auto-detect queues, run until Ctrl+C):
```sh
sudo ./dns_responder -i eth1
```

Fixed duration with stats output:
```sh
sudo ./dns_responder -i eth1 -d 30 -o results.txt -v
```

Track actual QPS based on first-to-last packet timing:
```sh
sudo ./dns_responder -i eth1 -d 30 -T
```

Count-only mode (no responses, minimal overhead, compatible with kxdpgun):
```sh
sudo ./dns_responder -i eth1 -C -T -d 30
```

Specify queue count (match NIC RSS config):
```sh
sudo ethtool -L eth1 combined 4
sudo ./dns_responder -i eth1 -q 4
```

## Example Usage with Load Testers

### dnsperf

```sh
# On the load generator host:
dnsperf -s <responder_ip> -d queryfile.txt -l 30 -c 8 -Q 1000000

# On the responder host:
sudo ./dns_responder -i eth1 -d 35
```

### kxdpgun

For raw packet counting (bypassing DNS response generation entirely):
```sh
# On the load generator host:
sudo kxdpgun -t 30 -Q 3000000 -i eth1 <responder_ip>

# On the responder host (count-only mode):
sudo ./dns_responder -i eth1 -C -T -d 35
```

For full DNS response generation:
```sh
# On the load generator host:
sudo kxdpgun -t 30 -Q 3000000 -i eth1 <responder_ip>

# On the responder host:
sudo ./dns_responder -i eth1 -T -d 35
```

### dns64perf++

```sh
# On the load generator host:
dns64perf++ <responder_ip> 53 <queryfile> 30 1000000

# On the responder host:
sudo ./dns_responder -i eth1 -d 35
```

## Timestamps File Format

When using `-t, --timestamps FILE`, the output is a plain text file with one nanosecond timestamp per line, sorted in chronological order:

```
# Per-packet RX timestamps (nanoseconds since start)
# Merge-sorted across 4 worker threads
182935
281074
383691
...
```

Each value is the `CLOCK_MONOTONIC` arrival time of an RX packet in nanoseconds relative to program start. Timestamps from all worker threads are merge-sorted into a single globally ordered sequence.

This can be used to compute inter-packet arrival times, detect microbursts, or build packet-rate histograms:

```sh
# Compute inter-arrival times (nanoseconds)
grep -v '^#' timestamps.txt | awk 'NR>1{print $1-prev}{prev=$1}'

# Packets per 1ms bin
grep -v '^#' timestamps.txt | awk '{print int($1/1000000)}' | uniq -c
```

## Timestamp Range Mode (`-T`)

The `-T` / `--ts-range` flag enables lightweight min/max timestamp tracking with no per-packet buffer allocation. Each worker thread records only the earliest and latest `CLOCK_MONOTONIC` RX timestamps. After the run, the global min and max are computed across all workers to determine the actual traffic window — the time between the first and last packet received.

This is used to calculate QPS based on when traffic was actually flowing, excluding idle startup/shutdown time that inflates the wall-clock duration:

```
Actual traffic window: 29.847s (first pkt to last pkt)
  RX QPS:          1503842 qps (1.50 Mqps)
  TX QPS:          1503791 qps (1.50 Mqps)
```

Unlike `-t` (which writes every per-packet timestamp to a file), `-T` has negligible overhead. In standard response mode, it uses one per-packet `clock_gettime` call (~30 ns). In count-only mode (`-C -T`), the overhead is even lighter — just one `clock_gettime` per batch (~50–100 µs apart), enabling accurate measurement even at 5M+ pps while maintaining sub-millisecond window accuracy.

## Count-Only Mode (`-C`)

The `-C` / `--count-only` flag enables an ultra-minimal mode designed for high-performance load measurement scenarios. Instead of generating DNS responses, the responder:

1. Receives packets on the AF_XDP socket
2. Counts them per-thread
3. Returns frames to the fill ring
4. Produces aggregate RX statistics

This mode is optimized for:
- Measuring raw packet reception rates with kxdpgun or other AF_XDP load generators
- Eliminating response generation overhead to focus on NIC/kernel performance
- Accurate QPS tracking when combined with `-T` (one `clock_gettime` per batch, ~0.06% overhead at 5M pps)

Unlike `--nxdomain`, count-only mode has no packet processing logic (no DNS header parsing, no answer templates), making it the absolute fastest path for pure packet counting.

## Benchmark Methodology

### Validating PPS Accuracy

1. Run `dns_responder` with `-d <duration>` and `-o stats.txt`
2. Run the load generator for the same duration
3. Compare:
   - `dns_responder` TX count should match load generator's "responses received" count
   - `dns_responder` RX count should match load generator's "queries sent" count
   - Discrepancy < 0.1% indicates accurate accounting

### Detecting Packet Loss

```
Packet loss = (dns_responder RX - dns_responder TX) / dns_responder RX × 100%
```

If TX < RX, packets were dropped during response generation (unlikely unless UMEM exhausted).

For network-level loss, compare with the `packet_capture/dns_timestamp.py` eBPF tool:
```sh
# Run simultaneously on the responder host:
sudo python3 ../packet_capture/dns_timestamp.py -i eth1 -o capture.dtrace -d 35

# Analyze:
python3 ../packet_capture/dns_timestamp_analyze.py capture.dtrace
```

Cross-reference the packet capture's RX/TX counts with `dns_responder` stats and the load generator's report. Three independent measurement points allow triangulating where loss occurs (network TX, network RX, or application).

### Detecting Skew

If the load generator reports a different query rate than `dns_responder` RX rate, suspect:
- NIC RSS misconfiguration (queries landing on queues without AF_XDP sockets)
- XDP program not attached correctly (check `ip link show dev <iface>`)
- Ring buffer overflow (increase `--frame-count`)

## Performance Tuning

### NIC Configuration

```sh
# Set RSS queues to match thread count
sudo ethtool -L eth1 combined 4

# Distribute RSS evenly
sudo ethtool -X eth1 equal 4

# Increase ring buffer sizes
sudo ethtool -G eth1 rx 4096 tx 4096
```

### IRQ Affinity

Pin NIC IRQs to match worker thread CPUs:
```sh
# Find IRQ numbers for the interface
grep eth1 /proc/interrupts

# Set affinity (example: IRQ 48 to CPU 0)
echo 0 | sudo tee /proc/irq/48/smp_affinity_list
```

### System Tuning

```sh
# Enable hugepages for UMEM (optional, automatic fallback)
echo 64 | sudo tee /sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages

# Disable CPU frequency scaling
for cpu in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
    echo performance | sudo tee $cpu
done
```
