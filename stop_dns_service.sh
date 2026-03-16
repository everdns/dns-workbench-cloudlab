#!/bin/bash

# Uninstall dns software that is installed

# Stop services before uninstalling
for svc in named pdns pdns-recursor knot-resolver knot; do
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
        echo "Stopping $svc..."
        sudo systemctl stop "$svc"
    fi
done
sudo pkill nsd
sudo pkill unbound