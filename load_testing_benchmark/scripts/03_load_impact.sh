#!/usr/bin/env bash
# scripts/03_load_impact.sh — Load Generator Impact Analysis
#
# Evaluates how load generator choice affects DNS benchmarking results
# by running all load generators against each DNS implementation and
# comparing latency, answer rate, and throughput.
#
# Usage:
#   ./scripts/03_load_impact.sh [OPTIONS]
#
# Options override config.sh defaults:
#   --client=HOST          Client host (SSH target)
#   --server=HOST          Server host (SSH target)
#   --resolver=IP          Resolver IP address
#   --tools="t1 t2 ..."    Space-separated list of tools to test
#   --dns-configs="d1 d2"  Space-separated list of DNS software configs
#   --min-qps=N            Minimum QPS (default: 100000)
#   --max-qps=N            Maximum QPS (default: 1000000)
#   --step=N               QPS step size (default: 100000)
#   --trials=N             Trials per (software, tool, QPS) (default: 10)
#   --runtime=N            Seconds per test run (default: 10)
#   --threads=N            Thread count (default: 20)
#   --results-dir=PATH     Output directory

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
        --dns-configs=*)    DNS_CONFIGS="${1#*=}" ;;
        --min-qps=*)        IMPACT_MIN_QPS="${1#*=}" ;;
        --max-qps=*)        IMPACT_MAX_QPS="${1#*=}" ;;
        --step=*)           IMPACT_QPS_STEP="${1#*=}" ;;
        --trials=*)         IMPACT_TRIALS="${1#*=}" ;;
        --runtime=*)        RUNTIME="${1#*=}" ;;
        --threads=*)        THREADS="${1#*=}" ;;
        --results-dir=*)    RESULTS_DIR="${1#*=}" ;;
        --interface=*)      DNS_RESPONDER_INTERFACE="${1#*=}" ;;
        --kxdpgun-iface=*)  KXDPGUN_INTERFACE="${1#*=}" ;;
        --pause=*)          PAUSE_BETWEEN_RUNS="${1#*=}" ;;
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
log_info "=== DNS Load Testing Benchmark: Load Generator Impact Analysis ==="
log_info "DNS configs: $DNS_CONFIGS"
log_info "QPS range: $IMPACT_MIN_QPS-$IMPACT_MAX_QPS (step $IMPACT_QPS_STEP), trials=$IMPACT_TRIALS"
log_info "Tools: $ENABLED_TOOLS"

validate_config
validate_ssh
validate_input_files "$ENABLED_TOOLS"
validate_tools "$ENABLED_TOOLS"

# ──────────────────────────────────────────────
# Setup output directories
# ──────────────────────────────────────────────
RUN_ID=$(generate_run_id)
OUTPUT_DIR="${RESULTS_DIR}/03_load_impact/${RUN_ID}"
RAW_DIR="${OUTPUT_DIR}/raw"
ensure_dir "$RAW_DIR"

TRIALS_CSV="${OUTPUT_DIR}/trials.csv"
init_csv "$TRIALS_CSV" \
    "dns_software" "tool" "requested_qps" "trial" \
    "achieved_qps" "latency_avg_ms" "latency_min_ms" "latency_max_ms" "latency_stddev_ms" \
    "queries_sent" "queries_completed" "queries_lost" "answer_rate" \
    "raw_file"

# Upload input files to client
upload_input_files "$ENABLED_TOOLS"

# ──────────────────────────────────────────────
# Main test loop
# ──────────────────────────────────────────────
for dns_sw in $DNS_CONFIGS; do
    log_info "════════════════════════════════════════════"
    log_info "DNS Software: $dns_sw"
    log_info "════════════════════════════════════════════"

    # Start DNS server
    start_dns_server "$dns_sw"

    qps=$IMPACT_MIN_QPS
    while (( qps <= IMPACT_MAX_QPS )); do
        for tool in $ENABLED_TOOLS; do
            for (( trial = 1; trial <= IMPACT_TRIALS; trial++ )); do
                log_info "─── $dns_sw / $tool / ${qps} QPS / trial $trial ───"

                cmd=$(build_tool_cmd "$tool" "$qps")
                raw_file="${RAW_DIR}/${dns_sw}_${tool}_${qps}_trial${trial}.txt"

                # Run the load generator
                log_info "Running: $cmd"
                if ssh_client "$cmd" > "$raw_file" 2>&1; then
                    log_info "Tool $tool completed successfully"
                else
                    log_warn "Tool $tool exited with non-zero status ($dns_sw, ${qps} QPS, trial $trial)"
                fi

                # Parse results
                metrics=$(parse_tool_output "$tool" "$raw_file")
                read -r achieved_qps lat_avg lat_min lat_max lat_stddev q_sent q_completed q_lost <<< "$metrics"

                answer_rate=$(safe_divide "$q_completed" "$q_sent" 6)

                # Append to trials CSV
                append_csv "$TRIALS_CSV" \
                    "$dns_sw" "$tool" "$qps" "$trial" \
                    "$achieved_qps" "$lat_avg" "$lat_min" "$lat_max" "$lat_stddev" \
                    "$q_sent" "$q_completed" "$q_lost" "$answer_rate" \
                    "raw/${dns_sw}_${tool}_${qps}_trial${trial}.txt"

                log_info "Result: achieved=${achieved_qps} QPS, latency_avg=${lat_avg}ms, answer_rate=${answer_rate}"

                # Pause between runs
                if (( PAUSE_BETWEEN_RUNS > 0 )); then
                    sleep "$PAUSE_BETWEEN_RUNS"
                fi
            done
        done

        qps=$(( qps + IMPACT_QPS_STEP ))
    done

    # Stop DNS server
    stop_dns_server "$dns_sw"

    # Cool-down between DNS software changes
    if (( PAUSE_BETWEEN_RUNS > 0 )); then
        log_info "Cool-down ${PAUSE_BETWEEN_RUNS}s before next DNS software..."
        sleep "$PAUSE_BETWEEN_RUNS"
    fi
