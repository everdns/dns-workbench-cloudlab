"""Helpers for working with multiple load-generation hosts."""
import re


def get_clients(config):
    """Return the list of load-generation client hosts from config.

    Raises a clear error if the legacy single ``client`` key is present or if
    no ``clients`` list is configured.
    """
    hosts = config.get("hosts", {})
    if "client" in hosts and "clients" not in hosts:
        raise ValueError(
            "Config uses the legacy 'hosts.client' key. Replace it with a "
            "'hosts.clients' list, e.g.\n  hosts:\n    clients:\n      - localhost"
        )
    clients = hosts.get("clients")
    if not clients:
        raise ValueError("No load-generation hosts configured under 'hosts.clients'")
    if isinstance(clients, str):
        clients = [clients]
    return list(clients)


def split_qps(total_qps, n):
    """Split a target QPS evenly across ``n`` hosts.

    Returns a list of ``n`` integers that sum to ``total_qps``. Any remainder
    is distributed one-per-host to the first hosts, e.g.
    ``split_qps(100000, 3) -> [33334, 33333, 33333]``.
    """
    if n < 1:
        raise ValueError(f"Number of hosts must be >= 1, got {n}")
    base = total_qps // n
    remainder = total_qps % n
    return [base + (1 if i < remainder else 0) for i in range(n)]


def host_token(host):
    """Return a filesystem-safe token for a host (for raw output filenames)."""
    return re.sub(r"[@.:/]", "-", str(host))
