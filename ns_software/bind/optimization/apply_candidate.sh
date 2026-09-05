#!/bin/bash
# The single privileged entrypoint of the BIND config tuner.
#
# Installed root-owned at /usr/local/sbin/dns_tuner_apply by install_tuner.sh.
# sudoers whitelists exactly these argv forms and nothing else:
#
#     sudo /usr/local/sbin/dns_tuner_apply apply-and-stop
#     sudo /usr/local/sbin/dns_tuner_apply baseline
#     sudo /usr/local/sbin/dns_tuner_apply status
#
# `rollback <generation>` also exists, but only the deadman timer and this
# script's own error paths invoke it -- it is not in the sudoers allowlist.
#
# The boundary this script defends carries a *typed candidate dict*, never
# config text. It renders the artifacts itself, as root, from the root-owned
# copy of tunables.yaml -- so a workstation that has been tampered with cannot
# hand the name server a config that named-checkconf happens to accept
# (checkconf is perfectly happy with `recursion yes; allow-transfer { any; };`).
#
# Exit codes, consumed by config_tuner/tuner/apply.py:
#     0  applied, healthy, conformant
#     2  invalid candidate (nothing in /etc was touched)
#     3  named-checkconf rejected the rendered config
#     4  sysctl/NIC/restart failed
#     5  health probe failed
#     6  ROLLBACK ITSELF FAILED -- host is suspect, campaign must halt
#     7  conformance probe failed
#    75  another apply holds the host lock

set -uo pipefail
umask 077
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

LIB=/usr/local/lib/dns-tuner
STATE=/var/lib/dns-tuner
STAGING=$STATE/staging/candidate.json
BACKUPS=/var/backups/dns-tuner
AUDIT=/var/log/dns-tuner/audit.jsonl
LOCKFILE=/var/lock/dns-tuner.lock
DEADMAN_UNIT=dns-tuner-deadman
DEADMAN_SECONDS=180
HEALTH_DEADLINE=45

SCHEMA=$LIB/tunables.yaml
RENDERER=$LIB/render_config.py
CONFORMANCE=$LIB/conformance.sh
NIC_ALLOWLIST=$LIB/nic_allowlist.conf
FACTS=$LIB/facts.json

BIND_OPTIONS=/etc/bind/named.conf.options
BIND_DEFAULT=/etc/default/named
SYSCTL_DROPIN=/etc/sysctl.d/99-dns-tuner.conf

PRISTINE_OPTIONS=/local/repository/ns_software/bind/named.conf.options

GEN=""
CANDIDATE_ID="-"
RENDER_DIR=""

log()  { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; }
die()  { local code=$1; shift; log "ERROR($code): $*"; emit "$code" "$*"; exit "$code"; }

emit() {
    # One machine-readable line on stdout; apply.py parses this.
    local code=$1 message=${2:-}
    printf '{"status":%s,"exit_code":%d,"candidate_id":"%s","generation":"%s","message":"%s"}\n' \
        "$([ "$code" -eq 0 ] && echo '"ok"' || echo '"error"')" \
        "$code" "$CANDIDATE_ID" "${GEN##*/}" \
        "$(printf '%s' "$message" | tr -d '"\\' | tr '\n' ' ')"
}

audit() {
    mkdir -p "$(dirname "$AUDIT")"
    printf '{"ts":"%s","action":"%s","candidate_id":"%s","generation":"%s","result":"%s"}\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$CANDIDATE_ID" "${GEN##*/}" "$2" >> "$AUDIT"
}

# --------------------------------------------------------------- deadman timer
# The load-bearing safety property for unattended operation. If a candidate
# wedges the host or drops the network -- which reconfiguring NIC queues can do
# to our own SSH session -- this fires and restores the previous generation with
# nobody watching. Armed before the first mutation, disarmed only on success.

