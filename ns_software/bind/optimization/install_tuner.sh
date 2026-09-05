#!/bin/bash
# One-time privileged setup for the BIND config tuner, run ON THE NAME SERVER.
#
#     sudo bash /local/repository/ns_software/bind/optimization/install_tuner.sh <iface>
#
# The whole point of this script is that /local/repository is a git checkout the
# experiment user can write. A sudoers rule pointing there would grant nothing,
# because anyone who could invoke it could also rewrite it first. So the helper,
# the schema, and the conformance probe are copied to root-owned locations under
# /usr/local, and sudoers whitelists only those copies.
#
# Re-running this is safe and is how you pick up a schema change.

set -euo pipefail

REPO=/local/repository
SRC="$REPO/ns_software/bind/optimization"
LIB=/usr/local/lib/dns-tuner
SBIN=/usr/local/sbin
STATE=/var/lib/dns-tuner
BACKUPS=/var/backups/dns-tuner
LOGDIR=/var/log/dns-tuner
SUDOERS=/etc/sudoers.d/dns-tuner

[ "$(id -u)" -eq 0 ] || { echo "run me as root: sudo bash $0 <iface>" >&2; exit 1; }

IFACE=${1:-}
TUNER_USER=${SUDO_USER:-$(logname 2>/dev/null || echo "")}

say() { printf '  %s\n' "$*"; }

echo "=== dns-tuner install ==="

# ---------------------------------------------------------------- interface gate
# Getting this wrong is the one mistake with no path back: reconfiguring the
# management NIC locks you out of the node entirely. Refuse rather than warn.
if [ -z "$IFACE" ]; then
    IFACE=$(ip -o -4 addr show 2>/dev/null | awk '$4 ~ /^10\.10\./ {print $2}' | head -1)
    [ -n "$IFACE" ] && say "no interface given; inferred '$IFACE' from the 10.10.x address"
fi
[ -n "$IFACE" ] || { echo "ERROR: pass the tuning interface as \$1" >&2; exit 1; }

ip link show "$IFACE" >/dev/null 2>&1 \
    || { echo "ERROR: interface '$IFACE' does not exist" >&2; exit 1; }

DEFAULT_IFACE=$(ip -o route show default 2>/dev/null | awk '{print $5}' | head -1)
if [ -n "$DEFAULT_IFACE" ] && [ "$IFACE" = "$DEFAULT_IFACE" ]; then
    echo "ERROR: '$IFACE' carries the default route. Tuning it would lock you out." >&2
    exit 1
fi
say "tuning interface: $IFACE (default route is on '${DEFAULT_IFACE:-none}')"

# --------------------------------------------------------------- prerequisites
for tool in named named-checkconf dig ethtool sysctl systemd-run flock python3; do
    command -v "$tool" >/dev/null 2>&1 \
        || { echo "ERROR: required tool '$tool' is missing" >&2; exit 1; }
done
if ! python3 -c 'import yaml' 2>/dev/null; then
    say "installing python3-yaml (the renderer needs it)"
    apt-get install -y python3-yaml >/dev/null 2>&1 \
        || { echo "ERROR: could not install python3-yaml" >&2; exit 1; }
fi
say "prerequisites present"

# -------------------------------------------------------- root-owned installation
install -d -m0755 -o root -g root "$LIB"
install -d -m0750 -o root -g root "$STATE" "$STATE/staging" "$BACKUPS" "$LOGDIR"

install -m0755 -o root -g root "$SRC/apply_candidate.sh" "$SBIN/dns_tuner_apply"
install -m0755 -o root -g root "$SRC/conformance.sh"     "$LIB/conformance.sh"
install -m0644 -o root -g root "$SRC/render_config.py"   "$LIB/render_config.py"
install -m0644 -o root -g root "$SRC/tunables.yaml"      "$LIB/tunables.yaml"
say "installed root-owned helper, schema, and probe under $LIB and $SBIN"

