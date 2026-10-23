#!/bin/sh
# Install the repo's default BIND config for one built version.
# Usage: configure_bind.sh <version> <options-file> <local-file> <dns64-zone-file>
# The options/local files are used verbatim; only a wrapper named.conf is added.
# The /etc/bind/db.workbench.lan zone must already be installed by sweep.py.
set -eu

V="$1"
OPTIONS="$2"
LOCAL="$3"
DNS64="$4"
CONF_DIR="/etc/bind-$V"
PREFIX="/opt/bind-$V"

sudo mkdir -p "$CONF_DIR" /etc/bind /var/cache/bind
sudo cp "$OPTIONS" "$CONF_DIR/named.conf.options"
sudo cp "$LOCAL" "$CONF_DIR/named.conf.local"
# named.conf.local also references this zone; it is loaded but never queried.
[ -f /etc/bind/db.dns64perf.test ] || sudo cp "$DNS64" /etc/bind/db.dns64perf.test
printf 'include "%s/named.conf.options";\ninclude "%s/named.conf.local";\n' \
    "$CONF_DIR" "$CONF_DIR" | sudo tee "$CONF_DIR/named.conf" >/dev/null

"$PREFIX/bin/named-checkconf" -z "$CONF_DIR/named.conf" 2>&1 \
    || "$PREFIX/sbin/named-checkconf" -z "$CONF_DIR/named.conf"