arm_deadman() {
    systemctl stop "$DEADMAN_UNIT.timer"    >/dev/null 2>&1 || true
    systemctl reset-failed "$DEADMAN_UNIT.service" >/dev/null 2>&1 || true
    if systemd-run --quiet --on-active="$DEADMAN_SECONDS" --unit="$DEADMAN_UNIT" \
            /usr/local/sbin/dns_tuner_apply rollback "$GEN" >/dev/null 2>&1; then
        log "deadman armed: rollback to ${GEN##*/} in ${DEADMAN_SECONDS}s unless disarmed"
    else
        die 4 "could not arm the deadman timer; refusing to mutate the host"
    fi
}

disarm_deadman() {
    systemctl stop "$DEADMAN_UNIT.timer"    >/dev/null 2>&1 || true
    systemctl reset-failed "$DEADMAN_UNIT.service" >/dev/null 2>&1 || true
    log "deadman disarmed"
}

# ------------------------------------------------------------------- rollback

do_rollback() {
    local gen=$1
    log "ROLLING BACK to ${gen##*/}"
    if [ ! -d "$gen" ]; then
        log "backup generation $gen is missing"
        return 1
    fi

    [ -f "$gen/named.conf.options" ] && cp -a "$gen/named.conf.options" "$BIND_OPTIONS"
    [ -f "$gen/default-named" ]      && cp -a "$gen/default-named" "$BIND_DEFAULT"
    if [ -f "$gen/99-dns-tuner.conf" ]; then
        cp -a "$gen/99-dns-tuner.conf" "$SYSCTL_DROPIN"
    else
        rm -f "$SYSCTL_DROPIN"
    fi
    sysctl --system >/dev/null 2>&1 || true

    # Restore NIC settings if we recorded any.
    if [ -f "$gen/nic_restore.sh" ]; then
        bash "$gen/nic_restore.sh" >/dev/null 2>&1 || log "NIC restore reported an error"
    fi

    systemctl daemon-reload >/dev/null 2>&1 || true
    systemctl restart named >/dev/null 2>&1
    wait_healthy || { log "still unhealthy after restoring ${gen##*/}"; return 1; }
    log "rollback to ${gen##*/} succeeded"
    return 0
}

rollback_or_fatal() {
    local reason=$1 code=$2
    if do_rollback "$GEN"; then
        audit "apply" "rolled_back"
        die "$code" "$reason (rolled back to ${GEN##*/})"
    fi
    # The candidate's generation could not be restored. Try the last known-good.
    if [ -L "$STATE/last-good" ] && do_rollback "$(readlink -f "$STATE/last-good")"; then
        audit "apply" "rolled_back_to_last_good"
        die "$code" "$reason (fell back to last-good)"
    fi
    audit "apply" "ROLLBACK_FAILED"
    die 6 "$reason AND rollback failed -- host is in an unknown state"
}

# --------------------------------------------------------------------- probes

wait_healthy() {
    local deadline=$((SECONDS + HEALTH_DEADLINE))
    while [ $SECONDS -lt $deadline ]; do
        if systemctl is-active --quiet named 2>/dev/null; then
            if [ -n "$(dig +short +timeout=2 +tries=1 @127.0.0.1 ns1.workbench.lan A 2>/dev/null)" ]; then
                return 0
            fi
        fi
        sleep 1
    done
    return 1
}

# ------------------------------------------------------------------ NIC pinning
# The interface is NEVER read from the candidate. It comes from the root-owned
# allowlist and is re-checked here, so the management NIC cannot be touched even
# if every other layer were compromised.

resolve_iface() {
    [ -r "$NIC_ALLOWLIST" ] || { echo ""; return 0; }
    local iface
    iface=$(grep -vE '^\s*(#|$)' "$NIC_ALLOWLIST" | head -1 | tr -d '[:space:]')
    [ -n "$iface" ] || { echo ""; return 0; }

    if ! grep -qxF "$iface" <(grep -vE '^\s*(#|$)' "$NIC_ALLOWLIST" | tr -d ' \t'); then
        log "interface '$iface' is not in the allowlist"; echo ""; return 0
    fi
    if ! ip link show "$iface" >/dev/null 2>&1; then
        log "allowlisted interface '$iface' does not exist"; echo ""; return 0
    fi
    local default_iface
    default_iface=$(ip -o route show default 2>/dev/null | awk '{print $5}' | head -1)
    if [ -n "$default_iface" ] && [ "$iface" = "$default_iface" ]; then
        log "REFUSING: '$iface' carries the default route"; echo ""; return 0
    fi
    echo "$iface"
}

