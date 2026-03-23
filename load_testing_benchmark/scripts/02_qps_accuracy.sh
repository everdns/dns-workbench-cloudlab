#!/usr/bin/env bash
# scripts/02_qps_accuracy.sh — QPS Accuracy Evaluation
#
# Measures how accurately each tool achieves a specified QPS using
# round-robin scheduling across tools. Uses dns_responder timestamps
# to compute QPS at 1s, 100ms, and 10ms intervals.
#
# Usage:
#   ./scripts/02_qps_accuracy.sh [OPTIONS]
#
# Options override config.sh defaults:
#   --client=HOST         Client host (SSH target)
#   --server=HOST         Server host (SSH target)
#   --resolver=IP         Resolver IP address
#   --tools="t1 t2 ..."   Space-separated list of tools to test
#   --min-qps=N           Minimum QPS (default: 100000)
#   --max-qps=N           Maximum QPS (default: 2000000)
#   --step=N              QPS step size (default: 50000)
#   --trials=N            Number of trials per tool per QPS (default: 10)
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

# Parse CLI arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --client=*)         CLIENT_HOST="${1#*=}" ;;
        --server=*)         SERVER_HOST="${1#*=}" ;;
        --resolver=*)       RESOLVER="${1#*=}" ;;
        --tools=*)          ENABLED_TOOLS="${1#*=}" ;;
        --min-qps=*)        ACCURACY_MIN_QPS="${1#*=}" ;;
        --max-qps=*)        ACCURACY_MAX_QPS="${1#*=}" ;;
        --step=*)           ACCURACY_STEP="${1#*=}" ;;
        --trials=*)         TRIALS="${1#*=}" ;;
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
source "$BASE_DIR/lib/parsers/parser_dns_responder.sh"

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
log_info "=== DNS Load Testing Benchmark: QPS Accuracy Evaluation ==="
log_info "Configuration: QPS range=$ACCURACY_MIN_QPS-$ACCURACY_MAX_QPS, step=$ACCURACY_STEP, trials=$TRIALS, runtime=${RUNTIME}s"
log_info "Tools: $ENABLED_TOOLS"

validate_config
validate_ssh
validate_input_files "$ENABLED_TOOLS"
validate_tools "$ENABLED_TOOLS"

# ──────────────────────────────────────────────
# Setup output directories
# ──────────────────────────────────────────────
RUN_ID=$(generate_run_id)
OUTPUT_DIR="${RESULTS_DIR}/02_qps_accuracy/${RUN_ID}"
RAW_DIR="${OUTPUT_DIR}/raw"
TS_DIR="${OUTPUT_DIR}/dns_responder_logs"
INTERVAL_DIR="${OUTPUT_DIR}/intervals"
ensure_dir "$RAW_DIR"
ensure_dir "$TS_DIR"
ensure_dir "$INTERVAL_DIR"

TRIALS_CSV="${OUTPUT_DIR}/trials.csv"
init_csv "$TRIALS_CSV" \
    "tool" "requested_qps" "trial" "achieved_qps" \
    "qps_1s_mean" "qps_1s_stddev" "qps_1s_max_dev" \
    "qps_100ms_mean" "qps_100ms_stddev" "qps_100ms_max_dev" \
    "qps_10ms_mean" "qps_10ms_stddev" "qps_10ms_max_dev" \
    "raw_file" "ts_file"

# Upload input files to client
upload_input_files "$ENABLED_TOOLS"
ssh_setup_work_dir "server"

# ──────────────────────────────────────────────
# Main test loop — round-robin scheduling
# ──────────────────────────────────────────────
log_info "Starting QPS accuracy evaluation: $ACCURACY_MIN_QPS → $ACCURACY_MAX_QPS (step $ACCURACY_STEP), $TRIALS trials"

# Convert ENABLED_TOOLS string to array
read -ra tools_array <<< "$ENABLED_TOOLS"

