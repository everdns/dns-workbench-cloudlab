#!/usr/bin/env bash
# lib/dns_responder.sh — dns_responder lifecycle management

# Global state for the current dns_responder instance
_DNS_RESPONDER_PID=""
_DNS_RESPONDER_OUTPUT_REMOTE=""
_DNS_RESPONDER_TS_REMOTE=""

# start_dns_responder [--timestamps REMOTE_TS_FILE]
# Starts dns_responder on the server host in the background.
# Output is captured to a remote file. If --timestamps is passed,
# the -t flag is added and timestamps are written to the specified file.
start_dns_responder() {
    local use_timestamps=0
    local ts_remote_file=""

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --timestamps)
                use_timestamps=1
                ts_remote_file="$2"
                shift 2
                ;;
            *)
                die "start_dns_responder: unknown argument: $1"
                ;;
        esac
    done

    if [[ -n "$_DNS_RESPONDER_PID" ]]; then
        log_warn "dns_responder already running (PID $_DNS_RESPONDER_PID), stopping first"
        stop_dns_responder
    fi

    ssh_setup_work_dir "server"

    _DNS_RESPONDER_OUTPUT_REMOTE="${REMOTE_WORK_DIR}/dns_responder_output.txt"

    local cmd="sudo ${DNS_RESPONDER_BIN} -i ${DNS_RESPONDER_INTERFACE}"

    if (( use_timestamps )) && [[ -n "$ts_remote_file" ]]; then
        cmd+=" -t ${ts_remote_file}"
        _DNS_RESPONDER_TS_REMOTE="$ts_remote_file"
    fi

    log_info "Starting dns_responder on server (interface: ${DNS_RESPONDER_INTERFACE})..."

    _DNS_RESPONDER_PID=$(ssh_server_bg "$cmd" "$_DNS_RESPONDER_OUTPUT_REMOTE")

    if [[ -z "$_DNS_RESPONDER_PID" ]]; then
        die "Failed to start dns_responder on server"
    fi

    # Wait for dns_responder to be ready
    sleep "$DNS_RESPONDER_STARTUP_WAIT"

    log_info "dns_responder started (remote PID: $_DNS_RESPONDER_PID)"
}

# stop_dns_responder
# Waits DNS_RESPONDER_SHUTDOWN_WAIT seconds after the test, then stops dns_responder.
stop_dns_responder() {
    if [[ -z "$_DNS_RESPONDER_PID" ]]; then
        log_warn "No dns_responder process to stop"
        return 0
    fi

    log_info "Waiting ${DNS_RESPONDER_SHUTDOWN_WAIT}s before stopping dns_responder..."
    sleep "$DNS_RESPONDER_SHUTDOWN_WAIT"

    log_info "Stopping dns_responder (PID: $_DNS_RESPONDER_PID)..."
    ssh_kill_remote "server" "$_DNS_RESPONDER_PID"
    _DNS_RESPONDER_PID=""
}

# fetch_dns_responder_output LOCAL_PATH
# Downloads the dns_responder output file from the server.
fetch_dns_responder_output() {
    local local_path="$1"
    if [[ -z "$_DNS_RESPONDER_OUTPUT_REMOTE" ]]; then
        log_warn "No dns_responder output file to fetch"
        return 1
    fi
    scp_from_server "$_DNS_RESPONDER_OUTPUT_REMOTE" "$local_path"
    _DNS_RESPONDER_OUTPUT_REMOTE=""
}

# fetch_dns_responder_timestamps LOCAL_PATH
# Downloads the dns_responder timestamp file from the server.
fetch_dns_responder_timestamps() {
    local local_path="$1"
    if [[ -z "$_DNS_RESPONDER_TS_REMOTE" ]]; then
        log_warn "No dns_responder timestamp file to fetch"
        return 1
    fi
    scp_from_server "$_DNS_RESPONDER_TS_REMOTE" "$local_path"
    _DNS_RESPONDER_TS_REMOTE=""
}
