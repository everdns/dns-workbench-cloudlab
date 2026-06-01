#!/bin/sh
# Stop and uninstall BIND (authoritative name server)
/local/repository/ns_software/bind/stop.sh

for pkg in bind9 bind9-utils bind9-dnsutils; do
    if dpkg -l "$pkg" 2>/dev/null | grep -q "^ii"; then
        echo "Removing $pkg..."
        sudo apt-get remove --purge "$pkg" -y
    else
        echo "$pkg is not installed, skipping."
    fi
done
