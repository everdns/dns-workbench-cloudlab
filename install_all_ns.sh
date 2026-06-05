#!/bin/sh
# Install all name server software
# Usage: install_all_ns.sh <multiple_iface_flag>
#
# Discovers every software under ns_software/ that has an install.sh and runs it.

IFACE_FLAG=${1:-false}

FAILED=""

for dir in "/local/repository/ns_software"/*/; do
    [ -x "${dir}install.sh" ] || continue
    if ! "${dir}install.sh" "$IFACE_FLAG"; then
        FAILED="$FAILED $(basename "$dir")"
    fi
done

if [ -n "$FAILED" ]; then
    echo "The following name server software failed to install:" >&2
    for name in $FAILED; do
        echo "  - $name" >&2
    done
    exit 1
fi

echo "All name server software installed successfully."
