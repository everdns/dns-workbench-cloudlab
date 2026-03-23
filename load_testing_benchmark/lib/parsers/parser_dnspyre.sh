#!/usr/bin/env bash
# lib/parsers/parser_dnspyre.sh — Parser for dnspyre and dnspyre-dnsworkbench output
#
# Expected output format (relevant lines from dnspyre text output):
#   Total requests:         979846
#   DNS success responses:  979846
#
#   Questions per second:   97871.8
#   DNS timings, 979846 datapoints
#            min:           43.01µs
#            mean:          158.9µs
#            [+/-sd]:       176.05µs
#            max:           15.2ms
#            p99:           917.5µs
#            p95:           327.68µs
#            p90:           229.38µs
#            p75:           163.84µs
#            p50:           122.88µs

# _dnspyre_to_ms VALUE_WITH_UNIT
# Converts a dnspyre latency value (e.g. "43.01µs", "15.2ms") to milliseconds.
_dnspyre_to_ms() {
    local raw="$1"
    if [[ -z "$raw" ]]; then
        echo "0"
        return
    fi
    local num unit
    num=$(echo "$raw" | grep -oE '[0-9]+\.?[0-9]*')
    unit=$(echo "$raw" | grep -oE '[a-zµ]+$')
    case "$unit" in
        us|µs) awk "BEGIN { printf \"%.4f\", $num / 1000 }" ;;
        ms)    awk "BEGIN { printf \"%.4f\", $num }" ;;
        s)     awk "BEGIN { printf \"%.4f\", $num * 1000 }" ;;
        *)     echo "${num:-0}" ;;
    esac
}

# parse_dnspyre RAW_FILE
# Extracts metrics from dnspyre/dnspyre-dnsworkbench output.
# Prints: achieved_qps latency_avg_ms latency_min_ms latency_max_ms latency_stddev_ms queries_sent queries_completed queries_lost
parse_dnspyre() {
    local file="$1"

    if [[ ! -f "$file" ]]; then
        echo "0 0 0 0 0 0 0 0"
        return 1
    fi

    local qps total_requests dns_success
    local lat_min_raw lat_mean_raw lat_sd_raw lat_max_raw

    qps=$(extract_number "Questions per second:" "$file")

    total_requests=$(extract_number "Total requests:" "$file")
    dns_success=$(extract_number "DNS success responses:" "$file")

    # Extract latency values with their units
    lat_min_raw=$(grep -E '^\s+min:' "$file" 2>/dev/null | head -1 | \
        grep -oE '[0-9]+\.?[0-9]*[a-zµ]+' | head -1)
    lat_mean_raw=$(grep -E '^\s+mean:' "$file" 2>/dev/null | head -1 | \
        grep -oE '[0-9]+\.?[0-9]*[a-zµ]+' | head -1)
    lat_sd_raw=$(grep -E '\[\+/-sd\]:' "$file" 2>/dev/null | head -1 | \
        grep -oE '[0-9]+\.?[0-9]*[a-zµ]+' | head -1)
    lat_max_raw=$(grep -E '^\s+max:' "$file" 2>/dev/null | head -1 | \
        grep -oE '[0-9]+\.?[0-9]*[a-zµ]+' | head -1)

    # Convert all latencies to ms
    local lat_min lat_mean lat_sd lat_max
    lat_min=$(_dnspyre_to_ms "$lat_min_raw")
    lat_mean=$(_dnspyre_to_ms "$lat_mean_raw")
    lat_sd=$(_dnspyre_to_ms "$lat_sd_raw")
    lat_max=$(_dnspyre_to_ms "$lat_max_raw")

    local queries_completed="${dns_success:-0}"
    local queries_lost
    queries_lost=$(awk "BEGIN { printf \"%d\", ${total_requests:-0} - ${dns_success:-0} }")

    echo "${qps:-0} ${lat_mean:-0} ${lat_min:-0} ${lat_max:-0} ${lat_sd:-0} ${total_requests:-0} ${queries_completed} ${queries_lost}"
}
