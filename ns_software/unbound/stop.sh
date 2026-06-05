#!/bin/sh
# Stop Unbound (authoritative name server)
if pgrep unbound >/dev/null 2>&1; then
    echo "Stopping unbound..."
    sudo pkill unbound 2>/dev/null
fi
