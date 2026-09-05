"""A synthetic response surface, so the whole stack can be tested for free.

A single real evaluation costs 20-30 minutes of exclusive testbed time, which
makes it a hopeless way to debug a search loop, a budget guard, or a resume
path. This module supplies a cheap stand-in: a declared function from candidate
to QPS, with saturating curves, interaction terms, measurement noise, and a list
of parameter combinations that deliberately fail to start.

Two things make it more than a toy:

  * The noise is sized to the *measured* baseline noise floor, so a search that
    chases noise in simulation will chase it on the testbed too, and you find
    that out in seconds instead of a day.
  * ``Evaluator`` does not bypass the subprocess in simulation. It passes the
    surface value to the real ``max_sustainable_qps.py`` as ``--simulate-max-qps``,
    so the real script runs its real two-phase search and writes real artifacts.
    Everything except the remote execution stays under test.

The surface also gives an honest answer to "how many evaluations does each
optimizer need", because on a declared surface the true optimum is known.
"""

import math
import random

import yaml


class ResponseSurface:
    """A declared, reproducible mapping from candidate to max sustainable QPS."""

    def __init__(self, spec):
        self.base_qps = float(spec.get("base_qps", 600000))
        self.effects = spec.get("effects", {}) or {}
        self.interactions = spec.get("interactions", []) or []
        noise = spec.get("noise", {}) or {}
        self.noise_sigma_pct = float(noise.get("sigma_pct", 1.5))
        self.quantize_to = int(spec.get("quantize_to", 10000))
        self.ceiling = spec.get("ceiling")
        self.fail_to_start = spec.get("fail_to_start", []) or []
        self._rng = random.Random(noise.get("seed", 7))

    @classmethod
    def from_file(cls, path):
        with open(path) as f:
            return cls(yaml.safe_load(f) or {})

    # ------------------------------------------------------------------ effects

    def _effect(self, name, value):
        spec = self.effects.get(name)
        if spec is None:
            return 0.0
        if isinstance(spec, dict) and "curve" in spec:
            return self._curve(spec, value)
        if isinstance(spec, dict):
            # A plain lookup table for enums.
            return float(spec.get(str(value), spec.get(value, 0.0)))
        return 0.0

    @staticmethod
    def _curve(spec, value):
        curve = spec["curve"]
        gain = float(spec.get("gain", 0.0))
        value = float(value)
        if value <= 0:
            return 0.0
        if curve == "saturating":
            # Rises steeply to a knee, then flattens; over-provisioning past the
            # knee costs a little, the way extra worker threads eventually do.
            knee = float(spec.get("knee", 16))
            penalty = float(spec.get("penalty_beyond", 0.0))
            saturated = gain * (1.0 - math.exp(-value / max(knee / 2.0, 1e-9)))
            over = max(0.0, value - knee)
            return saturated + penalty * (over / max(knee, 1e-9))
        if curve == "log":
            ref = float(spec.get("ref", 1048576))
            return gain * math.log1p(value / ref) / math.log(2.0)
        if curve == "linear":
            ref = float(spec.get("ref", 1.0))
            return gain * value / ref
        return 0.0

    # ------------------------------------------------------------------- score

    def fails_to_start(self, canon):
        for pattern in self.fail_to_start:
            if all(canon.get(k) == v for k, v in pattern.items()):
                return True
        return False

    def score(self, canon, noisy=True):
        """QPS for a canonical candidate, quantized like the real metric."""
        total = self.base_qps
        for name, value in canon.items():
            total += self._effect(name, value)

        for entry in self.interactions:
            a, b, weight = entry[0], entry[1], float(entry[2])
            total += weight * math.sqrt(
                max(0.0, self._effect(a, canon.get(a)))
                * max(0.0, self._effect(b, canon.get(b)))
            )

        if noisy and self.noise_sigma_pct:
            total *= 1.0 + self._rng.gauss(0.0, self.noise_sigma_pct / 100.0)

        if self.ceiling:
            total = min(total, float(self.ceiling))

        total = max(0.0, total)
        # The real metric is quantized to min_qps_step, so differences smaller
        # than one step are invisible there and must be invisible here too.
        return int(round(total / self.quantize_to) * self.quantize_to)

    def true_optimum(self, candidates):
        """Noise-free best over an enumerated space -- the ground truth."""
        best, best_score = None, -1
        for canon in candidates:
            if self.fails_to_start(canon):
                continue
            score = self.score(canon, noisy=False)
            if score > best_score:
                best, best_score = canon, score
        return best, best_score


DEFAULT_SURFACE = {
    # Rough shape of what an authoritative BIND on this testbed might look like.
    # Replace the magnitudes with measured ones once Phase 5 calibration lands.
    "base_qps": 620000,
    "effects": {
        "querylog": {"yes": -90000, "no": 0},
        "minimal_responses": {"no": 0, "no-auth": 55000,
                              "no-auth-recursive": 52000, "yes": 60000},
        "answer_cookie": {"yes": 0, "no": 18000},
        "zone_statistics": {"full": -12000, "terse": 0, "none": 6000},
        "named_threads": {"curve": "saturating", "knee": 16,
                          "gain": 90000, "penalty_beyond": -4000},
        "netdev_max_backlog": {"curve": "log", "gain": 9000, "ref": 50000},
        "udp_receive_buffer": {"curve": "log", "gain": 12000, "ref": 1048576},
        "nic_rx_ring": {"curve": "log", "gain": 7000, "ref": 2048},
    },
    "interactions": [["minimal_responses", "named_threads", 0.15]],
    "noise": {"sigma_pct": 1.5, "seed": 7},
    "quantize_to": 10000,
    # Must be a schema-VALID candidate, or canonical() rejects it before the
    # applier is ever reached and the rollback path goes untested. (An earlier
    # version paired a huge udp_receive_buffer with the default rmem_max, which
    # the buffers_need_headroom constraint rules out.)
    "fail_to_start": [{"named_threads": 64}],
}
