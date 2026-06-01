#!/bin/sh

# Stop and uninstall all DNS software from the current node.
# Each per-role script stops its service before removing its package/build.

for role in bind/ns bind/resolver powerdns/ns powerdns/resolver \
            knot/ns knot/resolver nsd/ns unbound/ns unbound/resolver; do
    /local/repository/$role/uninstall.sh
done

echo "Done."
