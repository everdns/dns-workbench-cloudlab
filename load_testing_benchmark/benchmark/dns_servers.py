import logging
import time

from benchmark.remote import ssh_run

log = logging.getLogger(__name__)


def start_dns_service(config, service_name):
    """Start a DNS service on the server host.

    Args:
        config: global config dict
        service_name: e.g. 'bind-resolver', 'powerdns-ns', etc.
    """
    server = config["hosts"]["server"]
    start_script = config["dns_services"]["start_script"]

    log.info("Starting DNS service '%s' on %s", service_name, server)
    result = ssh_run(server, f"{start_script} {service_name}", timeout=30)

    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to start {service_name}: {result.stderr}"
        )
    log.info("DNS service '%s' started", service_name)


def stop_dns_service(config, service_name=None):
    """Stop a DNS service on the server host.

    Args:
        config: global config dict
        service_name: specific service to stop, or None to stop all
    """
    server = config["hosts"]["server"]
    stop_script = config["dns_services"]["stop_script"]

    cmd = f"{stop_script} {service_name}" if service_name else stop_script
    log.info("Stopping DNS service%s on %s",
             f" '{service_name}'" if service_name else "s", server)

    result = ssh_run(server, cmd, timeout=30)
    if result.returncode != 0:
        log.warning("Stop command returned non-zero: %s", result.stderr)


def clear_dns_cache(config, service_name):
    """Clear the DNS resolver cache on the server host.

    Some underlying clear-cache scripts (e.g. unbound, pdns-recursor) restart
    the service, so callers should verify readiness afterwards.
    """
    server = config["hosts"]["server"]
    clear_script = config["dns_services"].get("clear_cache_script")
    if not clear_script:
        raise RuntimeError(
            "dns_services.clear_cache_script not configured"
        )

    log.info("Clearing cache for '%s' on %s", service_name, server)
    result = ssh_run(server, f"{clear_script} {service_name}", timeout=30)
    if result.returncode != 0:
        log.warning(
            "Clear cache for %s returned rc=%d: %s",
            service_name, result.returncode, result.stderr.strip(),
        )


def ensure_dns_running(config, service_name, timeout=30):
    """Ensure the DNS service is up; (re)start it if it is not responding."""
    try:
        wait_for_dns_ready(config, timeout=timeout)
        return
    except TimeoutError:
        log.warning(
            "DNS service '%s' not responding after cache clear; restarting",
            service_name,
        )
    start_dns_service(config, service_name)
    wait_for_dns_ready(config, timeout=timeout)


def warmup_dns_cache(config, qps=None, timeout=None):
    """Pre-populate the resolver cache via a single pass through the dnsperf input file.

    Uses a moderate QPS so the resolver can resolve each unique query
    recursively without dropping requests.
    """
    client = config["hosts"]["client"]
    server = config["hosts"]["server"]
    input_file = config["input_files"]["dnsperf"]
    query_timeout = config.get("timeout", 5)

    s3 = config.get("script3", {})
    warmup_qps = qps if qps is not None else int(s3.get("warmup_qps", 10000))
    warmup_timeout = timeout if timeout is not None else int(s3.get("warmup_timeout", 600))

    cmd = (
        f"dnsperf -s {server} -d {input_file} -n 1"
        f" -Q {warmup_qps} -c 1 -T 1 -t {query_timeout}"
        f" -O suppress=timeout -O qps-threshold-wait=0"
    )
    log.info("Warming up cache on %s via dnsperf (Q=%d, one pass)...", server, warmup_qps)
    result = ssh_run(client, cmd, timeout=warmup_timeout)
    if result.returncode != 0:
        log.warning(
            "Cache warmup returned rc=%d: %s",
            result.returncode, result.stderr.strip(),
        )
    else:
        log.info("Cache warmup complete")


def wait_for_dns_ready(config, timeout=30):
    """Poll until the DNS server on the resolver IP responds to queries.

    Uses dig to send a test query.
    """
    server = config["hosts"]["server"]
    client = config["hosts"]["client"]

    log.info("Waiting for DNS server at %s to be ready...", server)
    deadline = time.time() + timeout

    while time.time() < deadline:
        result = ssh_run(
            client,
            f"dig @{server}",
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            log.info("DNS server at %s is ready", server)
            return True
        time.sleep(1)

    raise TimeoutError(
        f"DNS server at {server} not ready after {timeout}s"
    )
