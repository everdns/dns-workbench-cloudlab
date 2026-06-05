#!/bin/sh
# Stop Unbound (recursive resolver)
if pgrep -f 'unbound -c /usr/local/etc/unbound/unbound.conf' >/dev/null 2>&1; then
    echo "Stopping unbound..."
    sudo pkill unbound 2>/dev/null
fi
