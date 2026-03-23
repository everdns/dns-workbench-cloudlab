#!/usr/bin/env bash
# lib/parsers/parser_kxdpgun.sh — Parser for kxdpgun output
#
# Expected output format (relevant lines):
#   Queries sent:      1000000
#   Queries answered:  999950
#   Queries lost:      50
#
#   Run time:          10.000 s
#   Average QPS:       99995.00
#
#   Latency: average 0.420 ms, min 0.100 ms, max 50.000 ms

# parse_kxdpgun RAW_FILE
# Extracts metrics from kxdpgun output.
# Prints: achieved_qps latency_avg_ms latency_min_ms latency_max_ms latency_stddev_ms queries_sent queries_completed queries_lost
parse_kxdpgun() {
    local file="$1"

    if [[ ! -f "$file" ]]; then
        echo "0 0 0 0 0 0 0 0"
        return 1
    fi

    local qps queries_sent queries_answered queries_lost
    local lat_avg lat_min lat_max

    qps=$(extract_number "Average QPS:" "$file")
    queries_sent=$(extract_number "Queries sent:" "$file")
    queries_answered=$(extract_number "Queries answered:" "$file")
    queries_lost=$(extract_number "Queries lost:" "$file")

    # Latency line: "Latency: average 0.420 ms, min 0.100 ms, max 50.000 ms"
    local lat_line
    lat_line=$(grep -E "Latency:" "$file" 2>/dev/null | head -1)
    if [[ -n "$lat_line" ]]; then
        lat_avg=$(echo "$lat_line" | grep -oE 'average [0-9.]+' | grep -oE '[0-9.]+')
        lat_min=$(echo "$lat_line" | grep -oE 'min [0-9.]+' | grep -oE '[0-9.]+')
        lat_max=$(echo "$lat_line" | grep -oE 'max [0-9.]+' | grep -oE '[0-9.]+')
    fi

    # kxdpgun does not report stddev
    local lat_stddev=""

    echo "${qps:-0} ${lat_avg:-0} ${lat_min:-0} ${lat_max:-0} ${lat_stddev:-} ${queries_sent:-0} ${queries_answered:-0} ${queries_lost:-0}"
}
