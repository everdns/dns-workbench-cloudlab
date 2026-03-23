#!/usr/bin/env bash
# lib/parsers/parser_dnsperf.sh — Parser for dnsperf and dnsperf-workbench output
#
# Expected output format (relevant lines):
#   Queries sent:         1000000
#   Queries completed:    999950 (99.99%)
#   Queries lost:         50 (0.01%)
#
#   Response codes:       NOERROR 999950 (100.00%)
#
#   Queries per second:   99995.000000
#
#   Average Latency (s):  0.000420
#   Minimum Latency (s):  0.000100
#   Maximum Latency (s):  0.050000
#   Latency StdDev (s):   0.001200

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

    # Latencies are in seconds in dnsperf output; convert to ms
    lat_avg=$(extract_number "Average Latency" "$file")
    lat_min=$(extract_number "Minimum Latency" "$file")
    lat_max=$(extract_number "Maximum Latency" "$file")
    lat_stddev=$(extract_number "Latency StdDev" "$file")

    # Convert seconds to milliseconds
    lat_avg_ms=$(awk "BEGIN { printf \"%.4f\", ${lat_avg:-0} * 1000 }")
    lat_min_ms=$(awk "BEGIN { printf \"%.4f\", ${lat_min:-0} * 1000 }")
    lat_max_ms=$(awk "BEGIN { printf \"%.4f\", ${lat_max:-0} * 1000 }")
    lat_stddev_ms=$(awk "BEGIN { printf \"%.4f\", ${lat_stddev:-0} * 1000 }")

    echo "${qps:-0} ${lat_avg_ms} ${lat_min_ms} ${lat_max_ms} ${lat_stddev_ms} ${queries_sent:-0} ${queries_completed:-0} ${queries_lost:-0}"
}
