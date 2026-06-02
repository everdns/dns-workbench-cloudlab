#!/bin/sh
# Shared helpers for the top-level orchestration scripts.
#
# Source this file from a dispatcher script:
#     . "$(dirname "$0")/script_lib.sh"
#
# It provides software discovery over the ns_software/ and resolver_software/
# trees so the top-level scripts don't have to hard-code the list of software.

# Repo root: the directory this library lives in. Works whether the calling
# script is invoked as ./foo.sh or by an absolute path (e.g. /local/repository).
REPO_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

# resolve_target <arg> <script>
# Splits <arg> of the form <role>_<sw> (role = text before the first '_',
# must be "ns" or "resolver"; sw = the rest) and, if
# <role>_software/<sw>/<script> exists and is executable, echoes its path and
# returns 0. Otherwise returns non-zero and echoes nothing.
resolve_target() {
    arg=$1
    script=$2

    role=${arg%%_*}
    sw=${arg#*_}

    case "$role" in
        ns|resolver) ;;
        *) return 1 ;;
    esac

    # No software part (arg had no '_', or was empty after the prefix).
    if [ -z "$sw" ] || [ "$sw" = "$arg" ]; then
        return 1
    fi

    target="$REPO_DIR/${role}_software/${sw}/${script}"
    if [ -x "$target" ]; then
        echo "$target"
        return 0
    fi
    return 1
}

# list_available <script>
# Prints, one per line, the <role>_<sw> identifier for every software directory
# under ns_software/ and resolver_software/ that contains an executable <script>.
list_available() {
    script=$1
    for role in ns resolver; do
        for dir in "$REPO_DIR/${role}_software"/*/; do
            [ -d "$dir" ] || continue
            [ -x "${dir}${script}" ] || continue
            sw=$(basename "$dir")
            echo "${role}_${sw}"
        done
    done
}

# run_all <script> [args...]
# Runs <script> for every software (both trees) that has an executable copy of it.
run_all() {
    script=$1
    shift
    for role in ns resolver; do
        for dir in "$REPO_DIR/${role}_software"/*/; do
            [ -d "$dir" ] || continue
            [ -x "${dir}${script}" ] || continue
            "${dir}${script}" "$@"
        done
    done
}
