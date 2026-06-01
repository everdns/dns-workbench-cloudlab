#!/bin/sh
# Stop and uninstall PowerDNS Recursor
/local/repository/resolver_software/powerdns/stop.sh

if dpkg -l pdns-recursor 2>/dev/null | grep -q "^ii"; then
    echo "Removing pdns-recursor..."
    sudo apt-get remove --purge pdns-recursor -y
else
    echo "pdns-recursor is not installed, skipping."
fi
