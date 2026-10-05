"""Tools the Claude agent uses to run agent_search.py.

The agent controls the search. It decides what to measure, when to repeat a
measurement to check noise, when to look at the name server, and when to stop.
SearchTools holds the tools it calls:

  measure_configuration  install, measure, restore and record one configuration
  get_results            every measurement so far
  get_run_details        per-level and per-trial data of one measurement
  inspect_server         read-only diagnostics on the name server
  finish                 end the search with a report

The tools only enforce the rules. Every value must be inside the parameter's
bounds, and it must render through the same code that bind_config.py and
system_config.py use to change the server. The run is limited to
``max_configs`` measurements. Between measurements the name server always holds
its base configuration. A bad call returns an error for the agent to fix; it
never reaches the server.

Search space: each entry in the config's ``parameters`` list may carry an
``agent:`` block describing what the agent may propose.

    - name: rmem
      values: [default, 16777216]            # shown to the agent as hints
      agent: {type: int, min: 212992, max: 268435456, allow_default: true}
    - name: netdev-budget
      values: [default, "600/4000"]
      agent: {type: pair, min: [1, 1], max: [100000, 1000000], allow_default: true}

``type`` is one of int, pair ("<a>/<b>", each part bounded), or enum (the
default when there is no ``agent:`` block; choices are ``values`` unless
``choices`` is given). ``allow_default`` defaults to whether "default" is in
``values``.
"""
import csv
import inspect
import json
import logging
import os
import subprocess
import threading
import time

import bind_config
import system_config
from bind_config import BindConfigError
from max_sustainable_qps import ssh_run
from search import point_id, row_qps, split_overrides
from system_config import SystemConfigError

log = logging.getLogger("agent_tools")

DEFAULT = system_config.DEFAULT

# Per-row fields shown to the agent, besides the parameter values.
RESULT_FIELDS = [
    "status", "error", "max_qps_passed", "max_qps_tested",
    "hit_max_qps_ceiling", "min_qps_fidelity_pct", "repeat_of", "hypothesis",
]
MAX_TEXT = 600
MAX_TOOL_OUTPUT = 12000
PROBE_TIMEOUT = 60

SYSTEM_PROMPT = """\
You are running a performance-tuning campaign on an authoritative DNS name \
server (BIND plus Linux host network settings). The goal is the highest max \
sustainable QPS: the highest query rate at which the server still answers \
nearly every query, as measured by a load generator on separate client \
machines. You are in charge of the whole search: what to measure, in what \
order, when to repeat a measurement, when to look at the server, and when to \
stop.

Your tools:
- measure_configuration installs a configuration on the real server, measures \
it (this takes several minutes), restores the base configuration, and returns \
the result. Each call uses one measurement from a small fixed budget, even if \
it fails.
- get_results lists every measurement so far. get_run_details shows the \
per-level answer rates of one measurement. This is where you can see how \
sharply the server falls off.
- inspect_server runs read-only diagnostics on the name server: counters, \
sysctls, NIC and CPU layout. The server holds its base configuration between \
measurements, and most counters are cumulative since boot. Compare them \
before and after a measurement to see where packets were dropped.
- finish ends the campaign with your report.

How to work:
- Form hypotheses about what limits throughput (socket buffers, packet drops \
in conntrack or the backlog, NAPI budget, file descriptors, EDNS size). Test \
first the ones that could matter most, and follow up on what the results \
show. Parameters interact, so changing several at once is fine when you have \
a reason to.
- Runs are noisy. Before you build on a difference of a few percent, or \
before you recommend a winner, consider spending a measurement to repeat it \
(repeat=true).
- The first listed value of every parameter is the starting configuration. \
Measuring it gives you a baseline to compare against.

Reading a result:
- max_qps_passed is the score. Higher is better.
- hit_max_qps_ceiling=True means the search reached its QPS ceiling. The \
score is then a lower bound, not the server's limit.
- A min_qps_fidelity_pct below the configured threshold means the load \
generator could not send the target rate. That score reflects the client, not \
the server.
- status config_failed means the configuration could not be applied (see \
error). timeout or failed means the measurement did not finish.

When you have used the budget, or you are confident more measurements cannot \
beat the best result, call finish. Recommend the configuration you trust most, \
say how sure you are and why, and explain what you learned about what limits \
this server. Call finish exactly once, then end your turn."""


