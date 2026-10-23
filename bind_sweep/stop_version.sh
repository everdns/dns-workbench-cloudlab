#!/bin/sh
# Stop any running named (sweep-built or packaged). Argument, if any, is ignored.
sudo systemctl stop named 2>/dev/null
sudo pkill -x named 2>/dev/null
exit 0
