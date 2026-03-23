#!/usr/bin/env bash
# lib/parsers/parser_dnspyre.sh — Parser for dnspyre and dnspyre-dnsworkbench output
#
# Expected output format (relevant lines from dnspyre text output):
#   Total requests:	 1000000
#   Total errors:	 50
#   DNS success codes:	 999950
#
#   Queries per second:	 99995.0
#
#   Latency:
#     min:     0.10ms
#     mean:    0.42ms
#     std:     1.20ms
#     max:     50.00ms
#     p99:     5.00ms
#     p99.9:   20.00ms
#     p99.99:  45.00ms

# parse_dnspyre RAW_FILE
# Extracts metrics from dnspyre/dnspyre-dnsworkbench output.
# Prints: achieved_qps latency_avg_ms latency_min_ms latency_max_ms latency_stddev_ms queries_sent queries_completed queries_lost
parse_dnspyre() {
    local file="$1"

    if [[ ! -f "$file" ]]; then
        echo "0 0 0 0 0 0 0 0"
        return 1
    fi

    local qps total_requests total_errors dns_success
    local lat_min lat_mean lat_std lat_max

    qps=$(extract_number "Queries per second" "$file")

    total_requests=$(grep -E "Total requests" "$file" 2>/dev/null | head -1 | \
        grep -oE '[0-9]+' | head -1)
    total_errors=$(grep -E "Total errors" "$file" 2>/dev/null | head -1 | \
        grep -oE '[0-9]+' | head -1)
    dns_success=$(grep -E "DNS success codes" "$file" 2>/dev/null | head -1 | \
        grep -oE '[0-9]+' | head -1)

    # Latencies are already in ms in dnspyre output
    lat_min=$(grep -E '^\s+min:' "$file" 2>/dev/null | head -1 | \
        grep -oE '[0-9]+\.?[0-9]*' | head -1)
    lat_mean=$(grep -E '^\s+mean:' "$file" 2>/dev/null | head -1 | \
        grep -oE '[0-9]+\.?[0-9]*' | head -1)
    lat_std=$(grep -E '^\s+std:' "$file" 2>/dev/null | head -1 | \
        grep -oE '[0-9]+\.?[0-9]*' | head -1)
    lat_max=$(grep -E '^\s+max:' "$file" 2>/dev/null | head -1 | \
        grep -oE '[0-9]+\.?[0-9]*' | head -1)

    local queries_completed="${dns_success:-0}"
    local queries_lost="${total_errors:-0}"

    echo "${qps:-0} ${lat_mean:-0} ${lat_min:-0} ${lat_max:-0} ${lat_std:-0} ${total_requests:-0} ${queries_completed} ${queries_lost}"
}