class AgentError(Exception):
    pass


# ---------------------------------------------------------------------------
# Search space
# ---------------------------------------------------------------------------

def _is_default(value):
    return isinstance(value, str) and value.strip().lower() == DEFAULT


def build_search_space(parameters):
    """Per-parameter specs from the config's ``parameters`` list."""
    space = []
    for p in parameters:
        agent = dict(p.get("agent") or {})
        kind = agent.get("type", "enum")
        spec = {
            "name": p["name"],
            "type": kind,
            "hints": list(p.get("values") or []),
            "description": agent.get("description", ""),
        }
        if kind == "enum":
            spec["choices"] = list(agent.get("choices") or p["values"])
        elif kind in ("int", "pair"):
            if "min" not in agent or "max" not in agent:
                raise ValueError(f"parameter '{p['name']}': agent type {kind} "
                                 "needs min and max")
            spec["min"], spec["max"] = agent["min"], agent["max"]
            spec["allow_default"] = agent.get(
                "allow_default", any(_is_default(v) for v in spec["hints"]))
            if kind == "pair" and not (len(spec["min"]) == len(spec["max"]) == 2):
                raise ValueError(f"parameter '{p['name']}': pair min/max must "
                                 "be two-element lists")
        else:
            raise ValueError(f"parameter '{p['name']}': unknown agent type {kind!r}")
        space.append(spec)
    return space


def _value_schema(spec):
    if spec["type"] == "enum":
        return {"type": "string", "enum": [str(c) for c in spec["choices"]]}
    base = {"type": "integer"} if spec["type"] == "int" else {"type": "string"}
    if spec.get("allow_default"):
        return {"anyOf": [base, {"type": "string", "enum": [DEFAULT]}]}
    return base


def configuration_schema(space):
    return {
        "type": "object",
        "properties": {spec["name"]: _value_schema(spec) for spec in space},
        "required": [spec["name"] for spec in space],
        "additionalProperties": False,
    }


def describe_space(space):
    """The search space as the agent sees it."""
    out = []
    for spec in space:
        entry = {"name": spec["name"], "type": spec["type"]}
        if spec["description"]:
            entry["description"] = spec["description"]
        if spec["type"] == "enum":
            entry["choices"] = spec["choices"]
        else:
            entry["min"], entry["max"] = spec["min"], spec["max"]
            entry["default_allowed"] = spec["allow_default"]
            if spec["type"] == "pair":
                entry["format"] = "'<first>/<second>', each part within its min/max"
            entry["suggested_values"] = spec["hints"]
        out.append(entry)
    return out


def _check_int(name, value, low, high):
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name}: expected an integer, got {value!r}")
    if isinstance(value, float) and value != number:
        raise ValueError(f"{name}: expected an integer, got {value!r}")
    if not low <= number <= high:
        raise ValueError(f"{name}: {number} is outside [{low}, {high}]")
    return number


def _normalize(spec, value):
    name = spec["name"]
    if spec["type"] == "enum":
        for choice in spec["choices"]:
            if str(choice) == str(value):
                return choice
        raise ValueError(f"{name}: {value!r} is not one of {spec['choices']}")
    if _is_default(value):
        if not spec["allow_default"]:
            raise ValueError(f"{name}: '{DEFAULT}' is not allowed")
        return DEFAULT
    if spec["type"] == "int":
        return _check_int(name, value, spec["min"], spec["max"])
    parts = str(value).split("/")
    if len(parts) != 2:
        raise ValueError(f"{name}: expected '<first>/<second>', got {value!r}")
    first, second = (_check_int(name, part.strip(), low, high)
                     for part, low, high in zip(parts, spec["min"], spec["max"]))
    return f"{first}/{second}"


