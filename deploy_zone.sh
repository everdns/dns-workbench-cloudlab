#!/bin/bash
# Generate a /16 zone (65536 records/IPs) and deploy it:
#   dnsperf input -> testers (10.10.1.2, 10.10.1.3):/local/repository/dnsperf_input
#   zone file     -> name server (10.10.1.1), then run update_zone_files.sh on it
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
OUT_DIR="$SCRIPT_DIR/zone_generation/output"
NS_HOST=10.10.1.1
TESTERS=(10.10.1.2 10.10.1.3)
REMOTE_REPO=/local/repository
SSH_OPTS=(-o StrictHostKeyChecking=accept-new)

python3 "$SCRIPT_DIR/zone_generation/zone_generator.py" \
    --config "$SCRIPT_DIR/zone_generation/config.json" \
    --base-subnet 10.0.0.0 --num-records 65536 --num-ips 65536 \
    --max-records-per-file 65536 --out-dir "$OUT_DIR"

ZONE_FILE="$OUT_DIR/db.workbench.lan"
DNSPERF_FILE="$OUT_DIR/dnsperf_input_10-0-0-0_65536"

for host in "${TESTERS[@]}"; do
    scp "${SSH_OPTS[@]}" "$DNSPERF_FILE" "$host:$REMOTE_REPO/dnsperf_input"
done

scp "${SSH_OPTS[@]}" "$ZONE_FILE" "$NS_HOST:/tmp/db.workbench.lan"
ssh "${SSH_OPTS[@]}" "$NS_HOST" "cd $REMOTE_REPO && sudo ./update_zone_files.sh /tmp/db.workbench.lan"
