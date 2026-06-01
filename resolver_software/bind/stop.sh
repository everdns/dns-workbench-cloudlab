#!/bin/sh
# Stop BIND (recursive resolver)
if systemctl is-active --quiet named 2>/dev/null; then
    echo "Stopping named..."
    sudo systemctl stop named
fi
