#!/usr/bin/env bash
# config.sh — Global configuration defaults for DNS load testing benchmark
# Override any value via environment variables before sourcing this file.

# Resolve the directory containing this config file
CONFIG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- Connection ---
CLIENT_HOST="${CLIENT_HOST:-}"
SERVER_HOST="${SERVER_HOST:-}"
SSH_USER="${SSH_USER:-root}"
SSH_OPTS="${SSH_OPTS:--o StrictHostKeyChecking=no -o ConnectTimeout=10}"
REMOTE_WORK_DIR="${REMOTE_WORK_DIR:-/tmp/dns_benchmark}"

# --- Resolver ---
RESOLVER="${RESOLVER:-}"
RESOLVER_PORT="${RESOLVER_PORT:-53}"

# --- Test Parameters ---
THREADS="${THREADS:-20}"
PORTS_PER_THREAD="${PORTS_PER_THREAD:-30}"
TIMEOUT="${TIMEOUT:-1}"
SUBNET="${SUBNET:-10.0.0.0/10}"
QPS_THRESHOLD_WAIT="${QPS_THRESHOLD_WAIT:-0}"
DELAY_BETWEEN_BURSTS="${DELAY_BETWEEN_BURSTS:-10000000}"  # 10ms in nanoseconds
RUNTIME="${RUNTIME:-10}"
PAUSE_BETWEEN_RUNS="${PAUSE_BETWEEN_RUNS:-5}"

# --- dns_responder ---
DNS_RESPONDER_MARGIN="${DNS_RESPONDER_MARGIN:-5}"
DNS_RESPONDER_STARTUP_WAIT="${DNS_RESPONDER_STARTUP_WAIT:-1}"
DNS_RESPONDER_SHUTDOWN_WAIT="${DNS_RESPONDER_SHUTDOWN_WAIT:-5}"
DNS_RESPONDER_INTERFACE="${DNS_RESPONDER_INTERFACE:-}"
DNS_RESPONDER_BIN="${DNS_RESPONDER_BIN:-dns_responder}"

# --- Script 1: Maximum Throughput ---
START_QPS="${START_QPS:-200000}"
QPS_STEP="${QPS_STEP:-10000}"
MAX_QPS="${MAX_QPS:-3000000}"

# --- Script 2: QPS Accuracy ---
ACCURACY_MIN_QPS="${ACCURACY_MIN_QPS:-100000}"
ACCURACY_MAX_QPS="${ACCURACY_MAX_QPS:-2000000}"
ACCURACY_STEP="${ACCURACY_STEP:-50000}"
TRIALS="${TRIALS:-10}"

# --- Script 3: Load Generator Impact ---
DNS_CONFIGS="${DNS_CONFIGS:-bind-resolver bind-ns powerdns-resolver powerdns-ns knot-resolver knot-ns nsd-ns unbound-resolver}"
IMPACT_MIN_QPS="${IMPACT_MIN_QPS:-100000}"
IMPACT_MAX_QPS="${IMPACT_MAX_QPS:-1000000}"
IMPACT_QPS_STEP="${IMPACT_QPS_STEP:-100000}"
IMPACT_TRIALS="${IMPACT_TRIALS:-10}"

# --- DNS Server Management ---
START_DNS_SCRIPT="${START_DNS_SCRIPT:-${CONFIG_DIR}/start_dns_service.sh}"
STOP_DNS_SCRIPT="${STOP_DNS_SCRIPT:-${CONFIG_DIR}/stop_dns_service.sh}"

# --- Tool Selection ---
ALL_TOOLS="dnsperf dnsperf-workbench-slice dnsperf-workbench-lencse dnspyre dnspyre-dnsworkbench dns64perfpp dns64perfpp-workbench kxdpgun"
ENABLED_TOOLS="${ENABLED_TOOLS:-$ALL_TOOLS}"

# --- Interface (for kxdpgun) ---
KXDPGUN_INTERFACE="${KXDPGUN_INTERFACE:-eth0}"

# --- Input Files ---
DNSPERF_INPUT="${DNSPERF_INPUT:-${CONFIG_DIR}/dnsperf_input}"
DNSPYRE_INPUT="${DNSPYRE_INPUT:-${CONFIG_DIR}/dnspyre_input}"

# --- Output ---
RESULTS_DIR="${RESULTS_DIR:-${CONFIG_DIR}/results}"