for (( trial = 1; trial <= TRIALS; trial++ )); do
    log_info "=== Trial $trial / $TRIALS ==="

    qps=$ACCURACY_MIN_QPS
    while (( qps <= ACCURACY_MAX_QPS )); do
        # Shuffle tool order for this rotation to reduce systematic bias
        mapfile -t shuffled_tools < <(shuffle_array "${tools_array[@]}")

        for tool in "${shuffled_tools[@]}"; do
            log_info "─── Trial $trial: $tool at ${qps} QPS ───"

            cmd=$(build_tool_cmd "$tool" "$qps")
            raw_file="${RAW_DIR}/${tool}_${qps}_trial${trial}.txt"
            ts_remote="${REMOTE_WORK_DIR}/timestamps_${tool}_${qps}_trial${trial}.txt"
            ts_local="${TS_DIR}/${tool}_${qps}_trial${trial}.txt"

            # Start dns_responder with timestamp capture
            start_dns_responder --timestamps "$ts_remote"

            # Run the load generator
            log_info "Running: $cmd"
            if ssh_client "$cmd" > "$raw_file" 2>&1; then
                log_info "Tool $tool completed successfully"
            else
                log_warn "Tool $tool exited with non-zero status at ${qps} QPS (trial $trial)"
            fi

            # Stop dns_responder
            stop_dns_responder

            # Fetch timestamp file
            fetch_dns_responder_timestamps "$ts_local"

            # Parse tool output
            metrics=$(parse_tool_output "$tool" "$raw_file")
            read -r achieved_qps lat_avg lat_min lat_max lat_stddev q_sent q_completed q_lost <<< "$metrics"

            # Compute interval QPS from dns_responder timestamps
            stats_1s="0 0 0"; stats_100ms="0 0 0"; stats_10ms="0 0 0"

            if [[ -f "$ts_local" ]]; then
                # 1-second intervals
                interval_file_1s="${INTERVAL_DIR}/${tool}_${qps}_trial${trial}_1s.csv"
                parse_dns_responder_timestamps "$ts_local" 1000 "$interval_file_1s"
                stats_1s=$(compute_interval_stats "$interval_file_1s" "$qps")

                # 100ms intervals
                interval_file_100ms="${INTERVAL_DIR}/${tool}_${qps}_trial${trial}_100ms.csv"
                parse_dns_responder_timestamps "$ts_local" 100 "$interval_file_100ms"
                stats_100ms=$(compute_interval_stats "$interval_file_100ms" "$qps")

                # 10ms intervals
                interval_file_10ms="${INTERVAL_DIR}/${tool}_${qps}_trial${trial}_10ms.csv"
                parse_dns_responder_timestamps "$ts_local" 10 "$interval_file_10ms"
                stats_10ms=$(compute_interval_stats "$interval_file_10ms" "$qps")
            else
                log_warn "No timestamp file for $tool at ${qps} QPS (trial $trial)"
            fi

            read -r mean_1s stddev_1s maxdev_1s <<< "$stats_1s"
            read -r mean_100ms stddev_100ms maxdev_100ms <<< "$stats_100ms"
            read -r mean_10ms stddev_10ms maxdev_10ms <<< "$stats_10ms"

            # Append to trials CSV
            append_csv "$TRIALS_CSV" \
                "$tool" "$qps" "$trial" "$achieved_qps" \
                "$mean_1s" "$stddev_1s" "$maxdev_1s" \
                "$mean_100ms" "$stddev_100ms" "$maxdev_100ms" \
                "$mean_10ms" "$stddev_10ms" "$maxdev_10ms" \
                "raw/${tool}_${qps}_trial${trial}.txt" \
                "dns_responder_logs/${tool}_${qps}_trial${trial}.txt"

            log_info "Result: achieved=${achieved_qps} QPS, 1s_mean=${mean_1s}, 100ms_mean=${mean_100ms}, 10ms_mean=${mean_10ms}"

            # Pause between runs
            if (( PAUSE_BETWEEN_RUNS > 0 )); then
                sleep "$PAUSE_BETWEEN_RUNS"
            fi
        done

        qps=$(( qps + ACCURACY_STEP ))
    done
done

# ──────────────────────────────────────────────
# Aggregate across trials
# ──────────────────────────────────────────────
log_info "Aggregating results across trials..."

SUMMARY_CSV="${OUTPUT_DIR}/summary.csv"
init_csv "$SUMMARY_CSV" \
    "tool" "requested_qps" \
    "achieved_qps_mean" "achieved_qps_stddev" \
    "qps_1s_mean" "qps_1s_stddev" "qps_1s_max_dev" \
    "qps_100ms_mean" "qps_100ms_stddev" "qps_100ms_max_dev" \
    "qps_10ms_mean" "qps_10ms_stddev" "qps_10ms_max_dev"

# Use awk to aggregate the trials CSV
awk -F',' '
NR == 1 { next }  # skip header
{
    tool = $1; qps = $2
    key = tool "," qps

    achieved[key] += $4
    achieved_sq[key] += $4 * $4

    m1s[key] += $5;   s1s[key] += $6;   d1s[key] = ($7 > d1s[key]) ? $7 : d1s[key]
    m100[key] += $8;   s100[key] += $9;  d100[key] = ($10 > d100[key]) ? $10 : d100[key]
    m10[key] += $11;   s10[key] += $12;  d10[key] = ($13 > d10[key]) ? $13 : d10[key]

    count[key]++

    if (!(key in keys_order)) {
        keys_order[key] = ++nkeys
        keys[nkeys] = key
    }
}
END {
    for (i = 1; i <= nkeys; i++) {
        k = keys[i]
        n = count[k]
        if (n == 0) continue

        a_mean = achieved[k] / n
        a_var = (achieved_sq[k] / n) - (a_mean * a_mean)
        if (a_var < 0) a_var = 0
        a_sd = sqrt(a_var)

        printf "%s,%.2f,%.2f,%.2f,%.2f,%.2f,%.2f,%.2f,%.2f,%.2f,%.2f,%.2f\n",
            k, a_mean, a_sd,
            m1s[k]/n, s1s[k]/n, d1s[k],
            m100[k]/n, s100[k]/n, d100[k],
            m10[k]/n, s10[k]/n, d10[k]
    }
}' "$TRIALS_CSV" >> "$SUMMARY_CSV"

# Generate JSON
SUMMARY_JSON="${OUTPUT_DIR}/summary.json"
csv_to_ndjson "$SUMMARY_CSV" "$SUMMARY_JSON"

TRIALS_JSON="${OUTPUT_DIR}/trials.json"
csv_to_ndjson "$TRIALS_CSV" "$TRIALS_JSON"

log_info "=== QPS Accuracy Evaluation Complete ==="
log_info "Results: $OUTPUT_DIR"
log_info "Trials CSV: $TRIALS_CSV"
log_info "Summary CSV: $SUMMARY_CSV"
log_info "Summary JSON: $SUMMARY_JSON"
