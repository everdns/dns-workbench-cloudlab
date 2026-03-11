#!/bin/bash

# Uninstall dns software that is installed

# Stop services before uninstalling
for svc in named pdns pdns-recursor knot-resolver knot; do
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
        echo "Stopping $svc..."
        sudo systemctl stop "$svc"
    fi
done

for pkg in bind9 bind9-utils bind9-dnsutils pdns-server pdns-recursor knot-resolver6 knot; do
    if dpkg -l "$pkg" 2>/dev/null | grep -q "^ii"; then
        echo "Removing $pkg..."
        sudo apt-get remove --purge "$pkg" -y
    else
        echo "$pkg is not installed, skipping."
    fi
done

echo "Done."