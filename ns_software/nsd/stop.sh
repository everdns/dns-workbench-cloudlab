#!/bin/sh
# Stop NSD (authoritative name server)
if pgrep nsd >/dev/null 2>&1; then
    echo "Stopping nsd..."
    sudo pkill nsd 2>/dev/null
fi
