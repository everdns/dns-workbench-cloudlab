import logging
import subprocess

log = logging.getLogger(__name__)


def is_local(host):
    """Check if host refers to the local machine."""
    return host in ("localhost", "127.0.0.1", "::1")


def ssh_run(host, command, timeout=None, check=False):
    """Run a command on a remote host via SSH, or locally if host is localhost.

    Returns subprocess.CompletedProcess. On timeout, kills the process (and the
    remote process if over SSH), then re-raises subprocess.TimeoutExpired with
    any partial stdout/stderr captured.
    """
    if is_local(host):
        log.debug("Local exec: %s", command)
        proc = subprocess.Popen(
            command, shell=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            log.warning("Local command timed out, killed: %s", command)
            raise subprocess.TimeoutExpired(
                command, timeout, output=stdout, stderr=stderr,
            )
        return subprocess.CompletedProcess(
            command, proc.returncode, stdout, stderr,
        )
    else:
        log.debug("SSH exec on %s: %s", host, command)
        ssh_cmd = [
            "ssh", "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=accept-new", host, command,
        ]
        proc = subprocess.Popen(
            ssh_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            log.warning("SSH command timed out on %s, killing remote process: %s",
                        host, command)
            # Kill the remote process — extract binary name from command
            binary = command.split()[0]
            try:
                subprocess.run(
                    ["ssh", "-o", "BatchMode=yes",
                     "-o", "StrictHostKeyChecking=accept-new",
                     host, f"pkill -f {binary}"],
                    timeout=10, capture_output=True, text=True,
                )
            except Exception:
                log.warning("pkill failed, trying killall for %s on %s",
                            binary, host)
                try:
                    subprocess.run(
                        ["ssh", "-o", "BatchMode=yes",
                         "-o", "StrictHostKeyChecking=accept-new",
                         host, f"killall -9 {binary}"],
                        timeout=10, capture_output=True, text=True,
                    )
                except Exception:
                    log.error("Could not kill %s on %s", binary, host)
            raise subprocess.TimeoutExpired(
                command, timeout, output=stdout, stderr=stderr,
            )
        result = subprocess.CompletedProcess(
            ssh_cmd, proc.returncode, stdout, stderr,
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
