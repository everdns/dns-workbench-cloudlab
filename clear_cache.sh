#!/bin/sh
# Clear the cache of a single DNS software service.
# Usage: clear_cache.sh <role>_<software>   (e.g. resolver_bind)
# In practice only resolver software has a cache to clear.

. "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/script_lib.sh"

if [ -z "$1" ]; then
    echo "Usage: $0 <software>"
    echo "Available software:"
    list_available clear_cache.sh | sed 's/^/  /'
    exit 1
fi

target=$(resolve_target "$1" clear_cache.sh)
if [ -z "$target" ]; then
    echo "Software '$1' does not exist."
    echo "Available software:"
    list_available clear_cache.sh | sed 's/^/  /'
    exit 1
fi

exec "$target"
