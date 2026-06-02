#!/bin/bash

OUTPUT=~/dns_configs.md

> "$OUTPUT"

REPO_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

# Read a flat "key: value" field from an info.yaml file.
read_info() {
    local file="$1" key="$2"
    sed -n "s/^${key}:[[:space:]]*//p" "$file" | head -n 1
}

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
declare -A seen_paths

# Discover every software's info.yaml across both trees.
for info in "$REPO_DIR"/ns_software/*/info.yaml "$REPO_DIR"/resolver_software/*/info.yaml; do
    [ -f "$info" ] || continue

    config_dir=$(read_info "$info" software_dir)
    plotting_name=$(read_info "$info" plotting_name)
    [ -n "$plotting_name" ] || plotting_name=$(basename "$(dirname "$info")")

    if [ -z "$config_dir" ] || [ ! -d "$config_dir" ]; then
        continue
    fi

    # Gather files not already emitted (shared dirs like /etc/bind appear under
    # more than one software). Skip the section entirely if nothing is new.
    new_files=()
    while IFS= read -r filepath; do
        [ -n "${seen_paths[$filepath]:-}" ] && continue
        seen_paths[$filepath]=1
        new_files+=("$filepath")
    done < <(find "$config_dir" -type f | sort)

    [ ${#new_files[@]} -eq 0 ] && continue

    found_any=true
    echo "# $plotting_name" >> "$OUTPUT"
    echo "" >> "$OUTPUT"

    for filepath in "${new_files[@]}"; do
        collect_file "$filepath"
    done

    echo "---" >> "$OUTPUT"
    echo "" >> "$OUTPUT"
done

if [ "$found_any" = false ]; then
    echo "No DNS software configuration directories found." >> "$OUTPUT"
fi

echo "Configs collected to $OUTPUT"
