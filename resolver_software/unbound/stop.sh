#!/bin/sh
# Stop Unbound (recursive resolver)
if pgrep -x unbound >/dev/null 2>&1; then
    echo "Stopping unbound..."
    sudo pkill unbound 2>/dev/null
fi
