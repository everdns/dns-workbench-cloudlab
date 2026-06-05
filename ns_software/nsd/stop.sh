#!/bin/sh
# Stop NSD (authoritative name server)
if pgrep -f 'nsd -c /etc/nsd/nsd.conf' >/dev/null 2>&1; then
    echo "Stopping nsd..."
    sudo pkill nsd 2>/dev/null
fi
