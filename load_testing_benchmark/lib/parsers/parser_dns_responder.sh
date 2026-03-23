#!/usr/bin/env bash
# lib/parsers/parser_dns_responder.sh — Timestamp-based interval QPS analysis
#
# Input: dns_responder timestamp file (one nanosecond timestamp per line, sorted)
# Output: per-interval QPS data and statistical summaries

# parse_dns_responder_timestamps TIMESTAMP_FILE INTERVAL_MS OUTPUT_FILE
# Reads timestamps (nanoseconds), buckets them into intervals of INTERVAL_MS,
# and writes per-interval QPS to OUTPUT_FILE.
# OUTPUT_FILE format: interval_start_ns,interval_end_ns,query_count,qps
parse_dns_responder_timestamps() {
    local ts_file="$1" interval_ms="$2" output_file="$3"
    local interval_ns=$(( interval_ms * 1000000 ))

    echo "interval_start_ns,interval_end_ns,query_count,qps" > "$output_file"

    awk -v interval_ns="$interval_ns" -v interval_s="$(awk "BEGIN { printf \"%.6f\", $interval_ms / 1000 }")" '
    /^#/ { next }
    /^[[:space:]]*$/ { next }
    {
        ts = $1 + 0
        bucket = int(ts / interval_ns)
        counts[bucket]++
        if (NR == 1 || bucket < min_bucket) min_bucket = bucket
        if (bucket > max_bucket) max_bucket = bucket
    }
    END {
        for (b = min_bucket; b <= max_bucket; b++) {
            start_ns = b * interval_ns
            end_ns = start_ns + interval_ns
            count = (b in counts) ? counts[b] : 0
            qps = count / interval_s
            printf "%d,%d,%d,%.2f\n", start_ns, end_ns, count, qps
        }
    }' "$ts_file" >> "$output_file"
}

# compute_interval_stats INTERVAL_CSV_FILE TARGET_QPS
# From a per-interval QPS CSV file, computes mean, stddev, max_deviation from target.
# Prints: mean stddev max_deviation
compute_interval_stats() {
    local file="$1" target_qps="$2"

    awk -F',' -v target="$target_qps" '
    NR == 1 { next }  # skip header
    {
        qps = $4 + 0
        sum += qps
        sumsq += qps * qps
        n++
        dev = qps - target
        if (dev < 0) dev = -dev
        if (dev > max_dev) max_dev = dev
    }
    END {
        if (n == 0) {
            print "0 0 0"
            exit
        }
        mean = sum / n
        variance = (sumsq / n) - (mean * mean)
        if (variance < 0) variance = 0
        stddev = sqrt(variance)
        printf "%.2f %.2f %.2f\n", mean, stddev, max_dev
    }' "$file"
}

# compute_total_from_timestamps TIMESTAMP_FILE RUNTIME
# Computes total query count and effective QPS from a timestamp file.
# Prints: total_queries achieved_qps
compute_total_from_timestamps() {
    local ts_file="$1" runtime="$2"

    awk -v runtime="$runtime" '
    /^#/ { next }
    /^[[:space:]]*$/ { next }
    { count++ }
    END {
        if (runtime > 0) {
            qps = count / runtime
        } else {
            qps = 0
        }
        printf "%d %.2f\n", count, qps
    }' "$ts_file"
}
