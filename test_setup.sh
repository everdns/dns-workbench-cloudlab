#!/bin/bash

SERVER_IP=${1:-10.10.1.2}
SERVER_TYPE=${2:-NS}

if [[ "$SERVER_TYPE" == "NS" ]]; then
    servers=(
        bind-ns
        knot-ns
        powerdns-ns
        nsd-ns
        unbound-ns
    )
else
    servers=(
        bind-resolver
        knot-resolver
        powerdns-recursor
        unbound-resolver
    )
fi

./stop_dns_service.sh
for server in "${servers[@]}"; do
    echo "=== Testing $server ==="
    ./start_dns_service.sh "$server"
    dig @"$SERVER_IP" ns1.workbench.lan
    ./stop_dns_service.sh
done

