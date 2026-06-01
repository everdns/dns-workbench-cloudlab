#!/bin/sh
# Stop and uninstall Knot Resolver
/local/repository/resolver_software/knot/stop.sh

if dpkg -l knot-resolver6 2>/dev/null | grep -q "^ii"; then
    echo "Removing knot-resolver6..."
    sudo apt-get remove --purge knot-resolver6 -y
else
    echo "knot-resolver6 is not installed, skipping."
fi
