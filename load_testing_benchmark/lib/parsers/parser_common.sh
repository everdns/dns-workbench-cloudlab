#!/usr/bin/env bash
# lib/parsers/parser_common.sh — Shared parser utilities

# extract_number PATTERN FILE
# Searches FILE for a line matching PATTERN (grep -E), then extracts the first
# number (integer or decimal) found on that line.
# Returns empty string if no match.
extract_number() {
    local pattern="$1" file="$2"
    grep -E "$pattern" "$file" 2>/dev/null | head -1 | \
        grep -oE '[0-9]+\.?[0-9]*' | head -1
}

# extract_field PATTERN FIELD_NUM FILE
# Searches FILE for a line matching PATTERN, splits by whitespace,
# and returns the FIELD_NUM-th field (1-based).
extract_field() {
    local pattern="$1" field_num="$2" file="$3"
    grep -E "$pattern" "$file" 2>/dev/null | head -1 | \
        awk "{print \$$field_num}"
}

# safe_divide NUMERATOR DENOMINATOR [SCALE]
# Returns NUMERATOR / DENOMINATOR using awk. Returns 0 if DENOMINATOR is 0.
safe_divide() {
    local num="${1:-0}" den="${2:-0}" scale="${3:-6}"
    awk "BEGIN { if ($den == 0) print 0; else printf \"%.${scale}f\", $num / $den }"
}

# safe_float VALUE
# Returns VALUE if it looks numeric, otherwise returns empty string.
safe_float() {
    local val="$1"
    if [[ "$val" =~ ^-?[0-9]+\.?[0-9]*$ ]]; then
        echo "$val"
    else
        echo ""
    fi
}
