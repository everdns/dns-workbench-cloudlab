"""Host-level tuning on the name server: sysctls, firewall/conntrack, and
named's systemd limits.

Unlike the named.conf.options statements in bind_config.py, these settings live
outside BIND, so they are applied as shell commands over SSH and undone by
restoring a snapshot taken before the grid search starts. Every parameter
accepts "default", which leaves the host as it was, so the baseline can be kept
as a grid point.

Sysctls apply immediately. rmem_default and LimitNOFILE only reach sockets and
processes created afterwards, which is fine because run_max_sustainable_qps
restarts the DNS service for every point.
"""
import logging
import shlex

from max_sustainable_qps import ssh_run

log = logging.getLogger(__name__)

DEFAULT = "default"

NAMED_DROPIN = "/etc/systemd/system/named.service.d/zz-grid-search-nofile.conf"
CONNTRACK_HASHSIZE = "/sys/module/nf_conntrack/parameters/hashsize"

RAISED_CONNTRACK_MAX = 2097152
RAISED_CONNTRACK_HASHSIZE = 524288
RAISED_UDP_MIN = 16384
UDP_MEM_SCALE = 4

# Every sysctl a parameter may touch; snapshot() records these and restore()
# writes them back.
SYSCTL_KEYS = [
    "net.core.rmem_max",
    "net.core.rmem_default",
    "net.core.netdev_max_backlog",
    "net.core.netdev_budget",
    "net.core.netdev_budget_usecs",
    "net.core.optmem_max",
    "net.ipv4.udp_rmem_min",
    "net.ipv4.udp_wmem_min",
    "net.ipv4.udp_mem",
    "net.netfilter.nf_conntrack_max",
]

# Remove every copy of the udp/53 NOTRACK rules; the loop stops once none are left.
NOTRACK_RULES = [
    "PREROUTING -p udp --dport 53 -j CT --notrack",
    "OUTPUT -p udp --sport 53 -j CT --notrack",
]


class SystemConfigError(Exception):
    pass


def _is_default(value):
    return isinstance(value, str) and value.strip().lower() in (DEFAULT, "stock", "auto")


def _as_int(name, value, minimum=0):
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise SystemConfigError(f"{name}: expected an integer or '{DEFAULT}', got {value!r}")
    if number < minimum:
        raise SystemConfigError(f"{name}: must be >= {minimum}, got {number}")
    return number


def _sysctl(key, value):
    return f"sudo sysctl -q -w {shlex.quote(f'{key}={value}')}"


def render_udp_conntrack(value):
    mode = str(value).strip().lower()
    if _is_default(mode):
        return []
    if mode == "ufw-off":
        return ["sudo ufw --force disable"]
    if mode == "notrack":
        return [f"sudo iptables -t raw -I {rule}" for rule in NOTRACK_RULES]
    if mode == "conntrack-raised":
        return [
            "sudo modprobe nf_conntrack",
            _sysctl("net.netfilter.nf_conntrack_max", RAISED_CONNTRACK_MAX),
            f"echo {RAISED_CONNTRACK_HASHSIZE} | sudo tee {CONNTRACK_HASHSIZE} >/dev/null",
        ]
    raise SystemConfigError(
        f"udp-conntrack: expected one of {DEFAULT}, ufw-off, notrack, "
        f"conntrack-raised, got {value!r}"
    )


def render_rmem(value):
    if _is_default(value):
        return []
    size = _as_int("rmem", value, minimum=1)
    return [_sysctl("net.core.rmem_max", size),
            _sysctl("net.core.rmem_default", size)]


def render_netdev_max_backlog(value):
    if _is_default(value):
        return []
    return [_sysctl("net.core.netdev_max_backlog",
                    _as_int("netdev-max-backlog", value, minimum=1))]


def render_netdev_budget(value):
    if _is_default(value):
        return []
    parts = str(value).split("/")
    if len(parts) != 2:
        raise SystemConfigError(
            f"netdev-budget: expected '<budget>/<budget_usecs>', got {value!r}"
        )
    budget = _as_int("netdev-budget", parts[0], minimum=1)
    usecs = _as_int("netdev-budget", parts[1], minimum=1)
    return [_sysctl("net.core.netdev_budget", budget),
            _sysctl("net.core.netdev_budget_usecs", usecs)]


