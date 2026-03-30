import logging
import os
import time

from benchmark.remote import is_local, scp_first_last_lines, scp_from, ssh_run, ssh_run_background

log = logging.getLogger(__name__)


def start_dns_responder(config, duration, output_file="/tmp/dns_responder_output.txt",
                        timestamps=False,
                        timestamps_file=None,
                        recieve_only=False):
    """Start dns_responder on the server host.

    Args:
        config: global config dict
        duration: how long to run in seconds
        output_file: remote path for output stats
        timestamps: whether to collect min max timestamps (for accuracy testing)
        timestamps_file: remote path for per-packet timestamps (optional)

    Returns:
        subprocess.Popen handle
    """
    server = config["hosts"]["server"]
    interface = config["server_interface"]
    responder_cfg = config.get("dns_responder", {})
    binary = responder_cfg.get("path", "dns_responder")
    xdp_prog = responder_cfg.get("xdp_prog", "")

    cmd_parts = [
        f"sudo {binary}",
        f"-i {interface}",
        f"-d {duration}",
        f"-o {output_file}",
    ]
    if xdp_prog:
        cmd_parts.append(f"-x {xdp_prog}")
    if timestamps:
        cmd_parts.append("-T")
    if recieve_only:
        cmd_parts.append("-C")
    if timestamps_file:
        cmd_parts.append(f"-t {timestamps_file}")

    cmd = " ".join(cmd_parts)
    log.info("Starting dns_responder on %s: %s", server, cmd)

    if config.get("dry_run"):
        log.info("[DRY RUN] Would execute: ssh %s '%s'", server, cmd)
        return None

    proc = ssh_run_background(server, cmd)
    return proc


def wait_dns_responder(proc, timeout=None):
    """Wait for dns_responder process to finish.

    Returns (stdout, stderr) tuple.
    """
    if proc is None:
        return "", ""
    stdout, stderr = proc.communicate(timeout=timeout)
    return stdout, stderr


def collect_dns_responder_output(config, remote_output_file, local_dir,
                                  remote_timestamps_file=None, timestamps_lines=None):
    """SCP dns_responder output (and optionally timestamps) back to local host.

    Returns (local_output_path, local_timestamps_path or None).
    """
    server = config["hosts"]["server"]
    os.makedirs(local_dir, exist_ok=True)

    local_output = os.path.join(local_dir, os.path.basename(remote_output_file))
    scp_from(server, remote_output_file, local_output)

    local_ts = None
    if remote_timestamps_file:
        local_ts = os.path.join(local_dir, os.path.basename(remote_timestamps_file))
        if timestamps_lines is None:
            scp_from(server, remote_timestamps_file, local_ts)
        else:
            scp_first_last_lines(server, remote_timestamps_file, local_ts, num_lines=5)

    return local_output, local_ts


def run_dns_responder_session(config, timestamps=False, timestamps_file=False, recieve_only=False):
    """Convenience: start dns_responder, return context for a test run.

    Returns a dict with keys: proc, output_file, timestamps_file, duration.
    """
    runtime = config["runtime"]
    margin = config["dns_responder_margin"]
    duration = runtime + margin*2

    output_file = "/tmp/dns_responder_output.txt"
    ts_file = "/tmp/dns_responder_timestamps.txt" if timestamps_file else None

    proc = start_dns_responder(config, duration, output_file, timestamps, ts_file, recieve_only)

    # Wait for dns_responder to initialize
    time.sleep(margin)

    return {
        "proc": proc,
        "output_file": output_file,
        "timestamps_file": ts_file,
        "duration": duration,
    }
