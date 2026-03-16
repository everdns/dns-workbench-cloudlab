#!/bin/sh
if [ -z "$1" ]; then
    echo "Usage: $0 <software>"
    echo "Options: bind-resolver, bind-ns, powerdns-resolver, powerdns-ns, knot-resolver, knot-ns, nsd-ns, unbound-resolver"
    exit 1
fi

case "$1" in
    bind-resolver)
        /local/repository/bind/resolver/start.sh
        ;;
    bind-ns)
        /local/repository/bind/ns/start.sh
        ;;
    powerdns-resolver)
        /local/repository/powerdns/resolver/start.sh
        ;;
    powerdns-ns)
        /local/repository/powerdns/ns/start.sh
        ;;
    knot-resolver)
        /local/repository/knot/resolver/start.sh
        ;;
    knot-ns)
        /local/repository/knot/ns/start.sh
        ;;
    nsd-ns)
        /local/repository/nsd/ns/start.sh
        ;;
    unbound-resolver)
        /local/repository/unbound/resolver/start.sh
        ;;
    unbound-ns)
        /local/repository/unbound/ns/start.sh
        ;;
    *)
        echo "Unknown software: $1"
        echo "Options: bind-resolver, bind-ns, powerdns-resolver, powerdns-ns, knot-resolver, knot-ns, nsd-ns, unbound-resolver, unbound-ns"
        exit 1
        ;;
esac
