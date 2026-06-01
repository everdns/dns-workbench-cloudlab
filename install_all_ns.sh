#!/bin/sh
# Install all name server software
# Usage: install_all_ns.sh <multiple_iface_flag>

IFACE_FLAG=${1:-false}

/local/repository/ns_software/bind/install.sh "$IFACE_FLAG"
/local/repository/ns_software/powerdns/install.sh "$IFACE_FLAG"
/local/repository/ns_software/knot/install.sh "$IFACE_FLAG"
/local/repository/ns_software/nsd/install.sh "$IFACE_FLAG"
/local/repository/ns_software/unbound/install.sh "$IFACE_FLAG"
/local/repository/dns_responder/install.sh