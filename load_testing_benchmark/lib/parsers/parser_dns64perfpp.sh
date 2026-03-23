#!/usr/bin/env bash
# lib/parsers/parser_dns64perfpp.sh — Parser for dns64perf++ and dns64perfpp-workbench output
#
# Expected output format (relevant lines):
#   Sent queries: 1000000
#   Received answers: 999999 (100.00%)
#   Valid answers: 999999 (100.00%)
#   Average round-trip time: 0.21 ms
#   Standard deviation of the round-trip time: 0.12 ms

# parse_dns64perfpp RAW_FILE
# Extracts metrics from dns64perf++ / dns64perfpp-workbench output.
# Prints: achieved_qps latency_avg_ms latency_min_ms latency_max_ms latency_stddev_ms queries_sent queries_completed queries_lost
parse_dns64perfpp() {
    local file="$1"

    if [[ ! -f "$file" ]]; then
        echo "0 0 0 0 0 0 0 0"
        return 1
    fi

    local sent received lat_avg lat_stddev

    sent=$(extract_number "Sent queries:" "$file")
    received=$(extract_number "Received answers:" "$file")

    # Latencies are already in ms
    lat_avg=$(extract_number "Average round-trip time:" "$file")
    lat_stddev=$(extract_number "Standard deviation of the round-trip time:" "$file")

    # Compute queries lost
    local lost
    lost=$(awk "BEGIN { printf \"%d\", ${sent:-0} - ${received:-0} }")

    # dns64perf++ does not report QPS, min latency, or max latency
    echo "0 ${lat_avg:-0} 0 0 ${lat_stddev:-0} ${sent:-0} ${received:-0} ${lost}"
}