done

# ──────────────────────────────────────────────
# Aggregate and compute summary
# ──────────────────────────────────────────────
log_info "Aggregating results..."

SUMMARY_CSV="${OUTPUT_DIR}/summary.csv"
init_csv "$SUMMARY_CSV" \
    "dns_software" "tool" "requested_qps" \
    "achieved_qps_mean" "achieved_qps_stddev" \
    "latency_avg_ms_mean" "latency_avg_ms_stddev" \
    "latency_max_ms_mean" \
    "queries_sent_mean" "queries_completed_mean" \
    "answer_rate_mean" "answer_rate_stddev"

# Aggregate trials
awk -F',' '
NR == 1 { next }
{
    sw = $1; tool = $2; qps = $3
    key = sw "," tool "," qps

    aq = $5 + 0
    la = $6 + 0
    lmax = $8 + 0
    qs = $10 + 0
    qc = $11 + 0
    ar = $13 + 0

    sum_aq[key] += aq; sq_aq[key] += aq * aq
    sum_la[key] += la
    sum_lmax[key] += lmax
    sum_qs[key] += qs
    sum_qc[key] += qc
    sum_ar[key] += ar; sq_ar[key] += ar * ar
    cnt[key]++

    if (!(key in order)) {
        order[key] = ++n
        keys[n] = key
    }
}
END {
    for (i = 1; i <= n; i++) {
        k = keys[i]
        c = cnt[k]
        if (c == 0) continue

        aq_m = sum_aq[k] / c
        aq_v = (sq_aq[k] / c) - (aq_m * aq_m)
        if (aq_v < 0) aq_v = 0

        ar_m = sum_ar[k] / c
        ar_v = (sq_ar[k] / c) - (ar_m * ar_m)
        if (ar_v < 0) ar_v = 0

        printf "%s,%.2f,%.2f,%.4f,%.4f,%.4f,%.0f,%.0f,%.6f,%.6f\n",
            k, aq_m, sqrt(aq_v),
            sum_la[k]/c, 0,
            sum_lmax[k]/c,
            sum_qs[k]/c, sum_qc[k]/c,
            ar_m, sqrt(ar_v)
    }
}' "$TRIALS_CSV" >> "$SUMMARY_CSV"

# ──────────────────────────────────────────────
# Compute 99.99% answer rate threshold per (dns_software, tool)
# ──────────────────────────────────────────────
THRESHOLD_CSV="${OUTPUT_DIR}/p9999_threshold.csv"
init_csv "$THRESHOLD_CSV" "dns_software" "tool" "p9999_threshold_qps"

awk -F',' '
NR == 1 { next }
{
    sw = $1; tool = $2; qps = $3 + 0; ar = $11 + 0

    pair = sw "," tool

    # Track the highest QPS where answer_rate >= 0.9999
    if (ar >= 0.9999) {
        if (!(pair in best) || qps > best[pair]) {
            best[pair] = qps
        }
    }

    if (!(pair in order)) {
        order[pair] = ++n
        pairs[n] = pair
    }
}
END {
    for (i = 1; i <= n; i++) {
        p = pairs[i]
        threshold = (p in best) ? best[p] : 0
        printf "%s,%d\n", p, threshold
    }
}' "$SUMMARY_CSV" >> "$THRESHOLD_CSV"

# Generate JSON files
SUMMARY_JSON="${OUTPUT_DIR}/summary.json"
csv_to_ndjson "$SUMMARY_CSV" "$SUMMARY_JSON"

TRIALS_JSON="${OUTPUT_DIR}/trials.json"
csv_to_ndjson "$TRIALS_CSV" "$TRIALS_JSON"

THRESHOLD_JSON="${OUTPUT_DIR}/p9999_threshold.json"
csv_to_ndjson "$THRESHOLD_CSV" "$THRESHOLD_JSON"

log_info "=== Load Generator Impact Analysis Complete ==="
log_info "Results: $OUTPUT_DIR"
log_info "Trials CSV: $TRIALS_CSV"
log_info "Summary CSV: $SUMMARY_CSV"
log_info "Threshold CSV: $THRESHOLD_CSV"
log_info "JSON: $SUMMARY_JSON"
