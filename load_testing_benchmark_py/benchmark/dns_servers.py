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
            f"dig @{server} example.com A +time=2 +tries=1 +short",
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            log.info("DNS server at %s is ready", server)
            return True
        time.sleep(1)

    raise TimeoutError(
        f"DNS server at {server} not ready after {timeout}s"
    )
