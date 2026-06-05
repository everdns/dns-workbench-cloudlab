#!/bin/sh
# Install all resolver software
# Usage: install_all_resolvers.sh <multiple_iface_flag>
#
# Discovers every software under resolver_software/ that has an install.sh and runs it.

IFACE_FLAG=${1:-false}

for dir in "/local/repository/resolver_software"/*/; do
    [ -x "${dir}install.sh" ] || continue
    "${dir}install.sh" "$IFACE_FLAG"
done