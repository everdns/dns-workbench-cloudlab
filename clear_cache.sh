#!/bin/sh
if [ -z "$1" ]; then
    echo "Usage: $0 <software>"
    echo "Options: bind-resolver, powerdns-resolver, knot-resolver, unbound-resolver"
    exit 1
fi

case "$1" in
    bind-resolver)
        /local/repository/resolver_software/bind/clear_cache.sh
        ;;
    powerdns-resolver)
        /local/repository/resolver_software/powerdns/clear_cache.sh
        ;;
    knot-resolver)
        /local/repository/resolver_software/knot/clear_cache.sh
        ;;
    unbound-resolver)
        /local/repository/resolver_software/unbound/clear_cache.sh
        ;;
    *)
        echo "Unknown software: $1"
        echo "Options: bind-resolver, powerdns-resolver, knot-resolver, unbound-resolver"
        exit 1
        ;;
esac