# The staging dir must be writable by the tuner user (it scp's the candidate
# there) but readable-only by root thereafter.
if [ -n "$TUNER_USER" ] && id "$TUNER_USER" >/dev/null 2>&1; then
    chown "$TUNER_USER":"$TUNER_USER" "$STATE/staging"
    chmod 0755 "$STATE"
    say "staging dir writable by '$TUNER_USER'"
else
    say "WARNING: could not determine the tuner user; set staging ownership by hand"
fi

# ------------------------------------------------------------- the NIC allowlist
# This file, not the candidate, decides which interface ethtool may touch.
cat > "$LIB/nic_allowlist.conf" <<EOF
# The ONLY interfaces the tuner may reconfigure with ethtool.
# Written by install_tuner.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ).
# The candidate dict cannot name an interface; this file is the sole source.
$IFACE
EOF
chmod 0644 "$LIB/nic_allowlist.conf"
chown root:root "$LIB/nic_allowlist.conf"
say "NIC allowlist pinned to '$IFACE'"

# ------------------------------------------------------------------ host facts
# fact_cap entries in tunables.yaml clamp declared maxima to what this host
# actually supports, so `named -n 64` on a 40-core box is rejected before it is
# ever written.
RX_MAX=$(ethtool -g "$IFACE" 2>/dev/null | awk '/Pre-set maximums/,/Current/ {if ($1=="RX:") {print $2; exit}}')
COMBINED_MAX=$(ethtool -l "$IFACE" 2>/dev/null | awk '/Pre-set maximums/,/Current/ {if ($1=="Combined:") {print $2; exit}}')
cat > "$LIB/facts.json" <<EOF
{
  "nproc": $(nproc),
  "ethtool_rx_max": ${RX_MAX:-null},
  "ethtool_combined_max": ${COMBINED_MAX:-null},
  "iface": "$IFACE",
  "bind_version": "$(named -v 2>&1 | head -1 | tr -d '"')",
  "captured_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF
chmod 0644 "$LIB/facts.json"
say "host facts: nproc=$(nproc) rx_max=${RX_MAX:-unknown} combined_max=${COMBINED_MAX:-unknown}"

# ------------------------------------------------------------ conformance config
cat > "$LIB/conformance.conf" <<'EOF'
# Assertions run after every apply. PROBE_EXPECT may be left empty to require
# only a non-empty NOERROR answer.
SERVER=127.0.0.1
PROBE_NAME=ns1.workbench.lan
PROBE_TYPE=A
PROBE_EXPECT=10.10.1.2
ABSENT_NAME=definitely-absent-probe.workbench.lan
RECURSE_NAME=www.example.com
EOF
chmod 0644 "$LIB/conformance.conf"

# ---------------------------------------------------------------------- sudoers
# sudo matches argv exactly, so there are no wildcards anywhere. `rollback` is
# deliberately absent: only the deadman timer and the script's own error paths
# invoke it.
if [ -z "$TUNER_USER" ]; then
    say "WARNING: no tuner user detected; skipping sudoers (install it by hand)"
else
    TMP_SUDOERS=$(mktemp)
    sed "s/@TUNER_USER@/$TUNER_USER/" "$SRC/dns-tuner.sudoers" > "$TMP_SUDOERS"
    if visudo -cf "$TMP_SUDOERS" >/dev/null 2>&1; then
        install -m0440 -o root -g root "$TMP_SUDOERS" "$SUDOERS"
        say "installed $SUDOERS for '$TUNER_USER'"
    else
        rm -f "$TMP_SUDOERS"
        echo "ERROR: generated sudoers file failed visudo validation" >&2
        exit 1
    fi
    rm -f "$TMP_SUDOERS"
fi

# ----------------------------------------------------------------------- verify
echo
echo "=== verification ==="
"$SBIN/dns_tuner_apply" status || true

cat <<EOF

=== next steps ===
  1. Smoke-test the safety path (about 2 minutes, no load generation):
         bash $SRC/../../../config_tuner/smoke_test.sh
  2. Note that CloudLab still grants '$TUNER_USER' blanket passwordless sudo.
     This install bounds what the *tuner* can do, not what a local user can do.
     That is the intended threat model: it contains an LLM proposing something
     harmful, not a determined operator.
EOF
