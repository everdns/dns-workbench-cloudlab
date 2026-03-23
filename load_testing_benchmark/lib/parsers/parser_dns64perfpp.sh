#!/usr/bin/env bash
# lib/parsers/parser_dns64perfpp.sh — Parser for dns64perf++ and dns64perfpp-workbench output
#
# Expected output format (relevant lines):
#   Sent:       1000000
#   Received:   999950
#   Lost:       50
#   Rate:       99995.00 qps
#   Avg Latency: 0.42 ms
#   Min Latency: 0.10 ms
#   Max Latency: 50.00 ms

# parse_dns64perfpp RAW_FILE
# Extracts metrics from dns64perf++ / dns64perfpp-workbench output.
# Prints: achieved_qps latency_avg_ms latency_min_ms latency_max_ms latency_stddev_ms queries_sent queries_completed queries_lost
parse_dns64perfpp() {
    local file="$1"

    if [[ ! -f "$file" ]]; then
        echo "0 0 0 0 0 0 0 0"
        return 1
    fi

    local qps sent received lost
    local lat_avg lat_min lat_max

    qps=$(extract_number "Rate:" "$file")
    sent=$(extract_number "Sent:" "$file")
    received=$(extract_number "Received:" "$file")
    lost=$(extract_number "Lost:" "$file")

    lat_avg=$(extract_number "Avg Latency:" "$file")
    lat_min=$(extract_number "Min Latency:" "$file")
    lat_max=$(extract_number "Max Latency:" "$file")

    # dns64perf++ does not report stddev
    local lat_stddev=""

    echo "${qps:-0} ${lat_avg:-0} ${lat_min:-0} ${lat_max:-0} ${lat_stddev:-} ${sent:-0} ${received:-0} ${lost:-0}"
}
