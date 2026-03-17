#!/bin/sh
# Install all name server software
# Usage: install_all_ns.sh <multiple_iface_flag>

IFACE_FLAG=${1:-false}

/local/repository/bind/ns/install.sh "$IFACE_FLAG"
/local/repository/powerdns/ns/install.sh "$IFACE_FLAG"
/local/repository/knot/ns/install.sh "$IFACE_FLAG"
/local/repository/nsd/ns/install.sh "$IFACE_FLAG"
/local/repository/unbound/ns/install.sh "$IFACE_FLAG"
