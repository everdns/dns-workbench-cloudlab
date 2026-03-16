#!/bin/bash

OUTPUT=~/dns_configs.md

> "$OUTPUT"

# Software name -> config directory mapping
declare -A SOFTWARE_DIRS
SOFTWARE_DIRS=(
    ["BIND"]="/etc/bind"
    ["Knot DNS"]="/etc/knot"
    ["Knot Resolver"]="/etc/knot-resolver"
    ["PowerDNS"]="/etc/powerdns"
    ["NSD"]="/etc/nsd"
    ["Unbound"]="/usr/local/etc/unbound"
)

# Order for consistent output
SOFTWARE_ORDER=("BIND" "Knot DNS" "Knot Resolver" "PowerDNS" "NSD" "Unbound")

is_zone_file() {
    local filename
    filename=$(basename "$1")
    case "$filename" in
        db.*) return 0 ;;
        *) return 1 ;;
    esac
}

collect_file() {
    local filepath="$1"
    echo "## $filepath" >> "$OUTPUT"
    echo "" >> "$OUTPUT"
    if is_zone_file "$filepath"; then
        echo "*Zone file — showing first 15 lines only*" >> "$OUTPUT"
        echo '```' >> "$OUTPUT"
        head -n 15 "$filepath" >> "$OUTPUT"
        echo '```' >> "$OUTPUT"
    else
        echo '```' >> "$OUTPUT"
        cat "$filepath" >> "$OUTPUT"
        echo '```' >> "$OUTPUT"
    fi
    echo "" >> "$OUTPUT"
}

found_any=false

for software in "${SOFTWARE_ORDER[@]}"; do
    config_dir="${SOFTWARE_DIRS[$software]}"

    if [ ! -d "$config_dir" ]; then
        continue
    fi

    found_any=true
    echo "# $software" >> "$OUTPUT"
    echo "" >> "$OUTPUT"

    # Find all regular files in the config directory and subdirectories
    while IFS= read -r filepath; do
        collect_file "$filepath"
    done < <(find "$config_dir" -type f | sort)

    echo "---" >> "$OUTPUT"
    echo "" >> "$OUTPUT"
done

if [ "$found_any" = false ]; then
    echo "No DNS software configuration directories found." >> "$OUTPUT"
fi

echo "Configs collected to $OUTPUT"
