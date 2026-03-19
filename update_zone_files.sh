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

echo "Zone file: $ZONE_FILE"
echo "Zone name: $ZONE_NAME"
echo ""

# --- BIND ---
if command -v named &>/dev/null; then
    BIND_DIR="/etc/bind"
    BIND_CONF="$BIND_DIR/named.conf.local"
    echo "[BIND] Detected"

    sudo cp "$ZONE_FILE" "$BIND_DIR/$ZONE_FILENAME"
    # Also copy part files if they exist (multi-file zones)
    for part in "${ZONE_FILE}.part"*; do
        [ -f "$part" ] && sudo cp "$part" "$BIND_DIR/$(basename "$part")"
    done

    if ! grep -q "zone \"$ZONE_NAME\"" "$BIND_CONF" 2>/dev/null; then
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

# --- Knot DNS ---
if command -v knotd &>/dev/null; then
    KNOT_DIR="/etc/knot"
    KNOT_CONF="$KNOT_DIR/knot.conf"
    echo "[Knot DNS] Detected"

    sudo cp "$ZONE_FILE" "$KNOT_DIR/$ZONE_FILENAME"
    for part in "${ZONE_FILE}.part"*; do
        [ -f "$part" ] && sudo cp "$part" "$KNOT_DIR/$(basename "$part")"
    done

    if ! grep -q "domain: $ZONE_NAME" "$KNOT_CONF" 2>/dev/null; then
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

# --- PowerDNS ---
if command -v pdns_server &>/dev/null; then
    PDNS_DIR="/etc/powerdns"
    PDNS_CONF="$PDNS_DIR/named.conf"
    echo "[PowerDNS] Detected"

    sudo cp "$ZONE_FILE" "$PDNS_DIR/$ZONE_FILENAME"
    for part in "${ZONE_FILE}.part"*; do
        [ -f "$part" ] && sudo cp "$part" "$PDNS_DIR/$(basename "$part")"
    done

    if ! grep -q "zone \"$ZONE_NAME\"" "$PDNS_CONF" 2>/dev/null; then
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

# --- NSD ---
if command -v nsd &>/dev/null; then
    NSD_DIR="/etc/nsd"
    NSD_CONF="$NSD_DIR/nsd.conf"
    echo "[NSD] Detected"

    sudo cp "$ZONE_FILE" "$NSD_DIR/$ZONE_FILENAME"
    for part in "${ZONE_FILE}.part"*; do
        [ -f "$part" ] && sudo cp "$part" "$NSD_DIR/$(basename "$part")"
    done

    if ! grep -q "name: \"$ZONE_NAME\"" "$NSD_CONF" 2>/dev/null; then
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

# --- Unbound ---
if command -v unbound &>/dev/null; then
    UNBOUND_DIR="/usr/local/etc/unbound"
    UNBOUND_CONF="$UNBOUND_DIR/unbound.conf"
    echo "[Unbound] Detected"

    sudo cp "$ZONE_FILE" "$UNBOUND_DIR/$ZONE_FILENAME"
    for part in "${ZONE_FILE}.part"*; do
        [ -f "$part" ] && sudo cp "$part" "$UNBOUND_DIR/$(basename "$part")"
    done

    if ! grep -q "name: \"$ZONE_NAME\"" "$UNBOUND_CONF" 2>/dev/null; then
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

echo ""
echo "Zone file update complete."
