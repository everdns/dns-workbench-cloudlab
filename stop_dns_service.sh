#!/bin/sh
# Stop DNS software services.
# Usage: ./stop_dns_service.sh [<role>_<software>]
# With no argument, stops every discovered service.

. "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/script_lib.sh"

if [ -z "$1" ]; then
    run_all stop.sh
    exit 0
fi

target=$(resolve_target "$1" stop.sh)
if [ -z "$target" ]; then
    echo "Software '$1' does not exist."
    echo "Available software:"
    list_available stop.sh | sed 's/^/  /'
    echo "Or run with no argument to stop all services."
    exit 1
fi

exec "$target"
