#!/bin/bash

# Stop dns software services
# Usage: ./stop_dns_service.sh [software]
# If no argument is given, stop all services

stop_all() {
    for svc in named pdns pdns-recursor knot-resolver knot; do
        if systemctl is-active --quiet "$svc" 2>/dev/null; then
            echo "Stopping $svc..."
            sudo systemctl stop "$svc"
        fi
    done
    sudo pkill nsd 2>/dev/null
    sudo pkill unbound 2>/dev/null
}

if [ -z "$1" ]; then
    stop_all
    exit 0
fi

case "$1" in
    bind-resolver|bind-ns)
        if systemctl is-active --quiet named 2>/dev/null; then
            echo "Stopping named..."
            sudo systemctl stop named
        fi
        ;;
    powerdns-resolver)
        if systemctl is-active --quiet pdns-recursor 2>/dev/null; then
            echo "Stopping pdns-recursor..."
            sudo systemctl stop pdns-recursor
        fi
        ;;
    powerdns-ns)
        if systemctl is-active --quiet pdns 2>/dev/null; then
            echo "Stopping pdns..."
            sudo systemctl stop pdns
        fi
        ;;
    knot-resolver)
        if systemctl is-active --quiet knot-resolver 2>/dev/null; then
            echo "Stopping knot-resolver..."
            sudo systemctl stop knot-resolver
        fi
        ;;
    knot-ns)
        if systemctl is-active --quiet knot 2>/dev/null; then
            echo "Stopping knot..."
            sudo systemctl stop knot
        fi
        ;;
    nsd-ns)
        echo "Stopping nsd..."
        sudo pkill nsd 2>/dev/null
        ;;
    unbound-resolver)
        echo "Stopping unbound..."
        sudo pkill unbound 2>/dev/null
        ;;
    *)
        echo "Unknown software: $1"
        echo "Options: bind-resolver, bind-ns, powerdns-resolver, powerdns-ns, knot-resolver, knot-ns, nsd-ns, unbound-resolver"
        echo "Or run with no argument to stop all services"
        exit 1
        ;;
esac