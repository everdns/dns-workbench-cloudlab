#!/bin/sh
# Stop and uninstall PowerDNS Authoritative Server
/local/repository/powerdns/ns/stop.sh

if dpkg -l pdns-server 2>/dev/null | grep -q "^ii"; then
    echo "Removing pdns-server..."
    sudo apt-get remove --purge pdns-server -y
else
    echo "pdns-server is not installed, skipping."
fi
