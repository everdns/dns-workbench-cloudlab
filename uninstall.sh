#!/bin/bash

# Uninstall bind9, pdns-server, and pdns-recursor if installed

for pkg in bind9 bind9-utils bind9-dnsutils pdns-server pdns-recursor; do
    if dpkg -l "$pkg" 2>/dev/null | grep -q "^ii"; then
        echo "Removing $pkg..."
        sudo apt-get remove --purge "$pkg" -y
    else
        echo "$pkg is not installed, skipping."
    fi
done

echo "Done."