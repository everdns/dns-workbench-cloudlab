#!/bin/sh
# Stop Unbound (recursive resolver)
echo "Stopping unbound..."
sudo pkill unbound 2>/dev/null