def validate_configuration(space, configuration):
    """Return (normalized configuration, errors). The configuration is ordered
    like the search space; errors is empty when it is usable."""
    if not isinstance(configuration, dict):
        return {}, ["configuration must be an object mapping parameter names to values"]
    errors = []
    normalized = {}
    names = {spec["name"] for spec in space}
    for extra in sorted(set(configuration) - names):
        errors.append(f"{extra}: not a parameter of this search")
    for spec in space:
        name = spec["name"]
        if name not in configuration:
            errors.append(f"{name}: missing")
            continue
        try:
            value = _normalize(spec, configuration[name])
        except ValueError as e:
            errors.append(str(e))
            continue
        # Dry render: the same code that builds the real change on the server.
        try:
            if system_config.is_system_parameter(name):
                system_config.render_commands({name: value})
            else:
                bind_config.render_statement(name, value)
        except (BindConfigError, SystemConfigError) as e:
            errors.append(str(e))
            continue
        normalized[name] = value
    return normalized, errors


# ---------------------------------------------------------------------------
# Server diagnostics
#
# Fixed, read-only commands. The agent picks one by name; it cannot run
# anything else on the server.
# ---------------------------------------------------------------------------

SERVER_PROBES = {
    "udp_counters": (
        "UDP and IP counters since boot (receive/send buffer errors, "
        "no-port, in-errors)",
        "nstat -az 2>/dev/null | grep -E '^(Udp|Ip)' || cat /proc/net/snmp",
    ),
    "softnet_stat": (
        "Per-CPU /proc/net/softnet_stat (hex columns: processed, dropped "
        "(backlog full), time_squeeze (NAPI budget exhausted))",
        "cat /proc/net/softnet_stat",
    ),
    "sysctls": (
        "Current net.core, UDP and conntrack sysctls",
        "sysctl -a 2>/dev/null | grep -E '^(net\\.core\\.|net\\.ipv4\\.udp|"
        "net\\.netfilter\\.nf_conntrack_(max|count|buckets))'",
    ),
    "firewall": (
        "ufw status, iptables raw table, conntrack usage",
        "sudo ufw status verbose; echo; sudo iptables -t raw -S; echo; "
        "sudo sysctl net.netfilter.nf_conntrack_count "
        "net.netfilter.nf_conntrack_max 2>&1",
    ),
    "nic": (
        "Interfaces with packet counters, channel/ring sizes, and nonzero "
        "drop/error NIC statistics",
        "ip -s link; for i in $(ls /sys/class/net | grep -v '^lo$'); do "
        "echo \"== $i\"; ethtool -l $i 2>/dev/null; ethtool -g $i 2>/dev/null; "
        "sudo ethtool -S $i 2>/dev/null | grep -Ei 'drop|miss|err|fifo|no_buf' "
        "| grep -Ev ': 0$'; done",
    ),
    "cpu": (
        "CPU topology, load, and how NIC interrupts are spread over CPUs",
        "lscpu; echo; cat /proc/loadavg; echo; "
        "grep -E 'CPU|eth|eno|ens|mlx|ice|i40e' /proc/interrupts | cut -c1-400",
    ),
    "named": (
        "BIND version, named's service limits, and the installed "
        f"{bind_config.INSTALLED_OPTIONS_PATH}",
        "named -V 2>&1 | head -5; systemctl show named -p LimitNOFILE "
        "-p ActiveState 2>&1; echo; "
        f"sudo cat {bind_config.INSTALLED_OPTIONS_PATH}",
    ),
    "memory": (
        "Memory and socket usage",
        "free -m; echo; cat /proc/net/sockstat",
    ),
}


# ---------------------------------------------------------------------------
# Transcript
# ---------------------------------------------------------------------------

