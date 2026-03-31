#!/usr/bin/env bash

set -euo pipefail

OUT_FILE=${1:-"dns_benchmark_env_$(date +%Y%m%d_%H%M%S).txt"}

exec > >(tee "$OUT_FILE") 2>&1

echo "=== DNS Benchmark Environment Capture ==="
echo "Timestamp: $(date)"
echo

# -------------------------------
# 🖥️ System Info
# -------------------------------
echo "=== SYSTEM ==="
uname -a
echo

echo "--- CPU ---"
lscpu || true
echo

echo "--- Memory ---"
free -h
echo
cat /proc/meminfo | head -20
echo

echo "--- NUMA ---"
numactl --hardware 2>/dev/null || echo "numactl not installed"
echo

# -------------------------------
# 💾 Storage
# -------------------------------
echo "=== STORAGE ==="
lsblk
echo

# -------------------------------
# 🌐 Network Interfaces
# -------------------------------
echo "=== NETWORK INTERFACES ==="
ip addr
echo

echo "=== NIC DETAILS ==="
for iface in $(ls /sys/class/net | grep -v lo); do
    echo "--- Interface: $iface ---"
    
    ethtool "$iface" 2>/dev/null || true
    echo

    echo "Driver:"
    ethtool -i "$iface" 2>/dev/null || true
    echo

    echo "Offloads:"
    ethtool -k "$iface" 2>/dev/null || true
    echo

    echo "Ring buffer:"
    ethtool -g "$iface" 2>/dev/null || true
    echo

    echo "Channels:"
    ethtool -l "$iface" 2>/dev/null || true
    echo

    echo "Statistics:"
    ethtool -S "$iface" 2>/dev/null | head -50 || true
    echo
done

# -------------------------------
# 🔥 IRQ / CPU Affinity
# -------------------------------
echo "=== IRQ AFFINITY ==="
cat /proc/interrupts | grep -i eth || true
echo

echo "irqbalance status:"
systemctl is-active irqbalance 2>/dev/null || echo "unknown"
echo

# -------------------------------
# ⚙️ Kernel + Sysctl
# -------------------------------
echo "=== SYSCTL NETWORK SETTINGS ==="
sysctl -a 2>/dev/null | grep -E "net.core|net.ipv4.udp|net.ipv4.ip_local_port_range" || true
echo

echo "=== FILE DESCRIPTORS ==="
ulimit -n
echo

# -------------------------------
# 🔐 Firewall / Conntrack
# -------------------------------
echo "=== FIREWALL ==="
iptables -L -v -n 2>/dev/null || true
nft list ruleset 2>/dev/null || true
echo

echo "=== CONNTRACK ==="
sysctl net.netfilter.nf_conntrack_max 2>/dev/null || true
echo

# -------------------------------
# 🧠 DNS Software Detection
# -------------------------------
echo "=== DNS SOFTWARE ==="

for bin in named unbound knotd pdns_server; do
    if command -v $bin >/dev/null 2>&1; then
        echo "--- Found: $bin ---"
        $bin -V 2>/dev/null || $bin --version 2>/dev/null || true
        echo
    fi
done

# -------------------------------
# 📊 Runtime Stats Snapshot
# -------------------------------
echo "=== RUNTIME STATS ==="

echo "--- CPU Usage ---"
top -b -n1 | head -20
echo

echo "--- Netstat UDP ---"
netstat -su || true
echo

echo "--- Socket Summary ---"
ss -s
echo

# -------------------------------
# 🌍 Environment
# -------------------------------
echo "=== ENVIRONMENT ==="

echo "Virtualization:"
systemd-detect-virt || true
echo

echo "Uptime:"
uptime
echo

echo "Loaded modules (network relevant):"
lsmod | grep -E "ixgbe|i40e|mlx|ena|virtio_net" || true
echo

echo "=== DONE ==="
echo "Saved to: $OUT_FILE"