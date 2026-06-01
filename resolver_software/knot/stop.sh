#!/bin/sh
# Stop Knot Resolver
if systemctl is-active --quiet knot-resolver 2>/dev/null; then
    echo "Stopping knot-resolver..."
    sudo systemctl stop knot-resolver
fi
