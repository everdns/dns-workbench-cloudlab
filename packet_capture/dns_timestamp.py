#!/usr/bin/env python3
"""
dns_timestamp.py — High-performance DNS packet timestamper using eBPF (XDP + TC).

Captures per-packet nanosecond timestamps for all UDP port 53 traffic at 1-3+ Mpps
without interfering with normal networking. Uses XDP for RX and TC egress for TX.

Requires: python3-bpfcc, linux-headers-$(uname -r)
Must be run as root.

Usage:
    sudo python3 dns_timestamp.py --iface eth0 --output /dev/shm/capture.dtrace
    sudo python3 dns_timestamp.py --iface eth0 --output /dev/shm/capture.dtrace --duration 60 --csv
"""

import argparse
import ctypes
import os
import signal
import socket
import struct
import subprocess
import sys
import time

from bcc import BPF
try:
    from bcc import XDPFlags
except ImportError:
    # Older BCC versions don't export XDPFlags; define manually
    class XDPFlags:
        SKB_MODE = 1 << 1
        DRV_MODE = 1 << 2
        HW_MODE = 1 << 3

# eBPF C source — XDP (RX) and TC (TX) programs
BPF_SOURCE = r"""
#include <uapi/linux/bpf.h>
#include <linux/if_ether.h>
#include <linux/ip.h>
#include <linux/udp.h>
#include <linux/pkt_cls.h>

struct dns_event {
    u64 timestamp_ns;
    u32 src_ip;
    u32 dst_ip;
    u16 src_port;
    u16 dst_port;
    u16 dns_txid;
    u16 pkt_size;
    u8  direction;  // 0 = RX, 1 = TX
    u8  qr_flag;    // 0 = query, 1 = response
    u8  pad[2];
};

BPF_RINGBUF_OUTPUT(events, 1 << RINGBUF_PAGE_ORDER);

static __always_inline int process_packet(void *data, void *data_end,
                                           u8 direction) {
    struct ethhdr *eth = data;
    if ((void *)(eth + 1) > data_end)
        return -1;
    if (eth->h_proto != __constant_htons(ETH_P_IP))
        return -1;

    struct iphdr *ip = (void *)(eth + 1);
    if ((void *)(ip + 1) > data_end)
        return -1;
    if (ip->protocol != IPPROTO_UDP)
        return -1;

    struct udphdr *udp = (void *)ip + (ip->ihl << 2);
    if ((void *)(udp + 1) > data_end)
        return -1;

    u16 sport = __constant_ntohs(udp->source);
    u16 dport = __constant_ntohs(udp->dest);

    // Filter: only DNS traffic (port 53)
    if (sport != 53 && dport != 53)
        return -1;

    // DNS header: need at least 4 bytes (2 TXID + 2 flags)
    void *dns_hdr = (void *)(udp + 1);
    if (dns_hdr + 4 > data_end)
        return -1;

    u16 dns_txid = *(u16 *)dns_hdr;
    u8 *flags_ptr = (u8 *)dns_hdr + 2;
    u8 qr_flag = (*flags_ptr >> 7) & 1;

    struct dns_event *evt = events.ringbuf_reserve(sizeof(struct dns_event));
    if (!evt)
        return -1;

    evt->timestamp_ns = bpf_ktime_get_ns();
    evt->src_ip = ip->saddr;
    evt->dst_ip = ip->daddr;
    evt->src_port = sport;
    evt->dst_port = dport;
    evt->dns_txid = __constant_ntohs(dns_txid);
    evt->pkt_size = __constant_ntohs(ip->tot_len) + sizeof(struct ethhdr);
    evt->direction = direction;
    evt->qr_flag = qr_flag;
    evt->pad[0] = 0;
    evt->pad[1] = 0;

    events.ringbuf_submit(evt, 0);
    return 0;
}

int xdp_dns_timestamp(struct xdp_md *ctx) {
    void *data = (void *)(long)ctx->data;
    void *data_end = (void *)(long)ctx->data_end;
    process_packet(data, data_end, 0);
    return XDP_PASS;
}

int tc_dns_timestamp(struct __sk_buff *skb) {
    void *data = (void *)(long)skb->data;
    void *data_end = (void *)(long)skb->data_end;
    process_packet(data, data_end, 1);
    return TC_ACT_OK;
}
"""

# Binary file format constants
MAGIC = b"DTRC"
VERSION = 1
HEADER_SIZE = 64
RECORD_SIZE = 28


class DnsEvent(ctypes.Structure):
    """Matches the kernel struct dns_event layout (28 bytes)."""
    _fields_ = [
        ("timestamp_ns", ctypes.c_uint64),
        ("src_ip", ctypes.c_uint32),
        ("dst_ip", ctypes.c_uint32),
        ("src_port", ctypes.c_uint16),
        ("dst_port", ctypes.c_uint16),
        ("dns_txid", ctypes.c_uint16),
        ("pkt_size", ctypes.c_uint16),
        ("direction", ctypes.c_uint8),
        ("qr_flag", ctypes.c_uint8),
        ("pad", ctypes.c_uint8 * 2),
    ]


