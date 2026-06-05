#!/bin/sh
# Install all resolver software
# Usage: install_all_resolvers.sh <multiple_iface_flag>
#
# Discovers every software under resolver_software/ that has an install.sh and runs it.

IFACE_FLAG=${1:-false}

FAILED=""

for dir in "/local/repository/resolver_software"/*/; do
    [ -x "${dir}install.sh" ] || continue
    if ! "${dir}install.sh" "$IFACE_FLAG"; then
        FAILED="$FAILED $(basename "$dir")"
    fi
done

if [ -n "$FAILED" ]; then
    echo "The following resolver software failed to install:" >&2
    for name in $FAILED; do
        echo "  - $name" >&2
    done
    exit 1
fi

echo "All resolver software installed successfully."