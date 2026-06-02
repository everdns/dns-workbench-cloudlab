#!/bin/bash
set -euo pipefail

usage() {
    echo "Usage: $0 <zone-file-path> [software ...]"
    echo "Example: $0 output/db.workbench.lan"
    echo "Example: $0 output/db.workbench.lan bind knot"
    echo ""
    echo "Updates the zone file for authoritative (ns_software) software."
    echo "With no software names, updates every ns_software that has update_zone.sh."
}

if [ $# -lt 1 ]; then
    usage
    exit 1
fi

if [ ! -f "$1" ]; then
    echo "Error: Zone file '$1' not found"
    exit 1
fi

# Resolve to an absolute path so the per-software scripts can find it
# regardless of their working directory.
ZONE_FILE="$(realpath "$1")"
shift

REPO_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

# Build the list of authoritative software to update. With explicit names use
# those; otherwise discover every ns_software directory that has update_zone.sh.
if [ $# -gt 0 ]; then
    softwares=("$@")
else
    softwares=()
    for dir in "$REPO_DIR/ns_software"/*/; do
        [ -x "${dir}update_zone.sh" ] || continue
        softwares+=("$(basename "$dir")")
    done
fi

echo "Zone file: $ZONE_FILE"
echo ""

for sw in "${softwares[@]}"; do
    script="$REPO_DIR/ns_software/$sw/update_zone.sh"
    if [ ! -x "$script" ]; then
        echo "Skipping '$sw': no update_zone.sh under ns_software/$sw"
        continue
    fi
    "$script" "$ZONE_FILE"
done

echo ""
echo "Zone file update complete."
