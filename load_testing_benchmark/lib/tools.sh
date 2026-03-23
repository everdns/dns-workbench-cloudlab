#!/usr/bin/env bash
# lib/tools.sh — Command builders for all 8 DNS load testing tools

# ──────────────────────────────────────────────
# dnsperf
# ──────────────────────────────────────────────

build_cmd_dnsperf() {
    local qps=$1
    local clients=$(( THREADS * PORTS_PER_THREAD ))
    local max_outstanding=$(compute_max_outstanding "$THREADS")
    echo "cd ${REMOTE_WORK_DIR} && dnsperf -s ${RESOLVER} -l ${RUNTIME} -d dnsperf_input" \
         "-c ${clients} -T ${THREADS}" \
         "-Q ${qps} -q ${max_outstanding}" \
         "-O suppress=timeout -O qps-threshold-wait=${QPS_THRESHOLD_WAIT} -t ${TIMEOUT}"
}

# ──────────────────────────────────────────────
# dnsperf-workbench (slice rate limiter)
# ──────────────────────────────────────────────

build_cmd_dnsperf_workbench_slice() {
    local qps=$1
    local clients=$(( THREADS * PORTS_PER_THREAD ))
    local max_outstanding=$(compute_max_outstanding "$THREADS")
    echo "cd ${REMOTE_WORK_DIR} && dnsperf-workbench -s ${RESOLVER} -l ${RUNTIME} -d dnsperf_input" \
         "-c ${clients} -T ${THREADS}" \
         "-Q ${qps} -q ${max_outstanding}" \
         "-O suppress=timeout -O rate-limiter=slice" \
         "-O qps-threshold-wait=${QPS_THRESHOLD_WAIT} -t ${TIMEOUT}"
}

# ──────────────────────────────────────────────
# dnsperf-workbench (lencse rate limiter)
# ──────────────────────────────────────────────

build_cmd_dnsperf_workbench_lencse() {
    local qps=$1
    local clients=$(( THREADS * PORTS_PER_THREAD ))
    local max_outstanding=$(compute_max_outstanding "$THREADS")
    echo "cd ${REMOTE_WORK_DIR} && dnsperf-workbench -s ${RESOLVER} -l ${RUNTIME} -d dnsperf_input" \
         "-c ${clients} -T ${THREADS}" \
         "-Q ${qps} -q ${max_outstanding}" \
         "-O suppress=timeout -O rate-limiter=lencse" \
         "-O qps-threshold-wait=${QPS_THRESHOLD_WAIT} -t ${TIMEOUT}"
}

# ──────────────────────────────────────────────
# dnspyre
# ──────────────────────────────────────────────

build_cmd_dnspyre() {
    local qps=$1
    echo "cd ${REMOTE_WORK_DIR} && dnspyre --type=A --server ${RESOLVER}" \
         "--duration ${RUNTIME}s -c ${THREADS}" \
         "--rate-limit ${qps} --request=${TIMEOUT}s" \
         "@dnspyre_input"
}

# ──────────────────────────────────────────────
# dnspyre-dnsworkbench
# ──────────────────────────────────────────────

build_cmd_dnspyre_dnsworkbench() {
    local qps=$1
    echo "cd ${REMOTE_WORK_DIR} && dnspyre-dnsworkbench --server ${RESOLVER}" \
         "--duration ${RUNTIME}s -c ${THREADS}" \
         "--rate-limit ${qps} --request=${TIMEOUT}s" \
         "@dnsperf_input"
}

# ──────────────────────────────────────────────
# dns64perf++
# ──────────────────────────────────────────────

build_cmd_dns64perfpp() {
    local qps=$1
    local burst_size=$(compute_burst_size "$qps" "$DELAY_BETWEEN_BURSTS" "$THREADS")
    local num_requests=$(compute_number_of_requests "$qps" "$RUNTIME" "$burst_size" "$THREADS")
    echo "dns64perf++ ${RESOLVER} ${RESOLVER_PORT} ${SUBNET}" \
         "${num_requests} ${burst_size} ${THREADS}" \
         "${PORTS_PER_THREAD} ${DELAY_BETWEEN_BURSTS} ${TIMEOUT}"
}

# ──────────────────────────────────────────────
# dns64perfpp-workbench
# ──────────────────────────────────────────────

build_cmd_dns64perfpp_workbench() {
    local qps=$1
    local burst_size=$(compute_burst_size "$qps" "$DELAY_BETWEEN_BURSTS" "$THREADS")
    local num_requests=$(compute_number_of_requests "$qps" "$RUNTIME" "$burst_size" "$THREADS")
    echo "cd ${REMOTE_WORK_DIR} && dns64perfpp-workbench ${RESOLVER} ${RESOLVER_PORT} dnsperf_input" \
         "${num_requests} ${burst_size} ${THREADS}" \
         "${PORTS_PER_THREAD} ${DELAY_BETWEEN_BURSTS} ${TIMEOUT}"
}

# ──────────────────────────────────────────────
# kxdpgun
# ──────────────────────────────────────────────

build_cmd_kxdpgun() {
    local qps=$1
    echo "cd ${REMOTE_WORK_DIR} && sudo kxdpgun -t ${RUNTIME} -Q ${qps} -b 1" \
         "-i dnsperf_input -I ${KXDPGUN_INTERFACE}"
}

# ──────────────────────────────────────────────
# Dispatcher
# ──────────────────────────────────────────────

# build_tool_cmd TOOL_NAME QPS
# Returns the full command string for the given tool at the specified QPS.
build_tool_cmd() {
    local tool="$1" qps="$2"
    case "$tool" in
        dnsperf)                   build_cmd_dnsperf "$qps" ;;
        dnsperf-workbench-slice)   build_cmd_dnsperf_workbench_slice "$qps" ;;
        dnsperf-workbench-lencse)  build_cmd_dnsperf_workbench_lencse "$qps" ;;
        dnspyre)                   build_cmd_dnspyre "$qps" ;;
        dnspyre-dnsworkbench)      build_cmd_dnspyre_dnsworkbench "$qps" ;;
        dns64perfpp)               build_cmd_dns64perfpp "$qps" ;;
        dns64perfpp-workbench)     build_cmd_dns64perfpp_workbench "$qps" ;;
        kxdpgun)                   build_cmd_kxdpgun "$qps" ;;
        *)                         die "Unknown tool: $tool" ;;
    esac
}

# get_tool_input_file TOOL_NAME
# Returns which input file format a tool uses.
get_tool_input_file() {
    case "$1" in
        dnspyre) echo "dnspyre_input" ;;
        *)       echo "dnsperf_input" ;;
    esac
}
