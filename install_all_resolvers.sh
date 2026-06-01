#!/bin/sh
# Install all resolver software
# Usage: install_all_resolvers.sh <multiple_iface_flag>

IFACE_FLAG=${1:-false}

/local/repository/resolver_software/bind/install.sh "$IFACE_FLAG"
/local/repository/resolver_software/powerdns/install.sh "$IFACE_FLAG"
/local/repository/resolver_software/knot/install.sh "$IFACE_FLAG"
/local/repository/resolver_software/unbound/install.sh "$IFACE_FLAG"
/local/repository/dns_responder/install.sh