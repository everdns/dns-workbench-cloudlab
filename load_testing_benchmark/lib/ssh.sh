#!/usr/bin/env bash
# lib/ssh.sh — SSH coordination layer for DNS load testing benchmark
# Provides wrappers for executing commands on client/server hosts,
# background process management, file transfer, and cleanup.

# ──────────────────────────────────────────────
# Remote process tracking
# ──────────────────────────────────────────────

# Arrays to track remote background processes for cleanup
declare -a _REMOTE_CLIENT_PIDS=()
declare -a _REMOTE_SERVER_PIDS=()
_CLEANUP_REGISTERED=0

# ──────────────────────────────────────────────
# Core SSH functions
# ──────────────────────────────────────────────

# ssh_client CMD
# Execute CMD on the client host. Returns the remote exit code.
ssh_client() {
    ssh $SSH_OPTS "${SSH_USER}@${CLIENT_HOST}" "$1"
}

# ssh_server CMD
# Execute CMD on the server host. Returns the remote exit code.
ssh_server() {
    ssh $SSH_OPTS "${SSH_USER}@${SERVER_HOST}" "$1"
}

# ssh_client_bg CMD
# Run CMD on the client host in the background.
# Prints the remote PID and tracks it for cleanup.
ssh_client_bg() {
    local cmd="$1"
    local pid
    pid=$(ssh $SSH_OPTS "${SSH_USER}@${CLIENT_HOST}" \
        "nohup bash -c '${cmd}' </dev/null &>/dev/null & echo \$!")
    if [[ -n "$pid" && "$pid" =~ ^[0-9]+$ ]]; then
        _REMOTE_CLIENT_PIDS+=("$pid")
        _register_cleanup
        echo "$pid"
    else
        log_error "Failed to start background process on client: $cmd"
        return 1
    fi
}

# ssh_server_bg CMD [OUTPUT_FILE]
# Run CMD on the server host in the background.
# If OUTPUT_FILE is provided, stdout/stderr are redirected there on the remote.
# Prints the remote PID and tracks it for cleanup.
ssh_server_bg() {
    local cmd="$1"
    local output_file="${2:-/dev/null}"
    local pid
    pid=$(ssh $SSH_OPTS "${SSH_USER}@${SERVER_HOST}" \
        "nohup bash -c '${cmd}' </dev/null >${output_file} 2>&1 & echo \$!")
    if [[ -n "$pid" && "$pid" =~ ^[0-9]+$ ]]; then
        _REMOTE_SERVER_PIDS+=("$pid")
        _register_cleanup
        echo "$pid"
    else
        log_error "Failed to start background process on server: $cmd"
        return 1
    fi
}

# ──────────────────────────────────────────────
# Remote process management
# ──────────────────────────────────────────────

# ssh_kill_remote HOST_TYPE PID
# Kill a remote process. HOST_TYPE is "client" or "server".
ssh_kill_remote() {
    local host_type="$1" pid="$2"
    local ssh_func="ssh_${host_type}"

    # Send TERM first, then KILL if still alive
    $ssh_func "kill -TERM $pid 2>/dev/null" 2>/dev/null
    sleep 1
    $ssh_func "kill -0 $pid 2>/dev/null && kill -9 $pid 2>/dev/null" 2>/dev/null

    # Remove from tracking array
    _remove_tracked_pid "$host_type" "$pid"
}

# ssh_wait_remote HOST_TYPE PID [TIMEOUT_S]
# Wait for a remote process to exit. Returns 0 if it exited, 1 on timeout.
ssh_wait_remote() {
    local host_type="$1" pid="$2" timeout_s="${3:-300}"
    local ssh_func="ssh_${host_type}"
    local elapsed=0

    while (( elapsed < timeout_s )); do
        if ! $ssh_func "kill -0 $pid 2>/dev/null" 2>/dev/null; then
            _remove_tracked_pid "$host_type" "$pid"
            return 0
        fi
        sleep 1
        (( elapsed++ ))
    done
    return 1
}

# ──────────────────────────────────────────────
# File transfer
# ──────────────────────────────────────────────

