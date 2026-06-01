#!/bin/sh
# Stop PowerDNS Recursor
if systemctl is-active --quiet pdns-recursor 2>/dev/null; then
    echo "Stopping pdns-recursor..."
    sudo systemctl stop pdns-recursor
fi
