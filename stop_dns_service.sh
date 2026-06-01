#!/bin/sh

# Stop dns software services
# Usage: ./stop_dns_service.sh [software]
# If no argument is given, stop all services

stop_all() {
    for role in ns_software/bind resolver_software/bind ns_software/powerdns resolver_software/powerdns \
                ns_software/knot resolver_software/knot ns_software/nsd ns_software/unbound resolver_software/unbound; do
        /local/repository/$role/stop.sh
    done
}

if [ -z "$1" ]; then
    stop_all
    exit 0
fi

case "$1" in
    bind-resolver)
        /local/repository/resolver_software/bind/stop.sh
        ;;
    bind-ns)
        /local/repository/ns_software/bind/stop.sh
        ;;
    powerdns-resolver)
        /local/repository/resolver_software/powerdns/stop.sh
        ;;
    powerdns-ns)
        /local/repository/ns_software/powerdns/stop.sh
        ;;
    knot-resolver)
        /local/repository/resolver_software/knot/stop.sh
        ;;
    knot-ns)
        /local/repository/ns_software/knot/stop.sh
        ;;
    nsd-ns)
        /local/repository/ns_software/nsd/stop.sh
        ;;
    unbound-resolver)
        /local/repository/resolver_software/unbound/stop.sh
        ;;
    unbound-ns)
        /local/repository/ns_software/unbound/stop.sh
        ;;
    *)
        echo "Unknown software: $1"
        echo "Options: bind-resolver, bind-ns, powerdns-resolver, powerdns-ns, knot-resolver, knot-ns, nsd-ns, unbound-resolver, unbound-ns"
        echo "Or run with no argument to stop all services"
        exit 1
        ;;
esac
