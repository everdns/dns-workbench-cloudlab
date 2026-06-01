#!/bin/sh
# Stop PowerDNS Authoritative Server
if systemctl is-active --quiet pdns 2>/dev/null; then
    echo "Stopping pdns..."
    sudo systemctl stop pdns
fi
