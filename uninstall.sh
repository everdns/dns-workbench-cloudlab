#!/bin/bash

# Uninstall dns software that is installed

for pkg in bind9 bind9-utils bind9-dnsutils pdns-server pdns-recursor knot-resolver6; do
    if dpkg -l "$pkg" 2>/dev/null | grep -q "^ii"; then
        echo "Removing $pkg..."
        sudo apt-get remove --purge "$pkg" -y
    else
        echo "$pkg is not installed, skipping."
    fi
done

echo "Done."