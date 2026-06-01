#!/bin/sh
# Stop and uninstall Knot DNS (authoritative name server)
/local/repository/knot/ns/stop.sh

if dpkg -l knot 2>/dev/null | grep -q "^ii"; then
    echo "Removing knot..."
    sudo apt-get remove --purge knot -y
else
    echo "knot is not installed, skipping."
fi
