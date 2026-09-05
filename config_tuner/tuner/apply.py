"""Ship a candidate to the name server and have it applied, or fail cleanly.

The workstation never sends configuration text. It sends the typed candidate
dict, and the name server renders it as root from its own copy of the schema --
so a compromised or merely out-of-date workstation cannot hand the host a config
that ``named-checkconf`` happens to accept. (checkconf is perfectly content with
``recursion yes; allow-transfer { any; };``.)

The transport is a staged file rather than piped stdin because
``benchmark/remote.py:ssh_run`` has no stdin channel, and the staging path is
fixed on the host so it cannot be influenced from here.
"""

import json
import logging
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LTB = os.path.join(REPO, "load_testing_benchmark")
if LTB not in sys.path:
    sys.path.insert(0, LTB)

from benchmark.remote import scp_to, ssh_run  # noqa: E402

from tuner import schema as schema_mod  # noqa: E402

log = logging.getLogger(__name__)

# Mirrors the exit-code contract at the top of apply_candidate.sh.
EXIT_MEANING = {
    0: ("ok", "applied, healthy, and conformant"),
    1: ("apply_failed", "helper refused to run (not root, or bad usage)"),
    2: ("apply_failed", "invalid candidate; the name server changed nothing"),
    3: ("apply_failed", "named-checkconf rejected the rendered config"),
    4: ("apply_failed", "sysctl, ethtool, or systemctl restart failed"),
    5: ("apply_failed", "health probe failed after restart"),
    6: ("fatal", "ROLLBACK FAILED -- the host is in an unknown state"),
    7: ("apply_failed", "conformance probe failed; the config answered incorrectly"),
    75: ("host_busy", "another apply holds the host lock"),
}


class Applier:
    """Applies candidates on a real name server over SSH."""

    def __init__(self, server, staging_remote="/var/lib/dns-tuner/staging/candidate.json",
                 apply_cmd="sudo /usr/local/sbin/dns_tuner_apply", timeout=300):
        self.server = server
        self.staging_remote = staging_remote
        self.apply_cmd = apply_cmd
        self.timeout = timeout

    def _run(self, action):
        result = ssh_run(self.server, f"{self.apply_cmd} {action}", timeout=self.timeout)
        return self._interpret(result.returncode, result.stdout, result.stderr)

    @staticmethod
    def _interpret(returncode, stdout, stderr):
        status, description = EXIT_MEANING.get(
            returncode, ("apply_failed", f"unexpected exit code {returncode}")
        )
        out = {
            "status": "ok" if returncode == 0 else status,
            "exit_code": returncode,
            "description": description,
            # Bounded so a pathological failure cannot flood the ledger or a
            # tool result, and trimmed of paths that might suggest a shell.
            "stderr_tail": (stderr or "").strip()[-2048:],
        }
        for line in reversed((stdout or "").strip().splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    out.update({k: v for k, v in json.loads(line).items()
                                if k not in ("status",)})
                except json.JSONDecodeError:
                    pass
                break
        return out

    def apply(self, schema, params, facts=None, stop_after=True):
        """Validate locally, stage the dict, and apply it on the host.

        Local validation is a courtesy that saves an SSH round trip; the name
        server re-validates against its own schema regardless, and that check is
        the one that counts.
        """
        try:
            cid = schema_mod.candidate_id(schema, params, facts)
            canon = schema_mod.canonical(schema, params, facts)
        except schema_mod.SchemaError as e:
            return {"status": "invalid_candidate", "exit_code": 2,
                    "description": str(e), "stderr_tail": "", "candidate_id": None}

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(dict(canon), f, sort_keys=True)
            local_path = f.name
        try:
            scp_to(self.server, local_path, self.staging_remote)
        except subprocess.CalledProcessError as e:
            return {"status": "infra_error", "exit_code": None,
                    "description": f"could not stage the candidate: {e}",
                    "stderr_tail": (e.stderr or "")[-2048:], "candidate_id": cid}
        finally:
            os.unlink(local_path)

        action = "apply-and-stop" if stop_after else "apply"
        log.info("Applying candidate %s on %s (%s)", cid, self.server, action)
        result = self._run(action)
        result.setdefault("candidate_id", cid)
        if result["status"] == "ok":
            log.info("Candidate %s applied and verified", cid)
        else:
            log.warning("Apply of %s failed (%s): %s",
                        cid, result["exit_code"], result["description"])
        return result

    def baseline(self):
        """Restore the pristine repo config. Run at the start and end of a run."""
        log.info("Restoring the baseline config on %s", self.server)
        return self._run("baseline")

    def status(self):
        result = ssh_run(self.server, f"{self.apply_cmd} status", timeout=60)
        try:
            return json.loads(result.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            return {"tuner_managed": None, "error": (result.stderr or "").strip()[-512:]}

    def fetch_facts(self, local_path, remote="/usr/local/lib/dns-tuner/facts.json"):
        """Copy the host's facts.json here so client-side clamping matches."""
        from benchmark.remote import scp_from
        try:
            scp_from(self.server, remote, local_path)
            return schema_mod.load_facts(local_path)
        except subprocess.CalledProcessError as e:
            log.warning("Could not fetch host facts: %s", e)
            return None


class SimulatedApplier:
    """Stand-in for offline runs: validates, then pretends the apply succeeded.

    It still enforces the schema, so a driver bug that proposes an invalid
    candidate surfaces in simulation rather than on the testbed. ``fail_on`` is
    a list of partial parameter dicts that should report an apply failure, so
    the rollback and consecutive-failure paths get exercised too.
    """

    def __init__(self, fail_on=None):
        self.fail_on = fail_on or []
        self.applied = []

    def apply(self, schema, params, facts=None, stop_after=True):
        try:
            canon = schema_mod.canonical(schema, params, facts)
            cid = schema_mod.candidate_id(schema, canon, facts)
        except schema_mod.SchemaError as e:
            return {"status": "invalid_candidate", "exit_code": 2,
                    "description": str(e), "stderr_tail": "", "candidate_id": None}

        for pattern in self.fail_on:
            if all(canon.get(k) == v for k, v in pattern.items()):
                return {"status": "apply_failed", "exit_code": 4,
                        "description": "simulated: this candidate does not start",
                        "stderr_tail": "", "candidate_id": cid}

        self.applied.append(dict(canon))
        return {"status": "ok", "exit_code": 0, "description": "simulated apply",
                "stderr_tail": "", "candidate_id": cid, "conformance": "pass"}

    def baseline(self):
        return {"status": "ok", "exit_code": 0, "description": "simulated baseline"}

    def status(self):
        return {"tuner_managed": True, "candidate_id": "-", "named_active": False,
                "simulated": True}

    def fetch_facts(self, local_path, remote=None):
        return None
