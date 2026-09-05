#!/bin/bash
# Phase 0 environment verification for the BIND config tuner.
#
# Run this ON THE NAME SERVER (10.10.1.2), not on a workstation:
#     bash /local/repository/ns_software/bind/optimization/verify_environment.sh [iface]
#
# It answers the questions that gate the tuning schema:
#   - does the named unit source /etc/default/named, or do we need a systemd drop-in?
#   - which BIND version is installed, and which candidate directives does it accept?
#   - how many cores, and what does the tuning NIC actually support?
#   - which interface carries the default route (that one must never be tuned)?
#
# Read-only: it writes only under a scratch dir in /tmp and never touches /etc.

set -uo pipefail

IFACE="${1:-}"
SCRATCH=$(mktemp -d /tmp/dns-tuner-verify.XXXXXX)
trap 'rm -rf "$SCRATCH"' EXIT

pass=0
fail=0
warn=0

hdr()  { printf '\n=== %s ===\n' "$1"; }
ok()   { printf '  [ ok ] %s\n' "$1"; pass=$((pass+1)); }
bad()  { printf '  [FAIL] %s\n' "$1"; fail=$((fail+1)); }
note() { printf '  [warn] %s\n' "$1"; warn=$((warn+1)); }
info() { printf '         %s\n' "$1"; }

printf '=== dns-tuner environment verification ===\n'
printf 'host: %s   date: %s\n' "$(hostname)" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# ---------------------------------------------------------------- BIND itself
hdr "BIND"
if command -v named >/dev/null 2>&1; then
    BIND_V=$(named -v 2>&1 | head -1)
    ok "named present: $BIND_V"
else
    bad "named not found -- install BIND first (ns_software/bind/install.sh)"
fi

for tool in named-checkconf dig systemd-run flock ethtool sysctl; do
    if command -v "$tool" >/dev/null 2>&1; then
        ok "$tool present"
    else
        bad "$tool MISSING -- the tuner depends on it"
    fi
done

# --------------------------------------------------- startup-flag target check
# RISK 2 in the plan: if the unit does not source /etc/default/named, the
# `named_threads` knob must be rendered as a systemd drop-in instead.
hdr "Startup flag target (/etc/default/named vs systemd drop-in)"
if systemctl cat named >"$SCRATCH/unit.txt" 2>/dev/null; then
    if grep -qE 'EnvironmentFile=.*default/named' "$SCRATCH/unit.txt"; then
        ok "named.service sources /etc/default/named -> targets.startup.kind: shell_env"
        info "$(grep -E 'EnvironmentFile' "$SCRATCH/unit.txt" | head -2 | tr '\n' ' ')"
    else
        note "named.service does NOT source /etc/default/named"
        info "-> set targets.startup.kind: systemd_dropin in tunables.yaml"
        info "-> path: /etc/systemd/system/named.service.d/10-dns-tuner.conf"
    fi
    if grep -qE '^\s*ExecStart' "$SCRATCH/unit.txt"; then
        info "ExecStart: $(grep -E '^\s*ExecStart' "$SCRATCH/unit.txt" | head -1)"
    fi
else
    bad "systemctl cat named failed -- is the unit named something else?"
fi

printf '\n  current /etc/default/named:\n'
if [ -r /etc/default/named ]; then
    sed 's/^/    | /' /etc/default/named
else
    info "(absent)"
fi

# ------------------------------------------------------------------- CPU / NUMA
hdr "CPU"
NPROC=$(nproc)
ok "nproc = $NPROC  -> named_threads fact_cap"
info "$(lscpu 2>/dev/null | grep -E '^(Model name|Socket|Core|Thread|NUMA node\(s\))' | tr '\n' ';')"

# -------------------------------------------------------------------- Interfaces
hdr "Network interfaces"
DEFAULT_IFACE=$(ip -o route show default 2>/dev/null | awk '{print $5}' | head -1)
if [ -n "$DEFAULT_IFACE" ]; then
    ok "default route is on '$DEFAULT_IFACE' -- this interface must NEVER be tuned"
else
    note "no default route found"
fi

if [ -z "$IFACE" ]; then
    # Guess the tuning interface: the one holding the 10.10.1.2 test address.
    IFACE=$(ip -o -4 addr show 2>/dev/null | awk '$4 ~ /^10\.10\./ {print $2}' | head -1)
    [ -n "$IFACE" ] && info "no interface argument given; inferred '$IFACE' from the 10.10.x address"
