#!/bin/sh
# Stop Unbound (authoritative name server)
echo "Stopping unbound..."
sudo pkill unbound 2>/dev/null