snapshot_nic() {
    local iface=$1 gen=$2
    [ -n "$iface" ] || return 0
    ethtool -g "$iface" > "$gen/ethtool-g.txt" 2>/dev/null || true
    ethtool -l "$iface" > "$gen/ethtool-l.txt" 2>/dev/null || true

    # Build an executable restore script from the current values.
    local rx tx combined
    rx=$(awk '/Current hardware settings/,0 {if ($1=="RX:") {print $2; exit}}' "$gen/ethtool-g.txt" 2>/dev/null)
    tx=$(awk '/Current hardware settings/,0 {if ($1=="TX:") {print $2; exit}}' "$gen/ethtool-g.txt" 2>/dev/null)
    combined=$(awk '/Current hardware settings/,0 {if ($1=="Combined:") {print $2; exit}}' "$gen/ethtool-l.txt" 2>/dev/null)

    {
        echo "#!/bin/bash"
        echo "# NIC restore for generation ${gen##*/}"
        [ -n "$rx" ] && [ -n "$tx" ] && echo "ethtool -G $iface rx $rx tx $tx || true"
        [ -n "$combined" ] && echo "ethtool -L $iface combined $combined || true"
    } > "$gen/nic_restore.sh"
    chmod 0755 "$gen/nic_restore.sh"
}

apply_nic() {
    local iface=$1 envfile=$2
    [ -f "$envfile" ] || return 0
    # shellcheck source=/dev/null
    . "$envfile"
    local staged_iface=${TUNER_IFACE:-}
    [ -n "$staged_iface" ] || return 0

    # Cross-check: the renderer was told the interface by us, but re-verify that
    # what came back matches the allowlisted one before handing it to ethtool.
    if [ "$staged_iface" != "$iface" ]; then
        log "staged interface '$staged_iface' != allowlisted '$iface'"
        return 1
    fi

    if [ -n "${ETHTOOL_RING_RX:-}" ]; then
        log "ethtool -G $iface rx $ETHTOOL_RING_RX"
        ethtool -G "$iface" rx "$ETHTOOL_RING_RX" || return 1
    fi
    if [ -n "${ETHTOOL_CHANNELS_COMBINED:-}" ]; then
        log "ethtool -L $iface combined $ETHTOOL_CHANNELS_COMBINED"
        ethtool -L "$iface" combined "$ETHTOOL_CHANNELS_COMBINED" || return 1
    fi
    return 0
}

# ----------------------------------------------------------------- subcommands

