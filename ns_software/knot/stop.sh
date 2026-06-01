#!/bin/sh
# Stop Knot DNS (authoritative name server)
if systemctl is-active --quiet knot 2>/dev/null; then
    echo "Stopping knot..."
    sudo systemctl stop knot
fi
