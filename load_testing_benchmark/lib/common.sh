#!/usr/bin/env bash
# lib/common.sh — Shared utility functions for DNS load testing benchmark

# ──────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────

log_info()  { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] [INFO]  $*" >&2; }
log_warn()  { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] [WARN]  $*" >&2; }
log_error() { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] [ERROR] $*" >&2; }

die() {
    log_error "$@"
    exit 1
}

# ──────────────────────────────────────────────
# Filesystem helpers
# ──────────────────────────────────────────────

ensure_dir() {
    local dir="$1"
    mkdir -p "$dir" || die "Failed to create directory: $dir"
}

# ──────────────────────────────────────────────
# Validation helpers
# ──────────────────────────────────────────────

# require_vars VAR_NAME1 VAR_NAME2 ...
# Dies if any named variable is empty or unset.
require_vars() {
    local var
    for var in "$@"; do
        if [[ -z "${!var:-}" ]]; then
            die "Required variable $var is not set"
        fi
    done
}

# require_commands CMD1 CMD2 ...
# Dies if any command is not found locally.
require_commands() {
    local cmd
    for cmd in "$@"; do
        if ! command -v "$cmd" &>/dev/null; then
            die "Required command not found: $cmd"
        fi
    done
}

# ──────────────────────────────────────────────
# Arithmetic helpers
# ──────────────────────────────────────────────

# round_up_to_multiple VALUE MULTIPLE
# Rounds VALUE up to the nearest multiple of MULTIPLE.
round_up_to_multiple() {
    local value=$1 multiple=$2
    if (( multiple <= 0 )); then
        echo "$value"
        return
    fi
    local remainder=$(( value % multiple ))
    if (( remainder == 0 )); then
        echo "$value"
    else
        echo $(( value + multiple - remainder ))
    fi
}

# compute_burst_size QPS DELAY_NS THREADS
# BURST_SIZE = (QPS * DELAY_NS) / (1000000000 * THREADS), minimum 1
compute_burst_size() {
    local qps=$1 delay_ns=$2 threads=$3
    local burst_size=$(( (qps * delay_ns) / (1000000000 * threads) ))
    if (( burst_size < 1 )); then
        burst_size=1
    fi
    echo "$burst_size"
}

# compute_number_of_requests QPS RUNTIME BURST_SIZE THREADS
# Returns QPS * RUNTIME rounded up to nearest multiple of (BURST_SIZE * THREADS).
compute_number_of_requests() {
    local qps=$1 runtime=$2 burst_size=$3 threads=$4
    local total=$(( qps * runtime ))
    local multiple=$(( burst_size * threads ))
    round_up_to_multiple "$total" "$multiple"
}

# compute_max_outstanding THREADS
# Returns 65536 * THREADS (for dnsperf).
compute_max_outstanding() {
    local threads=$1
    echo $(( 65536 * threads ))
}

# ──────────────────────────────────────────────
# Network helpers
# ──────────────────────────────────────────────

# wait_for_port HOST PORT TIMEOUT_SECONDS [SSH_FUNC]
# Polls until HOST:PORT is listening. If SSH_FUNC is provided (e.g., "ssh_server"),
# the check is performed via that SSH function; otherwise uses local nc.
wait_for_port() {
    local host="$1" port="$2" timeout_s="${3:-30}" ssh_func="${4:-}"
    local elapsed=0

    while (( elapsed < timeout_s )); do
        if [[ -n "$ssh_func" ]]; then
            if $ssh_func "ss -tln 2>/dev/null | grep -q ':${port} '" 2>/dev/null; then
                return 0
            fi
        else
            if nc -z "$host" "$port" 2>/dev/null; then
                return 0
            fi
        fi
        sleep 1
        (( elapsed++ ))
    done
    return 1
}

# ──────────────────────────────────────────────
# CSV helpers
# ──────────────────────────────────────────────

# csv_escape VALUE
# Wraps value in double quotes if it contains commas, quotes, or newlines.
csv_escape() {
    local val="$1"
    if [[ "$val" == *[,\"$'\n']* ]]; then
        val="${val//\"/\"\"}"
        echo "\"${val}\""
    else
        echo "$val"
    fi
}

# append_csv FILE VALUE1 VALUE2 ...
# Appends a comma-separated row to FILE.
append_csv() {
    local file="$1"
    shift
    local row=""
    local first=1
    for val in "$@"; do
        if (( first )); then
            first=0
        else
            row+=","
        fi
        row+="$(csv_escape "$val")"
    done
    echo "$row" >> "$file"
}

# init_csv FILE HEADER1 HEADER2 ...
# Writes the header row if the file does not exist or is empty.
init_csv() {
    local file="$1"
    shift
    if [[ ! -s "$file" ]]; then
        append_csv "$file" "$@"
    fi
}

# ──────────────────────────────────────────────
# JSON helpers (NDJSON — one JSON object per line)
# ──────────────────────────────────────────────

# csv_to_ndjson CSV_FILE JSON_FILE
# Converts a CSV file (with header) to NDJSON.
csv_to_ndjson() {
    local csv_file="$1" json_file="$2"
    awk -F',' '
    NR == 1 {
        for (i = 1; i <= NF; i++) {
            gsub(/^"|"$/, "", $i)
            headers[i] = $i
        }
        ncols = NF
        next
    }
    {
        printf "{"
        for (i = 1; i <= ncols; i++) {
            val = $i
            gsub(/^"|"$/, "", val)
            if (i > 1) printf ","
            # Try to detect numeric values
            if (val ~ /^-?[0-9]+\.?[0-9]*$/ && val != "") {
                printf "\"%s\":%s", headers[i], val
            } else {
                gsub(/"/, "\\\"", val)
                printf "\"%s\":\"%s\"", headers[i], val
            }
        }
        print "}"
    }' "$csv_file" > "$json_file"
}

# ──────────────────────────────────────────────
# Timestamp / misc
# ──────────────────────────────────────────────

timestamp() {
    date -u '+%Y-%m-%dT%H:%M:%SZ'
}

# Generate a unique run ID based on timestamp
generate_run_id() {
    date -u '+%Y%m%d_%H%M%S'
}

# shuffle_array ELEMENT1 ELEMENT2 ...
# Prints elements in random order, one per line.
shuffle_array() {
    local arr=("$@")
    local i n temp
    n=${#arr[@]}
    for (( i = n - 1; i > 0; i-- )); do
        local j=$(( RANDOM % (i + 1) ))
        temp="${arr[$i]}"
        arr[$i]="${arr[$j]}"
        arr[$j]="$temp"
    done
    printf '%s\n' "${arr[@]}"
}