scp_to_client() {
    local src="$1" dst="$2"
    scp $SSH_OPTS "$src" "${SSH_USER}@${CLIENT_HOST}:${dst}"
}

scp_to_server() {
    local src="$1" dst="$2"
    scp $SSH_OPTS "$src" "${SSH_USER}@${SERVER_HOST}:${dst}"
}

scp_from_client() {
    local src="$1" dst="$2"
    scp $SSH_OPTS "${SSH_USER}@${CLIENT_HOST}:${src}" "$dst"
}

scp_from_server() {
    local src="$1" dst="$2"
    scp $SSH_OPTS "${SSH_USER}@${SERVER_HOST}:${src}" "$dst"
}

# ──────────────────────────────────────────────
# Connectivity checks
# ──────────────────────────────────────────────

# ssh_check_connectivity
# Verifies SSH access to both client and server hosts.
ssh_check_connectivity() {
    log_info "Checking SSH connectivity to client ($CLIENT_HOST)..."
    log_info "Using $ssh_client to check connectivity..."
    if ! ssh_client "echo ok" &>/dev/null; then
        die "Cannot SSH to client host: ${SSH_USER}@${CLIENT_HOST}"
    fi
    log_info "Checking SSH connectivity to server ($SERVER_HOST)..."
    if ! ssh_server "echo ok" &>/dev/null; then
        die "Cannot SSH to server host: ${SSH_USER}@${SERVER_HOST}"
    fi
    log_info "SSH connectivity verified"
}

# ssh_setup_work_dir HOST_TYPE
# Create the remote working directory on the specified host.
ssh_setup_work_dir() {
    local host_type="$1"
    local ssh_func="ssh_${host_type}"
    $ssh_func "mkdir -p ${REMOTE_WORK_DIR}" || die "Failed to create work dir on $host_type"
}

# ──────────────────────────────────────────────
# Cleanup
# ──────────────────────────────────────────────

# _register_cleanup
# Registers the cleanup trap if not already done.
_register_cleanup() {
    if (( _CLEANUP_REGISTERED == 0 )); then
        trap _cleanup_all EXIT INT TERM
        _CLEANUP_REGISTERED=1
    fi
}

# _cleanup_all
# Kills all tracked remote processes on both hosts.
_cleanup_all() {
    local pid
    if (( ${#_REMOTE_SERVER_PIDS[@]} > 0 )); then
        log_warn "Cleaning up ${#_REMOTE_SERVER_PIDS[@]} remote server process(es)..."
        for pid in "${_REMOTE_SERVER_PIDS[@]}"; do
            ssh $SSH_OPTS "${SSH_USER}@${SERVER_HOST}" "kill -TERM $pid 2>/dev/null" 2>/dev/null
        done
        sleep 1
        for pid in "${_REMOTE_SERVER_PIDS[@]}"; do
            ssh $SSH_OPTS "${SSH_USER}@${SERVER_HOST}" "kill -9 $pid 2>/dev/null" 2>/dev/null
        done
        _REMOTE_SERVER_PIDS=()
    fi
    if (( ${#_REMOTE_CLIENT_PIDS[@]} > 0 )); then
        log_warn "Cleaning up ${#_REMOTE_CLIENT_PIDS[@]} remote client process(es)..."
        for pid in "${_REMOTE_CLIENT_PIDS[@]}"; do
            ssh $SSH_OPTS "${SSH_USER}@${CLIENT_HOST}" "kill -TERM $pid 2>/dev/null" 2>/dev/null
        done
        sleep 1
        for pid in "${_REMOTE_CLIENT_PIDS[@]}"; do
            ssh $SSH_OPTS "${SSH_USER}@${CLIENT_HOST}" "kill -9 $pid 2>/dev/null" 2>/dev/null
        done
        _REMOTE_CLIENT_PIDS=()
    fi
}

# _remove_tracked_pid HOST_TYPE PID
_remove_tracked_pid() {
    local host_type="$1" pid="$2"
    local -n arr="_REMOTE_${host_type^^}_PIDS"
    local new_arr=()
    local p
    for p in "${arr[@]}"; do
        if [[ "$p" != "$pid" ]]; then
            new_arr+=("$p")
        fi
    done
    arr=("${new_arr[@]}")
}
