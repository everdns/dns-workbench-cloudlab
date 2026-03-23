#!/usr/bin/env bash
# lib/dns_server.sh — DNS server lifecycle management (bind, powerdns, knot, nsd, unbound)
# Uses configurable start/stop scripts on the server host.

# start_dns_server SOFTWARE
# Starts a DNS server on the server host and waits for it to be ready.
start_dns_server() {
    local software="$1"

    log_info "Starting DNS server: $software on server host..."
    ssh_server "${START_DNS_SCRIPT} ${software}" || die "Failed to start DNS server: $software"

    # Wait for DNS server to be ready (check port 53)
    log_info "Waiting for DNS server ($software) to be ready..."
    local timeout=30
    local elapsed=0
    while (( elapsed < timeout )); do
        if ssh_server "dig @127.0.0.1 -p ${RESOLVER_PORT} +short +time=1 +tries=1 example.com A" &>/dev/null; then
            log_info "DNS server $software is ready"
            return 0
        fi
        sleep 1
        (( elapsed++ ))
    done
    die "DNS server $software failed to start within ${timeout}s"
}

# stop_dns_server [SOFTWARE]
# Stops a DNS server on the server host.
# If SOFTWARE is omitted, stops all DNS services.
stop_dns_server() {
    local software="${1:-}"

    if [[ -n "$software" ]]; then
        log_info "Stopping DNS server: $software..."
        ssh_server "${STOP_DNS_SCRIPT} ${software}" || log_warn "Failed to stop DNS server: $software"
    else
        log_info "Stopping all DNS services..."
        ssh_server "${STOP_DNS_SCRIPT}" || log_warn "Failed to stop DNS services"
    fi
}
