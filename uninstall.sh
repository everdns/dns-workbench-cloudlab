#!/bin/sh
# Stop and uninstall DNS software from the current node.
# Usage: ./uninstall.sh [<role>_<software>]
# With no argument, uninstalls every discovered software.
# Each per-software uninstall.sh stops its service before removing its package/build.

. "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/script_lib.sh"

if [ -z "$1" ]; then
    run_all uninstall.sh
    echo "Done."
    exit 0
fi

target=$(resolve_target "$1" uninstall.sh)
if [ -z "$target" ]; then
    echo "Software '$1' does not exist."
    echo "Available software:"
    list_available uninstall.sh | sed 's/^/  /'
    echo "Or run with no argument to uninstall all software."
    exit 1
fi

"$target"
echo "Done."