cmd_apply() {
    local stop_after=$1

    [ -f "$STAGING" ] || die 2 "no staged candidate at $STAGING"
    [ -L "$STAGING" ] && die 2 "staged candidate is a symlink"
    [ "$(stat -c%s "$STAGING")" -le 65536 ] || die 2 "staged candidate is too large"

    # --- Render as root, from the root-owned schema. Nothing in /etc is touched
    #     yet, so a rejection here needs no rollback.
    RENDER_DIR=$(mktemp -d /run/dns-tuner-render.XXXXXX)
    local iface render_args
    iface=$(resolve_iface)
    render_args=(--schema "$SCHEMA" --candidate "$STAGING" --out-dir "$RENDER_DIR")
    [ -r "$FACTS" ] && render_args+=(--facts "$FACTS")
    [ -n "$iface" ] && render_args+=(--iface "$iface")

    if ! CANDIDATE_ID=$(python3 "$RENDERER" "${render_args[@]}" 2>"$RENDER_DIR/err"); then
        local why; why=$(head -3 "$RENDER_DIR/err" | tr '\n' ' ')
        rm -rf "$RENDER_DIR"
        die 2 "$why"
    fi
    log "rendered candidate $CANDIDATE_ID (iface=${iface:-none})"

    # --- Snapshot everything we are about to change.
    GEN=$BACKUPS/$(date -u +%Y%m%dT%H%M%SZ)-$CANDIDATE_ID
    mkdir -p "$GEN"
    [ -f "$BIND_OPTIONS" ]  && cp -a "$BIND_OPTIONS" "$GEN/named.conf.options"
    [ -f "$BIND_DEFAULT" ]  && cp -a "$BIND_DEFAULT" "$GEN/default-named"
    [ -f "$SYSCTL_DROPIN" ] && cp -a "$SYSCTL_DROPIN" "$GEN/99-dns-tuner.conf"
    named -v > "$GEN/bind_version" 2>&1 || true
    snapshot_nic "$iface" "$GEN"

    arm_deadman

    # --- Pre-validate against the real include tree without touching /etc.
    #     Debian's named.conf uses absolute includes, so a shadow directory does
    #     not work; synthesize a checkconf-only wrapper instead. No -z: loading
    #     65k records on every evaluation is far too slow.
    {
        printf 'include "%s";\n' "$RENDER_DIR/named.conf.options"
        [ -f /etc/bind/named.conf.local ]         && printf 'include "/etc/bind/named.conf.local";\n'
        [ -f /etc/bind/named.conf.default-zones ] && printf 'include "/etc/bind/named.conf.default-zones";\n'
    } > "$RENDER_DIR/named.conf"
    if ! named-checkconf "$RENDER_DIR/named.conf" > "$RENDER_DIR/checkconf.err" 2>&1; then
        disarm_deadman
        local why; why=$(head -3 "$RENDER_DIR/checkconf.err" | tr '\n' ' ')
        rm -rf "$RENDER_DIR"
        die 3 "named-checkconf rejected the rendered config: $why"
    fi

    # --- Atomic install. Same filesystem, so rename(2) means named never sees a
    #     torn file.
    install -m0644 -o root -g root "$RENDER_DIR/named.conf.options" /etc/bind/.named.conf.options.new \
        || rollback_or_fatal "could not stage named.conf.options" 4
    mv -f /etc/bind/.named.conf.options.new "$BIND_OPTIONS" \
        || rollback_or_fatal "could not install named.conf.options" 4

    install -m0644 -o root -g root "$RENDER_DIR/default-named" /etc/default/.named.new \
        || rollback_or_fatal "could not stage /etc/default/named" 4
    mv -f /etc/default/.named.new "$BIND_DEFAULT" \
        || rollback_or_fatal "could not install /etc/default/named" 4

    install -m0644 -o root -g root "$RENDER_DIR/99-dns-tuner.conf" "$SYSCTL_DROPIN" \
        || rollback_or_fatal "could not install the sysctl drop-in" 4

    named-checkconf /etc/bind/named.conf >/dev/null 2>&1 \
        || rollback_or_fatal "live named.conf failed checkconf after install" 3

    # --- OS then NIC. NIC last, because it is the change that can drop our own
    #     SSH session -- and the deadman still has margin at this point.
    sysctl --system >/dev/null 2>&1 || rollback_or_fatal "sysctl --system failed" 4
    if [ -n "$iface" ]; then
        apply_nic "$iface" "$RENDER_DIR/nic.env" \
            || rollback_or_fatal "ethtool failed on $iface" 4
    fi

    systemctl daemon-reload >/dev/null 2>&1 || true
    systemctl restart named >/dev/null 2>&1 \
        || rollback_or_fatal "systemctl restart named failed" 4

    wait_healthy || rollback_or_fatal "health probe failed after restart" 5

    # EXPECT_TUNER_MANAGED: we just rendered and installed this config, so the
    # generated header must be present. Its absence would mean something
    # overwrote the file between install and probe.
    EXPECT_TUNER_MANAGED=yes bash "$CONFORMANCE" >"$RENDER_DIR/conformance.log" 2>&1 || {
        log "$(cat "$RENDER_DIR/conformance.log")"
        rollback_or_fatal "conformance probe failed" 7
    }

    disarm_deadman
    ln -sfn "$GEN" "$STATE/last-good"
    audit "apply" "ok"
    rm -rf "$RENDER_DIR"

    # `apply-and-stop`: leave named stopped so the evaluator's own
    # stop -> start -> wait_for_dns_ready path gives every candidate an
    # identical cold start. Without this, each measurement would run against a
    # process that had already been restarted once.
    if [ "$stop_after" = "yes" ]; then
        systemctl stop named >/dev/null 2>&1 || true
        log "named stopped; the evaluator will cold-start it"
    fi

    log "applied $CANDIDATE_ID successfully"
    emit 0 "applied"
    return 0
}

