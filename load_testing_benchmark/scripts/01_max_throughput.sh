#!/usr/bin/env bash
# scripts/01_max_throughput.sh — Maximum Throughput Discovery
#
# Determines the maximum sustainable QPS for each tool by ramping from
# START_QPS to MAX_QPS in QPS_STEP increments against dns_responder.
#
# Usage:
#   ./scripts/01_max_throughput.sh [OPTIONS]
#
# Options override config.sh defaults:
#   --client=HOST         Client host (SSH target)
#   --server=HOST         Server host (SSH target)
#   --resolver=IP         Resolver IP address
#   --tools="t1 t2 ..."   Space-separated list of tools to test
#   --start-qps=N         Starting QPS (default: 200000)
#   --max-qps=N           Maximum QPS (default: 3000000)
#   --step=N              QPS step size (default: 10000)
#   --runtime=N           Seconds per test run (default: 10)
#   --threads=N           Thread count (default: 20)
#   --results-dir=PATH    Output directory

set -euo pipefail

# ──────────────────────────────────────────────
# Resolve script directory and source libraries
# ──────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

source "$BASE_DIR/config.sh"

# Parse CLI arguments (override config before sourcing libs)
while [[ $# -gt 0 ]]; do
    case "$1" in
        --client=*)         CLIENT_HOST="${1#*=}" ;;
        --server=*)         SERVER_HOST="${1#*=}" ;;
        --resolver=*)       RESOLVER="${1#*=}" ;;
        --tools=*)          ENABLED_TOOLS="${1#*=}" ;;
        --start-qps=*)     START_QPS="${1#*=}" ;;
        --max-qps=*)       MAX_QPS="${1#*=}" ;;
        --step=*)           QPS_STEP="${1#*=}" ;;
        --runtime=*)        RUNTIME="${1#*=}" ;;
        --threads=*)        THREADS="${1#*=}" ;;
        --results-dir=*)    RESULTS_DIR="${1#*=}" ;;
        --interface=*)      DNS_RESPONDER_INTERFACE="${1#*=}" ;;
        --kxdpgun-iface=*)  KXDPGUN_INTERFACE="${1#*=}" ;;
        --pause=*)          PAUSE_BETWEEN_RUNS="${1#*=}" ;;
        --startup-wait=*)   DNS_RESPONDER_STARTUP_WAIT="${1#*=}" ;;
        --shutdown-wait=*)  DNS_RESPONDER_SHUTDOWN_WAIT="${1#*=}" ;;
        --help|-h)
            sed -n '2,/^$/s/^# //p' "$0"
            exit 0
            ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
    shift
done

# Source libraries
source "$BASE_DIR/lib/common.sh"
source "$BASE_DIR/lib/ssh.sh"
source "$BASE_DIR/lib/dns_responder.sh"
source "$BASE_DIR/lib/dns_server.sh"
source "$BASE_DIR/lib/tools.sh"
source "$BASE_DIR/lib/validators.sh"
source "$BASE_DIR/lib/parsers/parser_common.sh"
source "$BASE_DIR/lib/parsers/parser_dnsperf.sh"
source "$BASE_DIR/lib/parsers/parser_dnspyre.sh"
source "$BASE_DIR/lib/parsers/parser_dns64perfpp.sh"
source "$BASE_DIR/lib/parsers/parser_kxdpgun.sh"

# ──────────────────────────────────────────────
# Tool → parser mapping
# ──────────────────────────────────────────────
parse_tool_output() {
    local tool="$1" raw_file="$2"
    case "$tool" in
        dnsperf|dnsperf-workbench-slice|dnsperf-workbench-lencse)
            parse_dnsperf "$raw_file" ;;
        dnspyre|dnspyre-dnsworkbench)
            parse_dnspyre "$raw_file" ;;
        dns64perfpp|dns64perfpp-workbench)
            parse_dns64perfpp "$raw_file" ;;
        kxdpgun)
            parse_kxdpgun "$raw_file" ;;
        *)
            log_error "No parser for tool: $tool"
            echo "0 0 0 0 0 0 0 0"
            ;;
    esac
}