fi

if [ -z "$IFACE" ]; then
    bad "could not determine the tuning interface -- pass it as \$1"
elif ! ip link show "$IFACE" >/dev/null 2>&1; then
    bad "interface '$IFACE' does not exist"
elif [ "$IFACE" = "$DEFAULT_IFACE" ]; then
    bad "REFUSING: '$IFACE' carries the default route; tuning it would lock you out"
else
    ok "tuning interface '$IFACE' exists and is not the default route"
    printf '  %s addresses: %s\n' "$IFACE" "$(ip -o -4 addr show "$IFACE" | awk '{print $4}' | tr '\n' ' ')"

    printf '\n  ethtool -g %s (ring sizes -> nic_rx_ring fact_cap):\n' "$IFACE"
    ethtool -g "$IFACE" 2>&1 | sed 's/^/    | /'

    printf '\n  ethtool -l %s (channels -> nic_combined_queues fact_cap):\n' "$IFACE"
    ethtool -l "$IFACE" 2>&1 | sed 's/^/    | /'

    printf '\n  driver:\n'
    ethtool -i "$IFACE" 2>&1 | sed 's/^/    | /'
fi

# -------------------------------------------------------------------- sysctl
hdr "Current sysctl values (tuning baseline)"
for key in net.core.rmem_max net.core.wmem_max net.core.netdev_max_backlog \
           net.core.somaxconn net.ipv4.udp_mem; do
    val=$(sysctl -n "$key" 2>/dev/null)
    if [ -n "$val" ]; then
        printf '    %-34s = %s\n' "$key" "$val"
    else
        note "$key not readable"
    fi
done

# ------------------------------------------- candidate directive support probe
# The heart of Phase 0: does THIS BIND accept each directive we plan to tune?
# Each probe renders a minimal options block and runs named-checkconf on it.
hdr "Candidate directive support (named-checkconf probe)"
if ! command -v named-checkconf >/dev/null 2>&1; then
    bad "named-checkconf missing -- cannot probe directive support"
else
    probe() {
        local label=$1 line=$2
        cat > "$SCRATCH/probe.conf" <<EOF
options {
    directory "/var/cache/bind";
    recursion no;
    $line
};
EOF
        if named-checkconf "$SCRATCH/probe.conf" >"$SCRATCH/probe.err" 2>&1; then
            ok "$label"
        else
            note "$label REJECTED: $(head -1 "$SCRATCH/probe.err")"
            info "-> drop this knob from tunables.yaml, or gate it on the version"
        fi
    }

    probe "minimal-responses no-auth"   "minimal-responses no-auth;"
    probe "querylog no"                 "querylog no;"
    probe "answer-cookie no"            "answer-cookie no;"
    probe "zone-statistics full"        "zone-statistics full;"
    probe "tcp-clients 150"             "tcp-clients 150;"
    probe "udp-receive-buffer 1048576"  "udp-receive-buffer 1048576;"
    probe "udp-send-buffer 1048576"     "udp-send-buffer 1048576;"
    probe "max-udp-size 1232"           "max-udp-size 1232;"
    probe "notify no"                   "notify no;"
    probe "dnssec-validation no"        "dnssec-validation no;"
    probe "reuseport yes"               "reuseport yes;"
fi

# --------------------------------------------- named startup flag support probe
hdr "named startup flag support"
if command -v named >/dev/null 2>&1; then
    if named -h 2>&1 | grep -qE '^\s*-n\b'; then
        ok "named -n (worker threads) supported"
    else
        note "named -n not listed in usage -- check 'named -h'"
    fi
    if named -h 2>&1 | grep -qE '^\s*-U\b'; then
        note "named -U listed, but it is a no-op on BIND >= 9.18 (netmgr) -- do not tune it"
    else
        ok "named -U absent, as expected on BIND >= 9.18"
    fi
fi

# ------------------------------------------------------------------- summary
hdr "Summary"
printf '  %d ok, %d warnings, %d failures\n' "$pass" "$warn" "$fail"
if [ "$fail" -gt 0 ]; then
    printf '\n  Resolve the failures before building the tuning schema.\n'
    exit 1
fi
printf '\n  Record the ethtool maxima, nproc, and any REJECTED directives in\n'
printf '  ns_software/bind/optimization/tunables.yaml before the first campaign.\n'
exit 0
