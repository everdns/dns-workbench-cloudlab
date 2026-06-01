#!/usr/bin/env python3
"""Start each DNS service in turn and capture the dig MSG SIZE for a set of queries.

For every service listed in dns_services.services in the config, this script:
  1. Stops any currently running DNS service.
  2. Starts the target service and waits for it to respond.
  3. Runs `dig @<server> <qname> <qtype> +stats` for each configured query.
  4. Parses the ";; MSG SIZE  rcvd: N" line and prints it.

Usage:
    python examples/msg_size_per_service.py
    python examples/msg_size_per_service.py --config examples/config2.yaml
    python examples/msg_size_per_service.py --query 010-000-000-001.workbench.lan. A
"""
import argparse
import logging
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from benchmark.config import load_config
from benchmark.dns_servers import (
    start_dns_service,
    stop_dns_service,
    wait_for_dns_ready,
)
from benchmark.remote import ssh_run


MSG_SIZE_RE = re.compile(r";;\s*MSG SIZE\s+rcvd:\s*(\d+)")

DEFAULT_QUERIES = [
    ("010-000-000-001.workbench.lan.", "A"),
    ("010-000-000-002.workbench.lan.", "AAAA"),
    ("010-000-000-003.workbench.lan.", "HTTPS"),
    ("010-000-000-005.workbench.lan.", "NS"),
    ("010-000-000-006.workbench.lan.", "TXT"),
]


def run_dig(client, server, qname, qtype, timeout=10):
    cmd = f"dig @{server} {qname} {qtype} +stats"
    return ssh_run(client, cmd, timeout=timeout)


def parse_msg_size(dig_output):
    match = MSG_SIZE_RE.search(dig_output)
    return int(match.group(1)) if match else None


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--config", default=None, help="Path to config YAML file")
    parser.add_argument("--query", action="append", nargs=2, metavar=("QNAME", "QTYPE"),
                        help="Override the default query set; repeat for multiple queries")
    parser.add_argument("--ready-timeout", type=int, default=30,
                        help="Seconds to wait for each service to come up (default: 30)")
    parser.add_argument("--settle", type=float, default=1.0,
                        help="Seconds to sleep after readiness before querying (default: 1.0)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Print full dig output for each service")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    config = load_config(args.config)
    server = config["hosts"]["server"]
    client = config["hosts"]["client"]
    services = config["dns_services"]["services"]

    queries = [tuple(q) for q in args.query] if args.query else list(DEFAULT_QUERIES)

    results = {}
    for service in services:
        print(f"\n=== {service} ===")
        per_service = []
        try:
            stop_dns_service(config)
            start_dns_service(config, service)
            wait_for_dns_ready(config, timeout=args.ready_timeout)
            if args.settle > 0:
                time.sleep(args.settle)

            for qname, qtype in queries:
                dig_result = run_dig(client, server, qname, qtype)
                if args.verbose:
                    print(dig_result.stdout)

                msg_size = parse_msg_size(dig_result.stdout)
                label = f"{qname} {qtype}"
                if msg_size is None:
                    print(f"  {label:48s} MSG SIZE: <not found> (rc={dig_result.returncode})")
                    if dig_result.stderr:
                        print(f"    stderr: {dig_result.stderr.strip()}")
                else:
                    print(f"  {label:48s} MSG SIZE rcvd: {msg_size} bytes")
                per_service.append((qname, qtype, msg_size))
        except Exception as exc:
            print(f"  ERROR: {exc}")
        results[service] = per_service

    stop_dns_service(config)

    print("\n=== Summary ===")
    header = f"  {'service':15s} " + " ".join(f"{q[1]:>6s}" for q in queries)
    print(header)
    for service in services:
        sizes = {(qn, qt): sz for qn, qt, sz in results.get(service, [])}
        cells = []
        for qn, qt in queries:
            sz = sizes.get((qn, qt))
            cells.append(f"{sz if sz is not None else 'n/a':>6}")
        print(f"  {service:15s} " + " ".join(cells))


if __name__ == "__main__":
    main()
