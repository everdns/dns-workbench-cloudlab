#!/bin/bash
set -euo pipefail

if [ $# -lt 1 ]; then
    echo "Usage: $0 <zone-file-path>"
    echo "Example: $0 output/db.workbench.lan"
    exit 1
fi

ZONE_FILE="$1"

if [ ! -f "$ZONE_FILE" ]; then
    echo "Error: Zone file '$ZONE_FILE' not found"
    exit 1
fi

ZONE_FILENAME=$(basename "$ZONE_FILE")

# Extract zone name from filename (e.g., db.workbench.lan -> workbench.lan)
ZONE_NAME="${ZONE_FILENAME#db.}"
if [ "$ZONE_NAME" = "$ZONE_FILENAME" ]; then
    echo "Error: Zone file must be named db.<zone-name> (got '$ZONE_FILENAME')"
    exit 1
fi

if command -v knotd &>/dev/null; then
    KNOT_DIR="/etc/knot"
    KNOT_CONF="$KNOT_DIR/knot.conf"
    echo "[Knot DNS] Detected"

    sudo cp "$ZONE_FILE" "$KNOT_DIR/$ZONE_FILENAME"
    for part in "${ZONE_FILE}.part"*; do
        [ -f "$part" ] && sudo cp "$part" "$KNOT_DIR/$(basename "$part")"
    done

    if ! sudo grep -q "domain: $ZONE_NAME" "$KNOT_CONF" 2>/dev/null; then
        echo "  Adding zone entry to $KNOT_CONF"
        printf '  - domain: %s\n    file: "%s/%s"\n' \
            "$ZONE_NAME" "$KNOT_DIR" "$ZONE_FILENAME" | sudo tee -a "$KNOT_CONF" >/dev/null
    else
        echo "  Zone entry already exists in $KNOT_CONF"
    fi
    echo "[Knot DNS] Done"
else
    echo "[Knot DNS] Not installed, skipping"
fi
