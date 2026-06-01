#!/bin/sh
# Stop NSD (authoritative name server)
echo "Stopping nsd..."
sudo pkill nsd 2>/dev/null
