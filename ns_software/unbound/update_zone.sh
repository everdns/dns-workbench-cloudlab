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

if command -v unbound &>/dev/null; then
    UNBOUND_DIR="/usr/local/etc/unbound"
    UNBOUND_CONF="$UNBOUND_DIR/unbound.conf"
    echo "[Unbound] Detected"

    sudo cp "$ZONE_FILE" "$UNBOUND_DIR/$ZONE_FILENAME"
    for part in "${ZONE_FILE}.part"*; do
        [ -f "$part" ] && sudo cp "$part" "$UNBOUND_DIR/$(basename "$part")"
    done

    if ! sudo grep -q "name: \"$ZONE_NAME\"" "$UNBOUND_CONF" 2>/dev/null; then
        echo "  Adding zone entry to $UNBOUND_CONF"
        printf '\nauth-zone:\n\tname: "%s"\n\tzonefile: "%s/%s"\n' \
            "$ZONE_NAME" "$UNBOUND_DIR" "$ZONE_FILENAME" | sudo tee -a "$UNBOUND_CONF" >/dev/null
    else
        echo "  Zone entry already exists in $UNBOUND_CONF"
    fi
    echo "[Unbound] Done"
else
    echo "[Unbound] Not installed, skipping"
fi
