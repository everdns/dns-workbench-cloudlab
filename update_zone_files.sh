#!/bin/bash
set -euo pipefail

if [ $# -lt 1 ]; then
    echo "Usage: $0 <zone-file-path>"
    echo "Example: $0 output/db.workbench.lan"
    exit 1
fi

if [ ! -f "$1" ]; then
    echo "Error: Zone file '$1' not found"
    exit 1
fi

# Resolve to an absolute path so the per-software scripts can find it
# regardless of their working directory.
ZONE_FILE="$(realpath "$1")"

echo "Zone file: $ZONE_FILE"
echo ""

# Delegate to each authoritative software's update_zone.sh. Each script
# detects whether its software is installed and skips if not.
for sw in bind knot powerdns nsd unbound; do
    /local/repository/$sw/ns/update_zone.sh "$ZONE_FILE"
done

echo ""
echo "Zone file update complete."