class Transcript:
    """Appends one JSON object per event to a .jsonl file."""

    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()

    def record(self, event, **fields):
        if not self.path:
            return
        with self._lock:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "a") as f:
                f.write(json.dumps({"time": time.time(), "event": event, **fields},
                                   default=str) + "\n")


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def _clip(value, limit=MAX_TEXT):
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "..."


def _read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return list(csv.DictReader(f))


class SearchTools:
    """The tools the agent calls. Calls run one at a time, so the name
    server only ever sees one change at a time."""

    def __init__(self, space, evaluator, context, max_configs, transcript,
                 server, dry_run=False):
        self.space = space
        self.evaluator = evaluator
        self.context = context
        self.max_configs = max_configs
        self.transcript = transcript
        self.server = server
        self.dry_run = dry_run
        self.report = None
        self.dry_run_done = False
        # Set by the runner (e.g. a usage limit was reached): no further
        # measurements, the agent should write its report.
        self.stop_reason = None
        self._lock = threading.Lock()
        self._handlers = {
            "measure_configuration": self.measure_configuration,
            "get_results": self.get_results,
            "get_run_details": self.get_run_details,
            "inspect_server": self.inspect_server,
            "finish": self.finish,
        }

    # -- bookkeeping --------------------------------------------------------

    @property
    def done(self):
        """True once the agent has finished (or a dry run has shown its
        first configuration)."""
        return self.report is not None or self.dry_run_done

    def used(self):
        return len(self.evaluator.rows)

    def remaining(self):
        return max(0, self.max_configs - self.used())

    def best(self):
        """(point_id, qps) of the highest-scoring row, or None."""
        best = None
        for row in self.evaluator.rows.values():
            qps = row_qps(row)
            if qps is not None and (best is None or qps > best[1]):
                best = (row["point_id"], qps)
        return best

    def _row_view(self, row):
        entry = {"point_id": row["point_id"],
                 "configuration": {spec["name"]: row.get(spec["name"], "")
                                   for spec in self.space}}
        for field in RESULT_FIELDS:
            value = row.get(field, "")
            if value not in ("", None):
                entry[field] = _clip(value) if isinstance(value, str) else value
        return entry

    def _budget(self):
        best = self.best()
        return {"measurements_used": self.used(),
                "measurements_remaining": self.remaining(),
                "best_so_far": ({"point_id": best[0], "max_qps_passed": best[1]}
                                if best else None)}

    # -- tool definitions ---------------------------------------------------

    def definitions(self):
        """Tool definitions (name, description, input_schema)."""
        return [
            {
                "name": "measure_configuration",
                "description": (
                    "Install a configuration on the name server, measure its max "
                    "sustainable QPS (several minutes), restore the base "
                    "configuration, and return the result with its per-level "
                    "answer rates. Uses one measurement from the budget even if it "
                    "fails. Give every parameter. A configuration that was already "
                    "measured is rejected unless repeat=true, which measures it "
                    "again to gauge run-to-run noise."),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "configuration": configuration_schema(self.space),
                        "hypothesis": {
                            "type": "string",
                            "description": "What you expect this measurement to "
                                           "show, and why it is worth the budget"},
                        "repeat": {
                            "type": "boolean",
                            "description": "Measure an already-measured "
                                           "configuration again"},
                    },
                    "required": ["configuration", "hypothesis"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "get_results",
                "description": "Every measurement so far, with the remaining budget "
                               "and the best result.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "sort_by": {"type": "string", "enum": ["order", "qps"],
                                    "description": "order measured (default) or "
                                                   "max_qps_passed, best first"},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "get_run_details",
                "description": "Per-QPS-level results (passes, fails, average "
                               "answer rate) and per-trial results of one "
                               "measurement, by point_id.",
                "input_schema": {
                    "type": "object",
                    "properties": {"point_id": {"type": "string"}},
                    "required": ["point_id"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "inspect_server",
                "description": "Run a fixed read-only diagnostic on the name "
                               "server. Probes: " + "; ".join(
                                   f"{name}: {desc}"
                                   for name, (desc, _) in SERVER_PROBES.items()),
                "input_schema": {
                    "type": "object",
                    "properties": {"probe": {"type": "string",
                                             "enum": list(SERVER_PROBES)}},
                    "required": ["probe"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "finish",
                "description": "End the search. Call exactly once, when the budget "
                               "is used or further measurements cannot help.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "recommended_point_id": {
                            "type": "string",
                            "description": "point_id of the configuration you "
                                           "recommend, or empty if none worked"},
                        "summary": {
                            "type": "string",
                            "description": "Your report: the recommendation and "
                                           "how sure you are, what limits this "
                                           "server, and what you would try next"},
                    },
                    "required": ["recommended_point_id", "summary"],
                    "additionalProperties": False,
                },
            },
        ]

    def initial_prompt(self):
        rows = [self._row_view(r) for r in self.evaluator.rows.values()]
        previous = ("\n".join(json.dumps(r) for r in rows) if rows
                    else "(nothing measured yet)")
        dry = ("\n\nThis is a DRY RUN: measure_configuration only shows what "
               "would be applied to the server, and the run ends after your "
               "first call to it. Choose the configuration you would measure "
               "first." if self.dry_run else "")
        return (
            "<measurement_setup>\n"
            f"{json.dumps(self.context, indent=2, default=str)}\n"
            "</measurement_setup>\n\n"
            "<search_space>\n"
            f"{json.dumps(describe_space(self.space), indent=2, default=str)}\n"
            "</search_space>\n\n"
            f"<previous_results count=\"{len(rows)}\">\n{previous}\n"
            "</previous_results>\n\n"
            f"Measurement budget: {self.max_configs} in total, "
            f"{self.remaining()} remaining.\n\n"
            "Find the configuration with the highest max sustainable QPS. "
            "Plan how to spend the budget, then begin." + dry
        )

    # -- dispatch -----------------------------------------------------------

    def call(self, name, arguments):
        """Run one tool. Returns (text, is_error); never raises for a bad call."""
        handler = self._handlers.get(name)
        if handler is None:
            return f"unknown tool {name!r}", True
        if not isinstance(arguments, dict):
            return "tool input must be a JSON object", True
        with self._lock:
            self.transcript.record("tool_call", tool=name, input=arguments)
            try:
                inspect.signature(handler).bind(**arguments)
            except TypeError as e:
                result, is_error = f"bad arguments for {name}: {e}", True
            else:
                try:
                    result, is_error = handler(**arguments), False
                except AgentError as e:
                    result, is_error = str(e), True
            text = (result if isinstance(result, str)
                    else json.dumps(result, indent=1, default=str))
            text = _clip(text, MAX_TOOL_OUTPUT)
            self.transcript.record("tool_result", tool=name, is_error=is_error,
                                   text=text)
        if is_error:
            log.warning("Tool %s rejected: %s", name, _clip(text, 300))
        return text, is_error

    # -- tools --------------------------------------------------------------

    def measure_configuration(self, configuration, hypothesis, repeat=False):
        if self.report is not None:
            raise AgentError("the search has already finished")
        if self.stop_reason:
            raise AgentError(f"no more measurements: {self.stop_reason}. Call "
                             "finish now with your report.")
        if self.remaining() == 0:
            raise AgentError("the measurement budget is used up; call finish")
        overrides, errors = validate_configuration(self.space, configuration)
        if errors:
            raise AgentError("configuration rejected:\n- " + "\n- ".join(errors))

        base_id = point_id(overrides)
        pid = base_id
        if base_id in self.evaluator.rows:
            if not repeat:
                raise AgentError(
                    "this configuration was already measured: "
                    + json.dumps(self._row_view(self.evaluator.rows[base_id]))
                    + "\nChange it, or pass repeat=true to measure it again.")
            n = 2
            while f"{base_id}__rep{n}" in self.evaluator.rows:
                n += 1
            pid = f"{base_id}__rep{n}"

        log.info("Agent hypothesis: %s", hypothesis)
        if self.dry_run:
            self.dry_run_done = True
            bind_overrides, system_overrides = split_overrides(overrides)
            statements = [bind_config.render_statement(n, v)
                          for n, v in bind_overrides.items()]
            commands = system_config.render_commands(system_overrides)
            log.info("[dry run] %s", pid)
            for line in statements:
                log.info("[dry run] named.conf.options: %s", line)
            for line in commands:
                log.info("[dry run] host: %s", line)
            return {
                "dry_run": True,
                "point_id": pid,
                "named.conf.options statements": statements,
                "host commands": commands,
                "note": "Nothing was applied or measured. Call finish now.",
            }

        label = f"[agent {self.used() + 1}/{self.max_configs}]"
        extra = {"iteration": self.used() + 1, "hypothesis": hypothesis}
        if pid != base_id:
            extra["repeat_of"] = base_id
        row = self.evaluator.evaluate(overrides, label, extra, pid=pid)
        return {**self._row_view(row),
                "levels": self._levels(row),
                **self._budget()}

    def get_results(self, sort_by="order"):
        rows = list(self.evaluator.rows.values())
        if sort_by == "qps":
            rows.sort(key=lambda r: row_qps(r) or -1, reverse=True)
        return {**self._budget(),
                "results": [self._row_view(r) for r in rows]}

    def _run_files(self, row):
        run_dir = row.get("run_dir") or ""
        base = os.path.join(run_dir, "max_sustainable_qps")
        return (_read_csv(os.path.join(base, "level_tests.csv")),
                _read_csv(os.path.join(base, "trial_results.csv")))

    def _levels(self, row):
        """Compact per-level view: [target_qps, avg answer rate %, passed]."""
        levels, _ = self._run_files(row)
        return [[lvl.get("target_qps"), lvl.get("average_answer_rate_pct"),
                 lvl.get("passed")] for lvl in levels]

    def get_run_details(self, point_id):
        row = self.evaluator.rows.get(point_id)
        if row is None:
            raise AgentError(f"no measurement with point_id {point_id!r}; "
                             "see get_results")
        levels, trials = self._run_files(row)
        keep = ["target_qps", "trial", "achieved_qps", "answer_rate_pct",
                "qps_fidelity_pct", "passed", "status"]
        return {**self._row_view(row),
                "levels": levels,
                "trials": [{k: t.get(k) for k in keep} for t in trials]}

    def inspect_server(self, probe):
        if probe not in SERVER_PROBES:
            raise AgentError(f"unknown probe {probe!r}; choose from "
                             f"{list(SERVER_PROBES)}")
        _, command = SERVER_PROBES[probe]
        try:
            result = ssh_run(self.server, command, timeout=PROBE_TIMEOUT)
        except subprocess.TimeoutExpired:
            raise AgentError(f"probe {probe} timed out after {PROBE_TIMEOUT}s")
        except OSError as e:
            raise AgentError(f"probe {probe} could not run: {e}")
        output = (result.stdout or "") + (
            f"\n[stderr]\n{result.stderr}" if (result.stderr or "").strip() else "")
        return f"[{probe} on {self.server}, rc={result.returncode}]\n{output}"

    def finish(self, recommended_point_id, summary):
        if self.report is not None:
            return "already finished"
        if recommended_point_id and recommended_point_id not in self.evaluator.rows:
            raise AgentError(f"no measurement with point_id "
                             f"{recommended_point_id!r}; see get_results")
        row = self.evaluator.rows.get(recommended_point_id)
        self.report = {
            "recommended_point_id": recommended_point_id or None,
            "recommended_configuration": ({spec["name"]: row.get(spec["name"])
                                           for spec in self.space} if row else None),
            "recommended_max_qps_passed": row_qps(row) if row else None,
            "summary": summary,
            **self._budget(),
        }
        log.info("Agent finished. Recommended: %s", recommended_point_id or "(none)")
        return "Search finished. End your turn now."
