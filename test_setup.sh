#!/bin/bash

usage() {
    cat <<'EOF'
Usage: ./test_setup.sh [SERVER_IP]

Smoke-tests each DNS server implementation by starting it, issuing a
query against it, and then stopping it again.

Arguments:
  SERVER_IP     IP address to send dig queries to (default: 10.10.1.2)

Options:
  -h, --help    Show this help message and exit

Examples:
  ./test_setup.sh                       # test NS servers at 10.10.1.1
  ./test_setup.sh 10.10.1.3             # test NS servers at 10.10.1.3
EOF
}

if [[ "$1" == "-h" || "$1" == "--help" ]]; then
    usage
    exit 0
fi

SERVER_IP=${1:-10.10.1.1}

REPO_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

# Discover every software under ns_software/ that can be started.
servers=()
for dir in "$REPO_DIR/ns_software"/*/; do
    [ -x "${dir}start.sh" ] || continue
    servers+=("ns_$(basename "$dir")")
done

ssh "$SERVER_IP" "bash /local/repository/stop_dns_service.sh"
for server in "${servers[@]}"; do
    echo "=== Testing $server ==="
    ssh "$SERVER_IP" "bash /local/repository/start_dns_service.sh '$server'"
    dig @"$SERVER_IP" ns1.workbench.lan
    ssh "$SERVER_IP" "bash /local/repository/stop_dns_service.sh"
done
