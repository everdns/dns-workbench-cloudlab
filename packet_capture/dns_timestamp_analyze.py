#!/usr/bin/env python3
"""
dns_timestamp_analyze.py — Post-hoc analysis of .dtrace capture files.

Reads binary .dtrace files produced by dns_timestamp.py, computes per-query
RTT, latency distributions, packet counts, and drop analysis.

Requires: python3, numpy (optional but recommended for large captures)

Usage:
    # Single-node stats
    python3 dns_timestamp_analyze.py --input capture.dtrace

    # Cross-node analysis (client = load generator, server = DNS server)
    python3 dns_timestamp_analyze.py --client loadgen.dtrace --server ns.dtrace --output-dir ./results/
"""

import argparse
import csv
import os
import socket
import struct
import sys
from collections import defaultdict

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

# Must match dns_timestamp.py
MAGIC = b"DTRC"
HEADER_SIZE = 64
RECORD_SIZE = 28

# struct dns_event: u64, u32, u32, u16, u16, u16, u16, u8, u8, 2x pad
RECORD_FMT = "<QII4HBBH"  # Q=u64, I=u32, H=u16, B=u8, trailing H covers pad
RECORD_STRUCT = struct.Struct("<QIIHHHHBBxx")  # xx = 2 pad bytes


def ip_to_str(ip_be32):
    return socket.inet_ntoa(struct.pack("=I", ip_be32))


def read_header(f):
    """Read and validate file header. Returns (wallclock_ns, monotonic_ns, hostname)."""
    raw = f.read(HEADER_SIZE)
    if len(raw) < HEADER_SIZE:
        print("ERROR: File too short for header", file=sys.stderr)
        sys.exit(1)

    magic = raw[:4]
    if magic != MAGIC:
        print(f"ERROR: Bad magic {magic!r}, expected {MAGIC!r}", file=sys.stderr)
        sys.exit(1)

    version, header_size, record_size = struct.unpack_from("<HHH", raw, 4)
    wallclock_ns, monotonic_ns = struct.unpack_from("<qq", raw, 12)
    hostname = raw[32:64].rstrip(b"\x00").decode("utf-8", errors="replace")

    return {
        "version": version,
        "record_size": record_size,
        "wallclock_ns": wallclock_ns,
        "monotonic_ns": monotonic_ns,
        "hostname": hostname,
    }


def read_events(filepath):
    """Read all events from a .dtrace file. Returns (header, events_list)."""
    with open(filepath, "rb") as f:
        header = read_header(f)
        data = f.read()

    n_events = len(data) // RECORD_SIZE
    if len(data) % RECORD_SIZE != 0:
        print(
            f"WARNING: {len(data) % RECORD_SIZE} trailing bytes in {filepath}",
            file=sys.stderr,
        )

    events = []
    for i in range(n_events):
        offset = i * RECORD_SIZE
        (ts, src_ip, dst_ip, src_port, dst_port, dns_txid,
         pkt_size, direction, qr_flag) = RECORD_STRUCT.unpack_from(data, offset)
        events.append({
            "timestamp_ns": ts,
            "src_ip": src_ip,
            "dst_ip": dst_ip,
            "src_port": src_port,
            "dst_port": dst_port,
            "dns_txid": dns_txid,
            "pkt_size": pkt_size,
            "direction": direction,  # 0=RX, 1=TX
            "qr_flag": qr_flag,     # 0=query, 1=response
        })

    return header, events


def read_events_numpy(filepath):
    """Read events using numpy for fast processing."""
    with open(filepath, "rb") as f:
        header = read_header(f)
        data = f.read()

    dt = np.dtype([
        ("timestamp_ns", "<u8"),
        ("src_ip", "<u4"),
        ("dst_ip", "<u4"),
        ("src_port", "<u2"),
        ("dst_port", "<u2"),
        ("dns_txid", "<u2"),
        ("pkt_size", "<u2"),
        ("direction", "u1"),
        ("qr_flag", "u1"),
        ("pad", "2u1"),
    ])

    n_events = len(data) // RECORD_SIZE
    arr = np.frombuffer(data[:n_events * RECORD_SIZE], dtype=dt)
    return header, arr


