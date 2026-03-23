#!/usr/bin/env bash
# lib/parsers/parser_dnsperf.sh — Parser for dnsperf and dnsperf-workbench output
#
# Expected output format (relevant lines):
#   Queries sent:         3018807
#   Queries completed:    3018807 (100.00%)
#   Queries lost:         0 (0.00%)
#
#   Response codes:       NOERROR 3018807 (100.00%)
#   Average packet size:  request 47, response 63
#   Run time (s):         10.000390
#   Queries per second:   301868.927112
#
#   Average Latency (s):  0.000180 (min 0.000026, max 0.014830)
#   Latency StdDev (s):   0.000513

# parse_dnsperf RAW_FILE
# Extracts metrics from dnsperf/dnsperf-workbench output.
# Prints: achieved_qps latency_avg_ms latency_min_ms latency_max_ms latency_stddev_ms queries_sent queries_completed queries_lost
parse_dnsperf() {
    local file="$1"

    if [[ ! -f "$file" ]]; then
        echo "0 0 0 0 0 0 0 0"
        return 1
    fi

    local qps queries_sent queries_completed queries_lost
    local lat_avg lat_min lat_max lat_stddev

    qps=$(extract_number "Queries per second:" "$file")
    queries_sent=$(extract_number "Queries sent:" "$file")
    queries_completed=$(extract_number "Queries completed:" "$file")
    queries_lost=$(extract_number "Queries lost:" "$file")

    # Average Latency (s):  0.000180 (min 0.000026, max 0.014830)
    local lat_line
    lat_line=$(grep -E "Average Latency" "$file" 2>/dev/null | head -1)
    if [[ -n "$lat_line" ]]; then
        lat_avg=$(echo "$lat_line" | grep -oE 'Average Latency \(s\):\s+[0-9.]+' | grep -oE '[0-9]+\.?[0-9]*' | tail -1)
        lat_min=$(echo "$lat_line" | grep -oE 'min [0-9.]+' | grep -oE '[0-9.]+')
        lat_max=$(echo "$lat_line" | grep -oE 'max [0-9.]+' | grep -oE '[0-9.]+')
    fi

    lat_stddev=$(extract_number "Latency StdDev" "$file")

    # Convert seconds to milliseconds
    lat_avg_ms=$(awk "BEGIN { printf \"%.4f\", ${lat_avg:-0} * 1000 }")
    lat_min_ms=$(awk "BEGIN { printf \"%.4f\", ${lat_min:-0} * 1000 }")
    lat_max_ms=$(awk "BEGIN { printf \"%.4f\", ${lat_max:-0} * 1000 }")
    lat_stddev_ms=$(awk "BEGIN { printf \"%.4f\", ${lat_stddev:-0} * 1000 }")

    echo "${qps:-0} ${lat_avg_ms} ${lat_min_ms} ${lat_max_ms} ${lat_stddev_ms} ${queries_sent:-0} ${queries_completed:-0} ${queries_lost:-0}"
}
