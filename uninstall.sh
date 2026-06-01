#!/bin/sh

# Stop and uninstall all DNS software from the current node.
# Each per-role script stops its service before removing its package/build.

for role in ns_software/bind resolver_software/bind ns_software/powerdns resolver_software/powerdns \
            ns_software/knot resolver_software/knot ns_software/nsd ns_software/unbound resolver_software/unbound; do
    /local/repository/$role/uninstall.sh
done

echo "Done."
