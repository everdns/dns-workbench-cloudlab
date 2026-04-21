#!/bin/sh
if [ -z "$1" ]; then
    echo "Usage: $0 <software>"
    echo "Options: bind-resolver, powerdns-resolver, knot-resolver, unbound-resolver"
    exit 1
fi

case "$1" in
    bind-resolver)
        /local/repository/bind/resolver/clear_cache.sh
        ;;
    powerdns-resolver)
        /local/repository/powerdns/resolver/clear_cache.sh
        ;;
    knot-resolver)
        /local/repository/knot/resolver/clear_cache.sh
        ;;
    unbound-resolver)
        /local/repository/unbound/resolver/clear_cache.sh
        ;;
    *)
        echo "Unknown software: $1"
        echo "Options: bind-resolver, powerdns-resolver, knot-resolver, unbound-resolver"
        exit 1
        ;;
esac