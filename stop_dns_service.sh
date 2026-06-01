#!/bin/sh

# Stop dns software services
# Usage: ./stop_dns_service.sh [software]
# If no argument is given, stop all services

stop_all() {
    for role in bind/ns bind/resolver powerdns/ns powerdns/resolver \
                knot/ns knot/resolver nsd/ns unbound/ns unbound/resolver; do
        /local/repository/$role/stop.sh
    done
}

if [ -z "$1" ]; then
    stop_all
    exit 0
fi

case "$1" in
    bind-resolver)
        /local/repository/bind/resolver/stop.sh
        ;;
    bind-ns)
        /local/repository/bind/ns/stop.sh
        ;;
    powerdns-resolver)
        /local/repository/powerdns/resolver/stop.sh
        ;;
    powerdns-ns)
        /local/repository/powerdns/ns/stop.sh
        ;;
    knot-resolver)
        /local/repository/knot/resolver/stop.sh
        ;;
    knot-ns)
        /local/repository/knot/ns/stop.sh
        ;;
    nsd-ns)
        /local/repository/nsd/ns/stop.sh
        ;;
    unbound-resolver)
        /local/repository/unbound/resolver/stop.sh
        ;;
    unbound-ns)
        /local/repository/unbound/ns/stop.sh
        ;;
    *)
        echo "Unknown software: $1"
        echo "Options: bind-resolver, bind-ns, powerdns-resolver, powerdns-ns, knot-resolver, knot-ns, nsd-ns, unbound-resolver, unbound-ns"
        echo "Or run with no argument to stop all services"
        exit 1
        ;;
esac