def percentiles(values, pcts=(50, 75, 90, 95, 99, 99.9)):
    """Compute percentiles. Values in nanoseconds, output in microseconds."""
    if HAS_NUMPY:
        vals = np.array(values) if not isinstance(values, np.ndarray) else values
        results = {}
        for p in pcts:
            results[f"p{p}"] = np.percentile(vals, p) / 1000.0
        return results
    else:
        sorted_v = sorted(values)
        n = len(sorted_v)
        results = {}
        for p in pcts:
            idx = int(p / 100.0 * n)
            idx = min(idx, n - 1)
            results[f"p{p}"] = sorted_v[idx] / 1000.0
        return results


def analyze_single(filepath):
    """Analyze a single capture file."""
    print(f"\n=== Analyzing {filepath} ===\n")

    if HAS_NUMPY:
        header, events = read_events_numpy(filepath)
        n = len(events)
        if n == 0:
            print("No events found.")
            return

        rx_mask = events["direction"] == 0
        tx_mask = events["direction"] == 1
        query_mask = events["qr_flag"] == 0
        resp_mask = events["qr_flag"] == 1

        n_rx = int(rx_mask.sum())
        n_tx = int(tx_mask.sum())

        ts = events["timestamp_ns"]
        duration_s = (ts[-1] - ts[0]) / 1e9 if n > 1 else 0
    else:
        header, events = read_events(filepath)
        n = len(events)
        if n == 0:
            print("No events found.")
            return

        n_rx = sum(1 for e in events if e["direction"] == 0)
        n_tx = sum(1 for e in events if e["direction"] == 1)
        duration_s = (events[-1]["timestamp_ns"] - events[0]["timestamp_ns"]) / 1e9 if n > 1 else 0

    print(f"Host:         {header['hostname']}")
    print(f"Total events: {n:,}")
    print(f"  RX:         {n_rx:,}")
    print(f"  TX:         {n_tx:,}")
    print(f"Duration:     {duration_s:.2f}s")
    if duration_s > 0:
        print(f"Avg rate:     {n / duration_s:,.0f} evt/s")
        print(f"  RX rate:    {n_rx / duration_s:,.0f} evt/s")
        print(f"  TX rate:    {n_tx / duration_s:,.0f} evt/s")

    # Compute server-side processing time: match RX queries to TX responses by TXID
    print(f"\n--- Server-side latency (RX query -> TX response, matched by TXID) ---")
    if HAS_NUMPY:
        rx_queries = events[rx_mask & query_mask]
        tx_responses = events[tx_mask & resp_mask]
        _compute_rtt_numpy(rx_queries, tx_responses, "server processing")
    else:
        rx_queries = [e for e in events if e["direction"] == 0 and e["qr_flag"] == 0]
        tx_responses = [e for e in events if e["direction"] == 1 and e["qr_flag"] == 1]
        _compute_rtt(rx_queries, tx_responses, "server processing")

    # Also compute client-side RTT: TX query -> RX response
    print(f"\n--- Client-side RTT (TX query -> RX response, matched by TXID) ---")
    if HAS_NUMPY:
        tx_queries = events[tx_mask & query_mask]
        rx_responses = events[rx_mask & resp_mask]
        _compute_rtt_numpy(tx_queries, rx_responses, "client RTT")
    else:
        tx_queries = [e for e in events if e["direction"] == 1 and e["qr_flag"] == 0]
        rx_responses = [e for e in events if e["direction"] == 0 and e["qr_flag"] == 1]
        _compute_rtt(tx_queries, rx_responses, "client RTT")


