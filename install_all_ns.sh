#!/bin/sh
# Install all name server software
# Usage: install_all_ns.sh <multiple_iface_flag>
#
# Discovers every software under ns_software/ that has an install.sh and runs it.

IFACE_FLAG=${1:-false}

for dir in "/local/repository/ns_software"/*/; do
    [ -x "${dir}install.sh" ] || continue
    "${dir}install.sh" "$IFACE_FLAG"
done
