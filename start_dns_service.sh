#!/bin/sh
# Start a single DNS software service.
# Usage: start_dns_service.sh <role>_<software>   (e.g. ns_bind, resolver_unbound)

. "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/script_lib.sh"

usage() {
    echo "Usage: $0 <software>"
    echo "Available software:"
    list_available start.sh | sed 's/^/  /'
}

if [ -z "$1" ]; then
    usage
    exit 1
fi

target=$(resolve_target "$1" start.sh)
if [ -z "$target" ]; then
    echo "Software '$1' does not exist."
    echo "Available software:"
    list_available start.sh | sed 's/^/  /'
    exit 1
fi

exec "$target"