# ──────────────────────────────────────────────
# Validation
# ──────────────────────────────────────────────
log_info "=== DNS Load Testing Benchmark: Maximum Throughput Discovery ==="
log_info "Configuration: START_QPS=$START_QPS, MAX_QPS=$MAX_QPS, QPS_STEP=$QPS_STEP, RUNTIME=${RUNTIME}s"
log_info "Tools: $ENABLED_TOOLS"

validate_config
validate_ssh
validate_input_files "$ENABLED_TOOLS"
validate_tools "$ENABLED_TOOLS"

# ──────────────────────────────────────────────
# Setup output directories
# ──────────────────────────────────────────────
RUN_ID=$(generate_run_id)
OUTPUT_DIR="${RESULTS_DIR}/01_max_throughput/${RUN_ID}"
RAW_DIR="${OUTPUT_DIR}/raw"
ensure_dir "$RAW_DIR"

SUMMARY_CSV="${OUTPUT_DIR}/summary.csv"
init_csv "$SUMMARY_CSV" \
    "tool" "requested_qps" "achieved_qps" \
    "latency_avg_ms" "latency_min_ms" "latency_max_ms" "latency_stddev_ms" \
    "queries_sent" "queries_completed" "queries_lost" "answer_rate" \
    "raw_file"

# Upload input files to client
upload_input_files "$ENABLED_TOOLS"

# ──────────────────────────────────────────────
# Main test loop
# ──────────────────────────────────────────────
log_info "Starting throughput ramp: $START_QPS → $MAX_QPS (step $QPS_STEP)"

qps=$START_QPS
while (( qps <= MAX_QPS )); do
    for tool in $ENABLED_TOOLS; do
        log_info "─── Testing $tool at ${qps} QPS ───"

        # Build the command
        cmd=$(build_tool_cmd "$tool" "$qps")
        raw_file="${RAW_DIR}/${tool}_${qps}.txt"

        # Start dns_responder on server
        start_dns_responder

        # Run the load generator on client
        log_info "Running: $cmd"
        if ssh_client "$cmd" > "$raw_file" 2>&1; then
            log_info "Tool $tool completed successfully"
        else
            log_warn "Tool $tool exited with non-zero status at ${qps} QPS"
        fi

        # Stop dns_responder (waits DNS_RESPONDER_SHUTDOWN_WAIT first)
        stop_dns_responder

        # Parse results
        local_raw_file="$raw_file"
        metrics=$(parse_tool_output "$tool" "$local_raw_file")
        read -r achieved_qps lat_avg lat_min lat_max lat_stddev q_sent q_completed q_lost <<< "$metrics"

        # Compute answer rate
        answer_rate=$(safe_divide "$q_completed" "$q_sent" 6)

        # Append to CSV
        append_csv "$SUMMARY_CSV" \
            "$tool" "$qps" "$achieved_qps" \
            "$lat_avg" "$lat_min" "$lat_max" "$lat_stddev" \
            "$q_sent" "$q_completed" "$q_lost" "$answer_rate" \
            "raw/${tool}_${qps}.txt"

        log_info "Result: achieved=${achieved_qps} QPS, latency_avg=${lat_avg}ms, answer_rate=${answer_rate}"

        # Pause between runs
        if (( PAUSE_BETWEEN_RUNS > 0 )); then
            log_info "Pausing ${PAUSE_BETWEEN_RUNS}s between runs..."
            sleep "$PAUSE_BETWEEN_RUNS"
        fi
    done

    qps=$(( qps + QPS_STEP ))
done

# ──────────────────────────────────────────────
# Generate JSON summary
# ──────────────────────────────────────────────
SUMMARY_JSON="${OUTPUT_DIR}/summary.json"
csv_to_ndjson "$SUMMARY_CSV" "$SUMMARY_JSON"

log_info "=== Maximum Throughput Discovery Complete ==="
log_info "Results: $OUTPUT_DIR"
log_info "CSV: $SUMMARY_CSV"
log_info "JSON: $SUMMARY_JSON"
