#!/usr/bin/env bash
# lib/validators.sh — Input validation for DNS load testing benchmark

# validate_config
# Checks that all required configuration variables are set and numeric where expected.
validate_config() {
    require_vars CLIENT_HOST SERVER_HOST RESOLVER

    local numeric_vars=(
        THREADS PORTS_PER_THREAD TIMEOUT RUNTIME PAUSE_BETWEEN_RUNS
        DELAY_BETWEEN_BURSTS DNS_RESPONDER_MARGIN
        DNS_RESPONDER_STARTUP_WAIT DNS_RESPONDER_SHUTDOWN_WAIT
        START_QPS QPS_STEP MAX_QPS
        ACCURACY_MIN_QPS ACCURACY_MAX_QPS ACCURACY_STEP TRIALS
        IMPACT_MIN_QPS IMPACT_MAX_QPS IMPACT_QPS_STEP IMPACT_TRIALS
    )
    local var
    for var in "${numeric_vars[@]}"; do
        if [[ -n "${!var:-}" && ! "${!var}" =~ ^[0-9]+$ ]]; then
            die "$var must be a positive integer, got: ${!var}"
        fi
    done

    if [[ -z "$DNS_RESPONDER_INTERFACE" ]]; then
        die "DNS_RESPONDER_INTERFACE must be set"
    fi

    log_info "Configuration validated"
}

# validate_ssh
# Checks SSH connectivity to both hosts.
validate_ssh() {
    ssh_check_connectivity
}

# validate_input_files
# Checks that required input files exist locally.
validate_input_files() {
    local tools_str="$1"  # space-separated list of tool names
    local need_dnsperf=0
    local need_dnspyre=0
    local tool

    for tool in $tools_str; do
        case "$tool" in
            dnspyre)
                need_dnspyre=1
                ;;
            *)
                need_dnsperf=1
                ;;
        esac
    done

    if (( need_dnsperf )) && [[ ! -f "$DNSPERF_INPUT" ]]; then
        die "dnsperf input file not found: $DNSPERF_INPUT"
    fi
    if (( need_dnspyre )) && [[ ! -f "$DNSPYRE_INPUT" ]]; then
        die "dnspyre input file not found: $DNSPYRE_INPUT"
    fi
    log_info "Input files validated"
}

# validate_tools TOOLS_STRING
# For each tool, SSH to client and check if the binary exists.
validate_tools() {
    local tools_str="$1"
    local tool binary
    local failed=0

    for tool in $tools_str; do
        binary=$(_tool_binary "$tool")
        if ! ssh_client "command -v $binary" &>/dev/null; then
            log_error "Tool binary not found on client: $binary (for tool: $tool)"
            failed=1
        fi
    done

    if (( failed )); then
        die "One or more tool binaries not found on client host"
    fi
    log_info "All tool binaries verified on client host"
}

# _tool_binary TOOL_NAME
# Maps a canonical tool name to its binary name.
_tool_binary() {
    case "$1" in
        dnsperf)                   echo "dnsperf" ;;
        dnsperf-workbench-slice)   echo "dnsperf-workbench" ;;
        dnsperf-workbench-lencse)  echo "dnsperf-workbench" ;;
        dnspyre)                   echo "dnspyre" ;;
        dnspyre-dnsworkbench)      echo "dnspyre-dnsworkbench" ;;
        dns64perfpp)               echo "dns64perf++" ;;
        dns64perfpp-workbench)     echo "dns64perfpp-workbench" ;;
        kxdpgun)                   echo "kxdpgun" ;;
        *) die "Unknown tool: $1" ;;
    esac
}

# upload_input_files TOOLS_STRING
# Uploads the required input files to the client host.
upload_input_files() {
    local tools_str="$1"
    local tool
    local need_dnsperf=0
    local need_dnspyre=0

    for tool in $tools_str; do
        case "$tool" in
            dnspyre)
                need_dnspyre=1
                ;;
            *)
                need_dnsperf=1
                ;;
        esac
    done

    ssh_setup_work_dir "client"

    if (( need_dnsperf )); then
        log_info "Uploading dnsperf_input to client..."
        scp_to_client "$DNSPERF_INPUT" "${REMOTE_WORK_DIR}/dnsperf_input"
    fi
    if (( need_dnspyre )); then
        log_info "Uploading dnspyre_input to client..."
        scp_to_client "$DNSPYRE_INPUT" "${REMOTE_WORK_DIR}/dnspyre_input"
    fi
}
