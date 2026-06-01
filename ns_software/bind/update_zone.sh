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

if command -v named &>/dev/null; then
    BIND_DIR="/etc/bind"
    BIND_CONF="$BIND_DIR/named.conf.local"
    echo "[BIND] Detected"

    sudo cp "$ZONE_FILE" "$BIND_DIR/$ZONE_FILENAME"
    # Also copy part files if they exist (multi-file zones)
    for part in "${ZONE_FILE}.part"*; do
        [ -f "$part" ] && sudo cp "$part" "$BIND_DIR/$(basename "$part")"
    done

    if ! sudo grep -q "zone \"$ZONE_NAME\"" "$BIND_CONF" 2>/dev/null; then
        echo "  Adding zone entry to $BIND_CONF"
        printf '\nzone "%s" IN {\n    type master;\n    file "%s/%s";\n};\n' \
            "$ZONE_NAME" "$BIND_DIR" "$ZONE_FILENAME" | sudo tee -a "$BIND_CONF" >/dev/null
    else
        echo "  Zone entry already exists in $BIND_CONF"
    fi
    echo "[BIND] Done"
else
    echo "[BIND] Not installed, skipping"
fi