def render_udp_mem(value):
    mode = str(value).strip().lower()
    if _is_default(mode):
        return []
    if mode != "raised":
        raise SystemConfigError(f"udp-mem: expected {DEFAULT} or raised, got {value!r}")
    # udp_mem is sized from RAM at boot, so scale the host's own value rather
    # than hard-coding page counts that may not fit this machine.
    scale_udp_mem = (
        "read a b c < /proc/sys/net/ipv4/udp_mem && "
        f"sudo sysctl -q -w \"net.ipv4.udp_mem=$((a*{UDP_MEM_SCALE})) "
        f"$((b*{UDP_MEM_SCALE})) $((c*{UDP_MEM_SCALE}))\""
    )
    return [_sysctl("net.ipv4.udp_rmem_min", RAISED_UDP_MIN),
            _sysctl("net.ipv4.udp_wmem_min", RAISED_UDP_MIN),
            scale_udp_mem]


def render_optmem_max(value):
    if _is_default(value):
        return []
    return [_sysctl("net.core.optmem_max", _as_int("optmem-max", value, minimum=1))]


def render_named_nofile(value):
    if _is_default(value):
        return []
    limit = _as_int("named-nofile", value, minimum=1)
    content = shlex.quote(f"[Service]\nLimitNOFILE={limit}\n")
    return [
        f"sudo mkdir -p $(dirname {NAMED_DROPIN})",
        f"printf %s {content} | sudo tee {NAMED_DROPIN} >/dev/null",
        "sudo systemctl daemon-reload",
    ]


RENDERERS = {
    "udp-conntrack": render_udp_conntrack,
    "rmem": render_rmem,
    "netdev-max-backlog": render_netdev_max_backlog,
    "netdev-budget": render_netdev_budget,
    "udp-mem": render_udp_mem,
    "optmem-max": render_optmem_max,
    "named-nofile": render_named_nofile,
}


def is_system_parameter(name):
    return name in RENDERERS


def render_commands(overrides):
    """Shell commands that apply ``overrides``. Raises SystemConfigError on a
    bad value before anything touches the host."""
    commands = []
    for name, value in overrides.items():
        renderer = RENDERERS.get(name)
        if renderer is None:
            raise SystemConfigError(f"no system renderer registered for '{name}'")
        commands.extend(renderer(value))
    return commands


def _sh(server, command, check=True, timeout=60):
    result = ssh_run(server, command, timeout=timeout, check=False)
    if check and result.returncode != 0:
        raise SystemConfigError(
            f"command failed on {server} (rc={result.returncode}): {command}\n"
            f"{(result.stderr or '').strip()}"
        )
    return result.stdout


def snapshot(server):
    """Record the host settings that restore() puts back.

    A sysctl that does not exist yet (nf_conntrack_max before the module
    loads) is recorded as None and left alone on restore.
    """
    script = "; ".join(
        f"printf '%s=%s\\n' {key} \"$(sysctl -n {key} 2>/dev/null)\""
        for key in SYSCTL_KEYS
    )
    sysctls = {}
    for line in _sh(server, script).splitlines():
        key, _, value = line.partition("=")
        if key in SYSCTL_KEYS:
            sysctls[key] = " ".join(value.split()) or None

    ufw_status = _sh(server, "sudo ufw status", check=False)
    hashsize = _sh(server, f"cat {CONNTRACK_HASHSIZE} 2>/dev/null", check=False).strip()

    snap = {
        "sysctls": sysctls,
        "ufw_active": "Status: active" in ufw_status,
        "conntrack_hashsize": hashsize or None,
    }
    log.info("System baseline on %s: %s", server, snap)
    return snap


def restore_commands(snap):
    commands = [
        f"while sudo iptables -t raw -D {rule} 2>/dev/null; do :; done"
        for rule in NOTRACK_RULES
    ]
    if snap["ufw_active"]:
        commands.append(
            "sudo ufw status | grep -q 'Status: active' || sudo ufw --force enable"
        )
    else:
        commands.append(
            "! sudo ufw status | grep -q 'Status: active' || sudo ufw --force disable"
        )
    for key, value in snap["sysctls"].items():
        if value is not None:
            commands.append(_sysctl(key, value))
    if snap["conntrack_hashsize"]:
        commands.append(
            f"echo {snap['conntrack_hashsize']} | sudo tee {CONNTRACK_HASHSIZE} >/dev/null"
        )
    commands.append(
        f"if [ -e {NAMED_DROPIN} ]; then sudo rm -f {NAMED_DROPIN} && "
        "sudo systemctl daemon-reload; fi"
    )
    return commands


def apply(server, overrides, dry_run=False):
    commands = render_commands(overrides)
    if dry_run:
        for command in commands:
            print(f"# {command}")
        return
    for command in commands:
        _sh(server, command)


def restore(server, snap, dry_run=False):
    log.info("Restoring system baseline on %s", server)
    commands = restore_commands(snap)
    if dry_run:
        for command in commands:
            print(f"# {command}")
        return
    for command in commands:
        _sh(server, command)
