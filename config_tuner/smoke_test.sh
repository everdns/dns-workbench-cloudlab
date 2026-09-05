#!/bin/bash
# T2: the testbed safety smoke test. Run ON THE NAME SERVER, before every session.
#
#     bash /local/repository/config_tuner/smoke_test.sh [--deadman]
#
# About two minutes, no load generation. This is the evidence for the project's
# central safety claim -- that an unattended optimizer cannot damage the host.
# If any assertion here fails, do not start a campaign.
#
# The tests that matter most are the negative ones: a rejected candidate must
# leave /etc byte-for-byte identical, and no interface except the allowlisted
# one may ever change. `--deadman` additionally verifies the unattended rollback
# timer, which takes an extra ~3 minutes.

set -uo pipefail

APPLY=/usr/local/sbin/dns_tuner_apply
LIB=/usr/local/lib/dns-tuner
STAGING=/var/lib/dns-tuner/staging/candidate.json
BIND_OPTIONS=/etc/bind/named.conf.options
SCRATCH=$(mktemp -d /tmp/dns-tuner-smoke.XXXXXX)
DEADMAN_TEST=false
[ "${1:-}" = "--deadman" ] && DEADMAN_TEST=true

pass=0; fail=0
ok()   { printf '  [ ok ] %s\n' "$1"; pass=$((pass+1)); }
bad()  { printf '  [FAIL] %s\n' "$1"; fail=$((fail+1)); }
hdr()  { printf '\n=== %s ===\n' "$1"; }
info() { printf '         %s\n' "$1"; }

cleanup() {
    rm -rf "$SCRATCH"
    printf '\n--- restoring baseline ---\n'
    sudo "$APPLY" baseline >/dev/null 2>&1 || echo "  WARNING: baseline restore failed"
}
trap cleanup EXIT

echo "=== dns-tuner safety smoke test ==="
echo "host: $(hostname)   date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"

[ -x "$APPLY" ] || { echo "ERROR: $APPLY not installed -- run install_tuner.sh first"; exit 1; }

# ------------------------------------------------------------ pre-test snapshot
hdr "Snapshot"
sudo "$APPLY" baseline >/dev/null 2>&1 || true
OPTIONS_SHA_BEFORE=$(sha256sum "$BIND_OPTIONS" | awk '{print $1}')
info "named.conf.options sha256: ${OPTIONS_SHA_BEFORE:0:16}..."

# Every interface, not just the allowlisted one. The key negative assertion is
# that the others are untouched at the end.
ALL_IFACES=$(ip -o link show | awk -F': ' '{print $2}' | cut -d@ -f1 | grep -v '^lo$')
for i in $ALL_IFACES; do
    { ethtool -g "$i" 2>/dev/null; ethtool -l "$i" 2>/dev/null; } > "$SCRATCH/nic-before-$i.txt"
done
ALLOWED_IFACE=$(grep -vE '^\s*(#|$)' "$LIB/nic_allowlist.conf" 2>/dev/null | head -1 | tr -d '[:space:]')
info "interfaces: $(echo "$ALL_IFACES" | tr '\n' ' ') (allowlisted: ${ALLOWED_IFACE:-none})"

# -------------------------------------------------------------------- T2.1 status
hdr "status"
if sudo "$APPLY" status > "$SCRATCH/status.json" 2>&1; then
    ok "status returns JSON: $(cat "$SCRATCH/status.json")"
else
    bad "status failed: $(cat "$SCRATCH/status.json")"
fi

# ------------------------------------------------------------- T2.2 valid apply
hdr "A valid candidate applies and passes conformance"
cat > "$SCRATCH/good.json" <<'EOF'
{"querylog": "no", "tcp_clients": 300, "zone_statistics": "none"}
EOF
cp "$SCRATCH/good.json" "$STAGING"
if sudo "$APPLY" apply-and-stop > "$SCRATCH/good.out" 2>"$SCRATCH/good.err"; then
    ok "apply-and-stop exit 0"
    info "$(cat "$SCRATCH/good.out")"
    if grep -q 'tcp-clients 300' "$BIND_OPTIONS"; then
        ok "the tuned directive reached /etc/bind/named.conf.options"
    else
        bad "tcp-clients 300 not found in the installed config"
    fi
    if grep -q 'recursion no' "$BIND_OPTIONS" && grep -q 'allow-recursion { none; }' "$BIND_OPTIONS"; then
        ok "invariants survived the apply"
    else
        bad "an invariant is missing from the installed config"
    fi
    if systemctl is-active --quiet named; then
        bad "named is still running -- apply-and-stop should have stopped it"
    else
        ok "named stopped, as apply-and-stop promises (evaluator cold-starts it)"
    fi
else
    bad "a valid candidate was rejected: $(tail -2 "$SCRATCH/good.err")"
fi

# The deadman must not outlive a successful apply.
if systemctl is-active --quiet dns-tuner-deadman.timer 2>/dev/null; then
    bad "the deadman timer is still armed after a successful apply"
else
    ok "deadman disarmed after success"
fi

sudo "$APPLY" baseline >/dev/null 2>&1
SHA_AFTER_BASELINE=$(sha256sum "$BIND_OPTIONS" | awk '{print $1}')
if [ "$SHA_AFTER_BASELINE" = "$OPTIONS_SHA_BEFORE" ]; then
    ok "baseline restores the config byte-for-byte"