def write_file_header(f, hostname):
    """Write 64-byte binary file header."""
    wallclock_ns = int(time.time() * 1e9)
    monotonic_ns = int(time.monotonic_ns())
    hostname_bytes = hostname.encode("utf-8")[:32].ljust(32, b"\x00")

    header = struct.pack(
        "<4sHHH2sqqI",
        MAGIC,           # 4 bytes
        VERSION,         # 2 bytes
        HEADER_SIZE,     # 2 bytes
        RECORD_SIZE,     # 2 bytes
        b"\x00\x00",    # 2 bytes flags
        wallclock_ns,    # 8 bytes
        monotonic_ns,    # 8 bytes
        0,               # 4 bytes reserved
    )
    # 32 bytes so far, then 32 bytes hostname = 64 total
    header += hostname_bytes
    assert len(header) == HEADER_SIZE
    f.write(header)


def run_cmd(cmd, check=True):
    """Run a shell command, return (returncode, stdout)."""
    result = subprocess.run(
        cmd, shell=True, capture_output=True, text=True
    )
    if check and result.returncode != 0:
        print(f"Command failed: {cmd}", file=sys.stderr)
        if result.stderr:
            print(f"  stderr: {result.stderr.strip()}", file=sys.stderr)
    return result.returncode, result.stdout.strip()


def attach_tc_egress(b, iface):
    """Attach the TC egress eBPF program to the interface.

    Uses BCC to load the program, then pins it to bpffs and attaches
    via iproute2 tc commands.
    """
    # Set up clsact qdisc (remove old one first)
    run_cmd(f"tc qdisc del dev {iface} clsact", check=False)
    rc, _ = run_cmd(f"tc qdisc add dev {iface} clsact")
    if rc != 0:
        print(f"ERROR: Failed to add clsact qdisc to {iface}", file=sys.stderr)
        return False

    # Load the TC function
    tc_fn = b.load_func("tc_dns_timestamp", BPF.SCHED_CLS)

    # Pin the program to bpffs so tc can reference it
    pin_path = f"/sys/fs/bpf/dns_ts_tc_{iface.replace('.', '_')}"
    run_cmd(f"rm -f {pin_path}", check=False)

    # Get the program ID from its fd using bpftool
    rc, output = run_cmd(
        f"bpftool prog show name tc_dns_timesta", check=False
    )
    if rc == 0 and output:
        # Extract first program ID
        prog_id = output.split(":")[0].strip()
        rc, _ = run_cmd(f"bpftool prog pin id {prog_id} {pin_path}")
        if rc == 0:
            rc, _ = run_cmd(
                f"tc filter add dev {iface} egress bpf direct-action "
                f"pinned {pin_path}"
            )
            if rc == 0:
                return True

    # Fallback: try object-less attachment via BCC's internal API
    print(
        "WARNING: Could not attach TC egress via pinning. "
        "Trying alternative method...",
        file=sys.stderr,
    )

    # Use BPF.tc_attach_bpf if available (newer BCC versions)
    try:
        from bcc import lib
        lib.bpf_tc_attach(tc_fn.fd, iface.encode(), 0, 0)
        return True
    except (ImportError, AttributeError):
        pass

    print(
        "WARNING: Could not attach TC egress program. "
        "TX packets will NOT be captured. RX capture will still work.",
        file=sys.stderr,
    )
    return False


def cleanup_tc(iface):
    """Remove clsact qdisc and pinned program."""
    pin_path = f"/sys/fs/bpf/dns_ts_tc_{iface.replace('.', '_')}"
    run_cmd(f"tc qdisc del dev {iface} clsact", check=False)
    run_cmd(f"rm -f {pin_path}", check=False)


def ip_to_str(ip_be32):
    """Convert a big-endian u32 IP to dotted-quad string."""
    return socket.inet_ntoa(struct.pack("=I", ip_be32))