cmd_baseline() {
    [ -f "$PRISTINE_OPTIONS" ] || die 2 "pristine config not found at $PRISTINE_OPTIONS"
    CANDIDATE_ID="baseline"
    GEN=$BACKUPS/$(date -u +%Y%m%dT%H%M%SZ)-baseline
    mkdir -p "$GEN"
    [ -f "$BIND_OPTIONS" ] && cp -a "$BIND_OPTIONS" "$GEN/named.conf.options"
    [ -f "$BIND_DEFAULT" ] && cp -a "$BIND_DEFAULT" "$GEN/default-named"

    cp -a "$PRISTINE_OPTIONS" "$BIND_OPTIONS"
    rm -f "$SYSCTL_DROPIN"
    sysctl --system >/dev/null 2>&1 || true
    # Restore the NIC to whatever the last-good generation recorded.
    if [ -L "$STATE/last-good" ] && [ -f "$(readlink -f "$STATE/last-good")/nic_restore.sh" ]; then
        bash "$(readlink -f "$STATE/last-good")/nic_restore.sh" >/dev/null 2>&1 || true
    fi

    systemctl daemon-reload >/dev/null 2>&1 || true
    systemctl restart named >/dev/null 2>&1
    wait_healthy || die 5 "baseline config did not come up healthy"
    audit "baseline" "ok"
    log "restored the pristine baseline config"
    emit 0 "baseline restored"
    return 0
}

cmd_status() {
    local managed=false cid="-"
    if [ -r "$BIND_OPTIONS" ] && grep -q 'GENERATED BY config_tuner' "$BIND_OPTIONS"; then
        managed=true
        cid=$(grep -o 'candidate_id: [0-9a-f]*' "$BIND_OPTIONS" | head -1 | awk '{print $2}')
    fi
    printf '{"tuner_managed":%s,"candidate_id":"%s","named_active":%s,"last_good":"%s","iface":"%s","schema_sha":"%s"}\n' \
        "$managed" "${cid:--}" \
        "$(systemctl is-active --quiet named && echo true || echo false)" \
        "$([ -L "$STATE/last-good" ] && basename "$(readlink -f "$STATE/last-good")" || echo '-')" \
        "$(resolve_iface)" \
        "$(sha256sum "$SCHEMA" 2>/dev/null | awk '{print $1}')"
    return 0
}

# ---------------------------------------------------------------------- dispatch

[ "$(id -u)" -eq 0 ] || { echo "must run as root (via sudo)" >&2; exit 1; }

ACTION=${1:-}
case "$ACTION" in
    apply-and-stop|apply|baseline)
        mkdir -p "$STATE/staging" "$BACKUPS" "$(dirname "$AUDIT")"
        exec 9>"$LOCKFILE"
        flock -n 9 || { echo '{"status":"error","exit_code":75,"message":"host busy"}'; exit 75; }
        case "$ACTION" in
            apply-and-stop) cmd_apply yes ;;
            apply)          cmd_apply no ;;
            baseline)       cmd_baseline ;;
        esac
        ;;
    rollback)
        # Invoked by the deadman timer and by internal error paths only; not in
        # the sudoers allowlist.
        GEN=${2:?rollback needs a generation directory}
        do_rollback "$GEN" || exit 6
        ;;
    status)
        cmd_status
        ;;
    *)
        cat >&2 <<EOF
Usage: dns_tuner_apply <apply-and-stop|apply|baseline|status>
  apply-and-stop  render+apply the staged candidate, verify, then stop named
  apply           same, but leave named running
  baseline        restore the pristine repo config
  status          report whether /etc/bind is tuner-managed
EOF
        exit 1
        ;;
esac
