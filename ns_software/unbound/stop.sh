#!/bin/sh
# Stop Unbound (authoritative name server)
if pgrep -f 'unbound -c /usr/local/etc/unbound/unbound.conf' >/dev/null 2>&1; then
    echo "Stopping unbound..."
    sudo pkill unbound 2>/dev/null
fi
