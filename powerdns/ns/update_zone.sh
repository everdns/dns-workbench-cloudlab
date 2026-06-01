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

if command -v pdns_server &>/dev/null; then
    PDNS_DIR="/etc/powerdns"
    PDNS_CONF="$PDNS_DIR/named.conf"
    echo "[PowerDNS] Detected"

    sudo cp "$ZONE_FILE" "$PDNS_DIR/$ZONE_FILENAME"
    for part in "${ZONE_FILE}.part"*; do
        [ -f "$part" ] && sudo cp "$part" "$PDNS_DIR/$(basename "$part")"
    done

    if ! sudo grep -q "zone \"$ZONE_NAME\"" "$PDNS_CONF" 2>/dev/null; then
        echo "  Adding zone entry to $PDNS_CONF"
        printf '\nzone "%s" {\n    type master;\n    file "%s/%s";\n};\n' \
            "$ZONE_NAME" "$PDNS_DIR" "$ZONE_FILENAME" | sudo tee -a "$PDNS_CONF" >/dev/null
    else
        echo "  Zone entry already exists in $PDNS_CONF"
    fi
    echo "[PowerDNS] Done"
else
    echo "[PowerDNS] Not installed, skipping"
fi
