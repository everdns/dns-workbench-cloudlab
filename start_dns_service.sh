#!/bin/sh
if [ -z "$1" ]; then
    echo "Usage: $0 <software>"
    echo "Options: bind-resolver, bind-ns, powerdns-resolver, powerdns-ns, knot-resolver, knot-ns, nsd-ns, unbound-resolver"
    exit 1
fi

case "$1" in
    bind-resolver)
        /local/repository/resolver_software/bind/start.sh
        ;;
    bind-ns)
        /local/repository/ns_software/bind/start.sh
        ;;
    powerdns-resolver)
        /local/repository/resolver_software/powerdns/start.sh
        ;;
    powerdns-ns)
        /local/repository/ns_software/powerdns/start.sh
        ;;
    knot-resolver)
        /local/repository/resolver_software/knot/start.sh
        ;;
    knot-ns)
        /local/repository/ns_software/knot/start.sh
        ;;
    nsd-ns)
        /local/repository/ns_software/nsd/start.sh
        ;;
    unbound-resolver)
        /local/repository/resolver_software/unbound/start.sh
        ;;
    unbound-ns)
        /local/repository/ns_software/unbound/start.sh
        ;;
    *)
        echo "Unknown software: $1"
        echo "Options: bind-resolver, bind-ns, powerdns-resolver, powerdns-ns, knot-resolver, knot-ns, nsd-ns, unbound-resolver, unbound-ns"
        exit 1
        ;;
esac