else
    bad "baseline did not restore the original config"
fi

# ----------------------------------------------- T2.3 out-of-schema is rejected
# These bypass every client-side check by writing the staging file directly.
# The name server must reject them on its own, using its root-owned schema.
hdr "Out-of-schema candidates are rejected root-side, with /etc untouched"

reject_case() {
    local label=$1 payload=$2 expect=$3
    local sha_before sha_after rc
    sha_before=$(sha256sum "$BIND_OPTIONS" | awk '{print $1}')
    printf '%s' "$payload" > "$STAGING"
    sudo "$APPLY" apply-and-stop > "$SCRATCH/rej.out" 2>"$SCRATCH/rej.err"
    rc=$?
    sha_after=$(sha256sum "$BIND_OPTIONS" | awk '{print $1}')

    if [ "$rc" -eq "$expect" ]; then
        ok "$label -> exit $rc"
    else
        bad "$label -> exit $rc (expected $expect): $(tail -1 "$SCRATCH/rej.err")"
    fi
    if [ "$sha_before" = "$sha_after" ]; then
        ok "$label left /etc/bind/named.conf.options unchanged"
    else
        bad "$label MODIFIED the live config -- this is a containment failure"
    fi
}

reject_case "unknown parameter" \
    '{"definitely_not_a_knob": 1}' 2
reject_case "enum injection" \
    '{"minimal_responses": "no; }; options { recursion yes; };"}' 2
reject_case "int injection" \
    '{"named_threads": "8; querylog yes"}' 2
reject_case "out-of-range int" \
    '{"tcp_clients": 999999}' 2
reject_case "interface named in the candidate" \
    '{"nic_iface": "eth0"}' 2
reject_case "malformed JSON" \
    '{"querylog": ' 2

# --------------------------------------------------- T2.4 NIC containment
hdr "NIC containment"
for i in $ALL_IFACES; do
    { ethtool -g "$i" 2>/dev/null; ethtool -l "$i" 2>/dev/null; } > "$SCRATCH/nic-after-$i.txt"
    if diff -q "$SCRATCH/nic-before-$i.txt" "$SCRATCH/nic-after-$i.txt" >/dev/null 2>&1; then
        ok "interface '$i' ring/channel settings unchanged"
    elif [ "$i" = "$ALLOWED_IFACE" ]; then
        ok "interface '$i' changed -- but it is the allowlisted one"
    else
        bad "interface '$i' CHANGED and is not allowlisted -- containment failure"
    fi
done

if [ -n "$ALLOWED_IFACE" ]; then
    DEFAULT_IFACE=$(ip -o route show default 2>/dev/null | awk '{print $5}' | head -1)
    if [ "$ALLOWED_IFACE" = "$DEFAULT_IFACE" ]; then
        bad "the allowlisted interface carries the default route -- reinstall with a different one"
    else
        ok "allowlisted interface is not the default route"
    fi
fi

# ------------------------------------------------------------- T2.5 conformance
hdr "Conformance probe on the restored baseline"
if sudo bash "$LIB/conformance.sh" > "$SCRATCH/conf.out" 2>&1; then
    ok "conformance passes on the baseline config"
    sed 's/^/         /' "$SCRATCH/conf.out"
else
    bad "conformance FAILED on the baseline -- fix the zone or the probe config"
    sed 's/^/         /' "$SCRATCH/conf.out"
fi

# ----------------------------------------------------------------- T2.6 deadman
if [ "$DEADMAN_TEST" = true ]; then
    hdr "Deadman rollback (this takes ~3 minutes)"
    cp "$SCRATCH/good.json" "$STAGING"
    sudo "$APPLY" apply > "$SCRATCH/dm.out" 2>&1 &
    APPLY_PID=$!
    sleep 4
    # Kill the apply mid-flight, simulating a candidate that wedges the host.
    sudo pkill -9 -f 'dns_tuner_apply apply' 2>/dev/null || true
    wait $APPLY_PID 2>/dev/null || true

    if systemctl is-active --quiet dns-tuner-deadman.timer 2>/dev/null; then
        ok "deadman is armed after the apply was killed"
        info "waiting up to 210s for the unattended rollback..."
        for _ in $(seq 1 42); do
            sleep 5
            systemctl is-active --quiet dns-tuner-deadman.timer 2>/dev/null || break
        done
        sleep 10
        if [ -n "$(dig +short +timeout=2 @127.0.0.1 ns1.workbench.lan A 2>/dev/null)" ]; then
            ok "the host recovered on its own after the deadman fired"
        else
            bad "the host did NOT recover -- do not run unattended campaigns"
        fi
    else
        bad "the deadman was not armed when the apply was killed"
    fi
else
    hdr "Deadman rollback"
    info "skipped -- re-run with --deadman to verify unattended recovery (~3 min extra)"
fi

# ------------------------------------------------------------------- summary
hdr "Summary"
printf '  %d passed, %d failed\n' "$pass" "$fail"
if [ "$fail" -gt 0 ]; then
    printf '\n  SAFETY CHECK FAILED -- do not start an unattended campaign.\n'
    exit 1
fi
printf '\n  Safety path verified. The baseline config has been restored.\n'
exit 0
