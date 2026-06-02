#!/bin/bash

usage() {
    cat <<'EOF'
Usage: ./test_setup.sh [SERVER_IP] [SERVER_TYPE]

Smoke-tests each DNS server implementation by starting it, issuing a
query against it, and then stopping it again.

Arguments:
  SERVER_IP     IP address to send dig queries to (default: 10.10.1.2)
  SERVER_TYPE   Which group of servers to test (default: NS)
                  NS  -> every software under ns_software/ (as ns_<sw>)
                  any other value -> every software under resolver_software/
                                     (as resolver_<sw>)

Options:
  -h, --help    Show this help message and exit

Examples:
  ./test_setup.sh                       # test NS servers at 10.10.1.2
  ./test_setup.sh 10.10.1.3             # test NS servers at 10.10.1.3
  ./test_setup.sh 10.10.1.3 resolver    # test resolver servers at 10.10.1.3
EOF
}

if [[ "$1" == "-h" || "$1" == "--help" ]]; then
    usage
    exit 0
fi

SERVER_IP=${1:-10.10.1.2}
SERVER_TYPE=${2:-NS}

REPO_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if [[ "$SERVER_TYPE" == "NS" ]]; then
    role=ns
else
    role=resolver
fi

# Discover every software in the chosen tree that can be started.
servers=()
for dir in "$REPO_DIR/${role}_software"/*/; do
    [ -x "${dir}start.sh" ] || continue
    servers+=("${role}_$(basename "$dir")")
done

ssh "$SERVER_IP" "bash ./stop_dns_service.sh"
for server in "${servers[@]}"; do
    echo "=== Testing $server ==="
    ssh "$SERVER_IP" "bash ./start_dns_service.sh '$server'"
    dig @"$SERVER_IP" ns1.workbench.lan
    ssh "$SERVER_IP" "bash ./stop_dns_service.sh"
done