def _compute_rtt(outgoing, incoming, label):
    """Match outgoing to incoming by TXID, compute RTT distribution."""
    # Build map: txid -> list of timestamps (first unmatched)
    out_map = defaultdict(list)
    for e in outgoing:
        out_map[e["dns_txid"]].append(e["timestamp_ns"])

    rtts = []
    matched = 0
    for e in incoming:
        txid = e["dns_txid"]
        if out_map[txid]:
            out_ts = out_map[txid].pop(0)
            rtt_ns = e["timestamp_ns"] - out_ts
            if rtt_ns > 0:
                rtts.append(rtt_ns)
                matched += 1

    print(f"  Outgoing: {len(outgoing):,}  Incoming: {len(incoming):,}  Matched: {matched:,}")

    if not rtts:
        print(f"  No matched {label} pairs found.")
        return

    pcts = percentiles(rtts)
    print(f"  Latency percentiles (us):")
    for k, v in pcts.items():
        print(f"    {k:>6}: {v:>10.1f} us")

    avg_us = sum(rtts) / len(rtts) / 1000.0
    min_us = min(rtts) / 1000.0
    max_us = max(rtts) / 1000.0
    print(f"    mean:   {avg_us:>10.1f} us")
    print(f"    min:    {min_us:>10.1f} us")
    print(f"    max:    {max_us:>10.1f} us")

    return rtts


def _compute_rtt_numpy(outgoing, incoming, label):
    """Match outgoing to incoming by TXID using numpy, compute RTT."""
    if len(outgoing) == 0 or len(incoming) == 0:
        print(f"  No {label} pairs (outgoing={len(outgoing)}, incoming={len(incoming)}).")
        return None

    # Build dict: txid -> deque of timestamps
    out_map = defaultdict(list)
    for i in range(len(outgoing)):
        out_map[int(outgoing["dns_txid"][i])].append(int(outgoing["timestamp_ns"][i]))

    rtts = []
    for i in range(len(incoming)):
        txid = int(incoming["dns_txid"][i])
        if out_map[txid]:
            out_ts = out_map[txid].pop(0)
            rtt_ns = int(incoming["timestamp_ns"][i]) - out_ts
            if rtt_ns > 0:
                rtts.append(rtt_ns)

    matched = len(rtts)
    print(f"  Outgoing: {len(outgoing):,}  Incoming: {len(incoming):,}  Matched: {matched:,}")

    if not rtts:
        print(f"  No matched {label} pairs found.")
        return None

    rtts_arr = np.array(rtts, dtype=np.float64)
    pcts = percentiles(rtts_arr)
    print(f"  Latency percentiles (us):")
    for k, v in pcts.items():
        print(f"    {k:>6}: {v:>10.1f} us")

    print(f"    mean:   {rtts_arr.mean() / 1000.0:>10.1f} us")
    print(f"    min:    {rtts_arr.min() / 1000.0:>10.1f} us")
    print(f"    max:    {rtts_arr.max() / 1000.0:>10.1f} us")

    return rtts_arr


