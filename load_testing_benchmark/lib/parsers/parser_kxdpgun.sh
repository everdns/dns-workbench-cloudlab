#!/usr/bin/env bash
# lib/parsers/parser_kxdpgun.sh — Parser for kxdpgun output
#
# Expected output format (relevant lines):
#   total queries:     400040 (100010 pps)
#   total replies:     400033 (100008 pps) (99%)
#   average DNS reply size: 63 B
#   average Ethernet reply rate: 84006930 bps (84.01 Mbps)
#   responded NOERROR:   400026
#   responded YXRRSET:   7
#   duration: 4 s

# parse_kxdpgun RAW_FILE
# Extracts metrics from kxdpgun output.
# Prints: achieved_qps latency_avg_ms latency_min_ms latency_max_ms latency_stddev_ms queries_sent queries_completed queries_lost
parse_kxdpgun() {
    local file="$1"

    if [[ ! -f "$file" ]]; then
        echo "0 0 0 0 0 0 0 0"
        return 1
    fi

    local queries_sent queries_replied qps duration

    # "total queries:     400040 (100010 pps)"
    queries_sent=$(grep -E "total queries:" "$file" 2>/dev/null | head -1 | \
        grep -oE '[0-9]+' | head -1)

    # "total replies:     400033 (100008 pps) (99%)"
    queries_replied=$(grep -E "total replies:" "$file" 2>/dev/null | head -1 | \
        grep -oE '[0-9]+' | head -1)

    # Extract QPS from the pps value on the total replies line
    qps=$(grep -E "total replies:" "$file" 2>/dev/null | head -1 | \
        grep -oE '\([0-9]+ pps\)' | grep -oE '[0-9]+')

    # Compute queries lost
    local queries_lost
    queries_lost=$(awk "BEGIN { printf \"%d\", ${queries_sent:-0} - ${queries_replied:-0} }")

    # kxdpgun does not report latency
    echo "${qps:-0} 0 0 0 0 ${queries_sent:-0} ${queries_replied:-0} ${queries_lost}"
}
