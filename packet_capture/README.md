# DNS Packet Capture & Timestamping

High-performance per-packet timestamping for DNS traffic (UDP port 53) at 1-3+ million packets per second using eBPF (XDP + TC).

## How it works

- **XDP program** (RX): fires before kernel stack processes each packet — filters UDP port 53, records nanosecond timestamp + metadata to a BPF ring buffer, passes packet through normally
- **TC egress program** (TX): fires on outgoing packets — same filtering and recording
- **Userspace reader**: drains the ring buffer and writes compact 28-byte binary records to disk
- **Analysis script**: reads capture files, matches queries to responses by TXID, computes RTT distributions and drop rates

The eBPF programs do not drop, redirect, or modify packets. Normal DNS operation is completely unaffected.

## Install

```bash
bash packet_capture/install.sh
```

Installs: `bpfcc-tools`, `python3-bpfcc`, `linux-headers`, `python3-numpy`

## Capture

```bash
# Basic capture (Ctrl+C to stop)
sudo python3 packet_capture/dns_timestamp.py --iface eth0 --output /dev/shm/capture.dtrace

# Timed capture with CSV sidecar
sudo python3 packet_capture/dns_timestamp.py --iface eth0 --output /dev/shm/capture.dtrace --duration 60 --csv

# Native XDP mode for max performance (requires driver support)
sudo python3 packet_capture/dns_timestamp.py --iface enp130s0f0 --output /dev/shm/capture.dtrace --xdp-mode native
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--iface` | required | Network interface |
| `--output` | required | Output file path (.dtrace) |
| `--duration` | 0 | Seconds to capture (0 = until Ctrl+C) |
| `--csv` | off | Also write CSV sidecar |
| `--xdp-mode` | skb | `skb` (compatible), `native` (fast), `offload` (NIC) |
| `--ringbuf-pages-order` | 12 | Ring buffer = 2^N pages (12 = 16MB) |

### Live output

```
Attached XDP (skb mode) to eth0
Attached TC egress to eth0
Capturing DNS packets (RX+TX) on eth0. Press Ctrl+C to stop.
[   1.0s]      1,234 events |       1,234 evt/s |      0 drops
[   2.0s]      2,891 events |       1,657 evt/s |      0 drops
```

## Analyze

```bash
# Single node
python3 packet_capture/dns_timestamp_analyze.py --input /dev/shm/capture.dtrace

# Cross-node (client = load generator, server = DNS server)
python3 packet_capture/dns_timestamp_analyze.py \
  --client /dev/shm/loadgen.dtrace \
  --server /dev/shm/ns.dtrace \
  --output-dir ./results/
```

Output includes:
- Packet counts (RX/TX, query/response)
- Latency percentiles (p50, p75, p90, p95, p99, p99.9) in microseconds
- Query and response drop counts between nodes
- CSV exports

## Performance tips

- **Write to tmpfs**: Use `--output /dev/shm/capture.dtrace` to avoid disk I/O bottleneck. At 3 Mpps, a 60s capture is ~5 GB.
- **Pin to CPU**: `taskset -c 4 sudo python3 dns_timestamp.py ...` — use a core not busy with the DNS server or NIC interrupts.
- **Native XDP**: Use `--xdp-mode native` on interfaces with XDP driver support (ixgbe has it since kernel 4.12).
- **Ring buffer sizing**: If you see drops, increase with `--ringbuf-pages-order 14` (64MB).
- **Disable CSV during high-PPS capture**: The `--csv` flag adds per-packet string formatting overhead.

## Binary format (.dtrace)

**Header** (64 bytes):
| Offset | Size | Field |
|--------|------|-------|
| 0 | 4 | Magic: `DTRC` |
| 4 | 2 | Version (1) |
| 6 | 2 | Header size (64) |
| 8 | 2 | Record size (28) |
| 10 | 2 | Flags (reserved) |
| 12 | 8 | Wall clock (ns, CLOCK_REALTIME at start) |
| 20 | 8 | Monotonic clock (ns, at start) |
| 28 | 4 | Reserved |
| 32 | 32 | Hostname (null-padded) |

**Records** (28 bytes each, flat array after header):
| Offset | Size | Field |
|--------|------|-------|
| 0 | 8 | Timestamp (ns, monotonic) |
| 8 | 4 | Source IP (network byte order) |
| 12 | 4 | Dest IP (network byte order) |
| 16 | 2 | Source port |
| 18 | 2 | Dest port |
| 20 | 2 | DNS transaction ID |
| 22 | 2 | Packet size (bytes) |
| 24 | 1 | Direction (0=RX, 1=TX) |
| 25 | 1 | QR flag (0=query, 1=response) |
| 26 | 2 | Padding |

## Timestamp accuracy

- `bpf_ktime_get_ns()` uses the TSC (Time Stamp Counter) on x86 — monotonic, nanosecond resolution, <100ns jitter in XDP context
- XDP fires before sk_buff allocation, so RX timestamps are as close to wire time as possible without hardware timestamping
- TC egress timestamps are taken just before the packet leaves the kernel
- Cross-node correlation requires NTP sync (~1ms) or PTP for sub-millisecond accuracy
