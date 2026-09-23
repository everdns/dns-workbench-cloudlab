#!/bin/sh
# Shared helpers for the top-level orchestration scripts.
#
# Source this file from a dispatcher script:
#     . "$(dirname "$0")/script_lib.sh"
#
# It provides software discovery over the ns_software/ tree so the top-level
# scripts don't have to hard-code the list of software.

# Repo root: the directory this library lives in. Works whether the calling
# script is invoked as ./foo.sh or by an absolute path (e.g. /local/repository).
REPO_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

# resolve_target <arg> <script>
# Splits <arg> of the form ns_<sw> (sw = the text after the "ns_" prefix) and,
# if ns_software/<sw>/<script> exists and is executable, echoes its path and
# returns 0. Otherwise returns non-zero and echoes nothing.
resolve_target() {
    arg=$1
    script=$2

    case "$arg" in
        ns_*) ;;
        *) return 1 ;;
    esac
    sw=${arg#ns_}

    if [ -z "$sw" ]; then
        return 1
    fi

    target="$REPO_DIR/ns_software/${sw}/${script}"
    if [ -x "$target" ]; then
        echo "$target"
        return 0
    fi
    return 1
}

# list_available <script>
# Prints, one per line, the ns_<sw> identifier for every software directory
# under ns_software/ that contains an executable <script>.
list_available() {
    script=$1
    for dir in "$REPO_DIR/ns_software"/*/; do
        [ -d "$dir" ] || continue
        [ -x "${dir}${script}" ] || continue
        sw=$(basename "$dir")
        echo "ns_${sw}"
    done
}

# run_all <script> [args...]
# Runs <script> for every software under ns_software/ that has an executable copy of it.
run_all() {
    script=$1
    shift
    for dir in "$REPO_DIR/ns_software"/*/; do
        [ -d "$dir" ] || continue
        [ -x "${dir}${script}" ] || continue
        "${dir}${script}" "$@"
    done
}
