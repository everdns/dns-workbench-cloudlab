#!/bin/sh
# Install all name server software
# Usage: install_all_ns.sh <multiple_iface_flag>
#
# Discovers every software under ns_software/ that has an install.sh and runs it.

IFACE_FLAG=${1:-false}

REPO_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

for dir in "$REPO_DIR/ns_software"/*/; do
    [ -x "${dir}install.sh" ] || continue
    "${dir}install.sh" "$IFACE_FLAG"
done

# dns_responder lives outside ns_software/ but is needed by the NS setup.
"$REPO_DIR/dns_responder/install.sh"
