#!/bin/sh
# Install all resolver software
# Usage: install_all_resolvers.sh <multiple_iface_flag>

IFACE_FLAG=${1:-false}

/local/repository/bind/resolver/install.sh "$IFACE_FLAG"
/local/repository/powerdns/resolver/install.sh "$IFACE_FLAG"
/local/repository/knot/resolver/install.sh "$IFACE_FLAG"
/local/repository/unbound/resolver/install.sh "$IFACE_FLAG"
