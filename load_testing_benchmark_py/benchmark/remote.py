import logging
import subprocess

log = logging.getLogger(__name__)


def is_local(host):
    """Check if host refers to the local machine."""
    return host in ("localhost", "127.0.0.1", "::1")


def ssh_run(host, command, timeout=None, check=False):
    """Run a command on a remote host via SSH, or locally if host is localhost.

    Returns subprocess.CompletedProcess.
    """
    if is_local(host):
        log.debug("Local exec: %s", command)
        return subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=timeout,
        )
    else:
        log.debug("SSH exec on %s: %s", host, command)
        result = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
             host, command],
            capture_output=True, text=True, timeout=timeout,
        )
        if check and result.returncode != 0:
            raise RuntimeError(
                f"SSH command failed on {host} (rc={result.returncode}): {command}\n"
                f"stderr: {result.stderr}"
            )
        return result


def ssh_run_background(host, command):
    """Start a command on a remote host in the background.

    Returns subprocess.Popen. The caller must manage the process lifecycle.
    """
    if is_local(host):
        log.debug("Local background exec: %s", command)
        return subprocess.Popen(
            command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True,
        )
    else:
        log.debug("SSH background exec on %s: %s", host, command)
        return subprocess.Popen(
            ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
             host, command],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )


def scp_from(host, remote_path, local_path):
    """Copy a file from remote host to local path."""
    if is_local(host):
        subprocess.run(["cp", remote_path, local_path], check=True)
    else:
        log.debug("SCP from %s:%s -> %s", host, remote_path, local_path)
        subprocess.run(
            ["scp", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
             f"{host}:{remote_path}", local_path],
            check=True, capture_output=True, text=True,
        )


def scp_to(host, local_path, remote_path):
    """Copy a file from local to remote host."""
    if is_local(host):
        subprocess.run(["cp", local_path, remote_path], check=True)
    else:
        log.debug("SCP to %s:%s <- %s", host, remote_path, local_path)
        subprocess.run(
            ["scp", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
             local_path, f"{host}:{remote_path}"],
            check=True, capture_output=True, text=True,
        )