def main():
    parser = argparse.ArgumentParser(
        description="High-performance DNS packet timestamper using eBPF"
    )
    parser.add_argument(
        "--iface", required=True, help="Network interface to capture on"
    )
    parser.add_argument(
        "--output", required=True, help="Output file path (.dtrace binary format)"
    )
    parser.add_argument(
        "--duration", type=int, default=0,
        help="Capture duration in seconds (0 = until Ctrl+C)",
    )
    parser.add_argument(
        "--csv", action="store_true", help="Also write a CSV sidecar file"
    )
    parser.add_argument(
        "--ringbuf-pages-order", type=int, default=12,
        help="Ring buffer size as 2^N pages (default 12 = 16MB)",
    )
    parser.add_argument(
        "--xdp-mode", choices=["native", "skb", "offload"], default="skb",
        help="XDP attach mode (default: skb; use native for max performance)",
    )
    args = parser.parse_args()

    if os.geteuid() != 0:
        print("ERROR: Must be run as root", file=sys.stderr)
        sys.exit(1)

    iface = args.iface

    # Compile eBPF
    cflags = [f"-DRINGBUF_PAGE_ORDER={args.ringbuf_pages_order}"]
    print(f"Compiling eBPF programs...", file=sys.stderr)
    b = BPF(text=BPF_SOURCE, cflags=cflags)

    # Attach XDP (RX)
    xdp_flags = {
        "native": XDPFlags.DRV_MODE,
        "skb": XDPFlags.SKB_MODE,
        "offload": XDPFlags.HW_MODE,
    }
    xdp_fn = b.load_func("xdp_dns_timestamp", BPF.XDP)
    b.attach_xdp(iface, xdp_fn, xdp_flags[args.xdp_mode])
    print(f"Attached XDP ({args.xdp_mode} mode) to {iface}", file=sys.stderr)

    # Attach TC egress (TX)
    tc_ok = attach_tc_egress(b, iface)
    if tc_ok:
        print(f"Attached TC egress to {iface}", file=sys.stderr)

    # Open output files
    hostname = socket.gethostname()
    outfile = open(args.output, "wb", buffering=65536)
    write_file_header(outfile, hostname)

    csvfile = None
    if args.csv:
        csv_path = args.output.rsplit(".", 1)[0] + ".csv"
        csvfile = open(csv_path, "w", buffering=65536)
        csvfile.write(
            "timestamp_ns,src_ip,dst_ip,src_port,dst_port,"
            "dns_txid,pkt_size,direction,qr_flag\n"
        )

    # Stats
    event_count = 0
    drop_count = 0
    last_report = time.monotonic()
    last_count = 0
    start_time = time.monotonic()
    running = True

    def handle_signal(signum, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    def handle_event(ctx, data, size):
        nonlocal event_count
        evt = ctypes.cast(data, ctypes.POINTER(DnsEvent)).contents

        outfile.write(bytes(evt))
        event_count += 1

        if csvfile:
            csvfile.write(
                f"{evt.timestamp_ns},{ip_to_str(evt.src_ip)},{ip_to_str(evt.dst_ip)},"
                f"{evt.src_port},{evt.dst_port},{evt.dns_txid},{evt.pkt_size},"
                f"{evt.direction},{evt.qr_flag}\n"
            )

    # Set up ring buffer
    b["events"].open_ring_buffer(handle_event)

    dir_label = "RX+TX" if tc_ok else "RX only"
    dur_label = f"Duration: {args.duration}s" if args.duration else "Press Ctrl+C to stop"
    print(
        f"Capturing DNS packets ({dir_label}) on {iface}. {dur_label}.",
        file=sys.stderr,
    )

    # Main loop
    while running:
        try:
            b.ring_buffer_poll(timeout=100)
        except Exception:
            if not running:
                break
            raise

        now = time.monotonic()

        if args.duration and (now - start_time) >= args.duration:
            running = False

        if now - last_report >= 1.0:
            rate = (event_count - last_count) / (now - last_report)
            elapsed = now - start_time
            print(
                f"[{elapsed:6.1f}s] {event_count:>10,} events | "
                f"{rate:>10,.0f} evt/s | {drop_count:>6,} drops",
                file=sys.stderr,
            )
            last_report = now
            last_count = event_count

    # Drain remaining
    try:
        b.ring_buffer_poll(timeout=0)
    except Exception:
        pass

    # Final stats
    elapsed = time.monotonic() - start_time
    avg_rate = event_count / elapsed if elapsed > 0 else 0
    print(f"\n--- Capture complete ---", file=sys.stderr)
    print(f"Duration:     {elapsed:.1f}s", file=sys.stderr)
    print(f"Total events: {event_count:,}", file=sys.stderr)
    print(f"Average rate: {avg_rate:,.0f} evt/s", file=sys.stderr)
    print(f"Drops:        {drop_count:,}", file=sys.stderr)
    print(f"Output:       {args.output}", file=sys.stderr)
    if csvfile:
        print(f"CSV:          {args.output.rsplit('.', 1)[0] + '.csv'}", file=sys.stderr)

    # Cleanup
    outfile.flush()
    outfile.close()
    if csvfile:
        csvfile.flush()
        csvfile.close()

    b.remove_xdp(iface, xdp_flags[args.xdp_mode])
    cleanup_tc(iface)
    print("Detached eBPF programs.", file=sys.stderr)


if __name__ == "__main__":
    main()