def analyze_cross_node(client_path, server_path, output_dir):
    """Cross-node analysis: compare client and server captures."""
    print(f"\n=== Cross-node analysis ===")
    print(f"  Client: {client_path}")
    print(f"  Server: {server_path}\n")

    if HAS_NUMPY:
        c_hdr, c_events = read_events_numpy(client_path)
        s_hdr, s_events = read_events_numpy(server_path)
    else:
        c_hdr, c_events = read_events(client_path)
        s_hdr, s_events = read_events(server_path)

    if HAS_NUMPY:
        # Client: TX queries, RX responses
        c_tx_q = int(((c_events["direction"] == 1) & (c_events["qr_flag"] == 0)).sum())
        c_rx_r = int(((c_events["direction"] == 0) & (c_events["qr_flag"] == 1)).sum())
        # Server: RX queries, TX responses
        s_rx_q = int(((s_events["direction"] == 0) & (s_events["qr_flag"] == 0)).sum())
        s_tx_r = int(((s_events["direction"] == 1) & (s_events["qr_flag"] == 1)).sum())
    else:
        c_tx_q = sum(1 for e in c_events if e["direction"] == 1 and e["qr_flag"] == 0)
        c_rx_r = sum(1 for e in c_events if e["direction"] == 0 and e["qr_flag"] == 1)
        s_rx_q = sum(1 for e in s_events if e["direction"] == 0 and e["qr_flag"] == 0)
        s_tx_r = sum(1 for e in s_events if e["direction"] == 1 and e["qr_flag"] == 1)

    print(f"Client ({c_hdr['hostname']}):")
    print(f"  Queries sent (TX):      {c_tx_q:,}")
    print(f"  Responses received (RX): {c_rx_r:,}")
    print(f"Server ({s_hdr['hostname']}):")
    print(f"  Queries received (RX):  {s_rx_q:,}")
    print(f"  Responses sent (TX):    {s_tx_r:,}")

    # Drop analysis
    query_drop = c_tx_q - s_rx_q
    resp_drop = s_tx_r - c_rx_r
    print(f"\nDrop analysis:")
    print(f"  Query drops  (client TX - server RX): {query_drop:,} "
          f"({query_drop / c_tx_q * 100:.2f}%)" if c_tx_q > 0 else "")
    print(f"  Response drops (server TX - client RX): {resp_drop:,} "
          f"({resp_drop / s_tx_r * 100:.2f}%)" if s_tx_r > 0 else "")

    # Per-node latency analysis
    print(f"\n--- Client-side RTT ---")
    analyze_single(client_path)

    print(f"\n--- Server-side processing time ---")
    analyze_single(server_path)

    # Export CSV summary if output_dir specified
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        summary_path = os.path.join(output_dir, "summary.csv")
        with open(summary_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["metric", "value"])
            w.writerow(["client_hostname", c_hdr["hostname"]])
            w.writerow(["server_hostname", s_hdr["hostname"]])
            w.writerow(["client_queries_sent", c_tx_q])
            w.writerow(["client_responses_received", c_rx_r])
            w.writerow(["server_queries_received", s_rx_q])
            w.writerow(["server_responses_sent", s_tx_r])
            w.writerow(["query_drops", query_drop])
            w.writerow(["response_drops", resp_drop])

        print(f"\nSummary written to {summary_path}")

        # Export per-event CSV for the client
        _export_events_csv(client_path, os.path.join(output_dir, "client_events.csv"))
        _export_events_csv(server_path, os.path.join(output_dir, "server_events.csv"))


def _export_events_csv(dtrace_path, csv_path):
    """Export .dtrace events to CSV."""
    _, events = read_events(dtrace_path)
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "timestamp_ns", "src_ip", "dst_ip", "src_port", "dst_port",
            "dns_txid", "pkt_size", "direction", "qr_flag",
        ])
        for e in events:
            w.writerow([
                e["timestamp_ns"],
                ip_to_str(e["src_ip"]),
                ip_to_str(e["dst_ip"]),
                e["src_port"],
                e["dst_port"],
                e["dns_txid"],
                e["pkt_size"],
                "RX" if e["direction"] == 0 else "TX",
                "Q" if e["qr_flag"] == 0 else "R",
            ])
    print(f"Events CSV written to {csv_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Analyze .dtrace DNS packet capture files"
    )
    parser.add_argument(
        "--input", help="Single .dtrace file to analyze"
    )
    parser.add_argument(
        "--client", help="Client (load generator) .dtrace file"
    )
    parser.add_argument(
        "--server", help="Server (DNS) .dtrace file"
    )
    parser.add_argument(
        "--output-dir", help="Directory for CSV output files"
    )
    args = parser.parse_args()

    if not HAS_NUMPY:
        print(
            "NOTE: numpy not found. Analysis will work but may be slow for large files. "
            "Install with: pip install numpy",
            file=sys.stderr,
        )

    if args.input:
        analyze_single(args.input)
    elif args.client and args.server:
        analyze_cross_node(args.client, args.server, args.output_dir)
    elif args.client or args.server:
        # Single file provided via --client or --server
        path = args.client or args.server
        analyze_single(path)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
