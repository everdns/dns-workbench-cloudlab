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

if command -v nsd &>/dev/null; then
    NSD_DIR="/etc/nsd"
    NSD_CONF="$NSD_DIR/nsd.conf"
    echo "[NSD] Detected"

    sudo cp "$ZONE_FILE" "$NSD_DIR/$ZONE_FILENAME"
    for part in "${ZONE_FILE}.part"*; do
        [ -f "$part" ] && sudo cp "$part" "$NSD_DIR/$(basename "$part")"
    done

    if ! sudo grep -q "name: \"$ZONE_NAME\"" "$NSD_CONF" 2>/dev/null; then
        echo "  Adding zone entry to $NSD_CONF"
        printf '\nzone:\n\tname: "%s"\n\tzonefile: "%s"\n' \
            "$ZONE_NAME" "$ZONE_FILENAME" | sudo tee -a "$NSD_CONF" >/dev/null
    else
        echo "  Zone entry already exists in $NSD_CONF"
    fi
    echo "[NSD] Done"
else
    echo "[NSD] Not installed, skipping"
fi
