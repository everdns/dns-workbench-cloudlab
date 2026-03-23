import re

from benchmark.results import ToolResult
from benchmark.tools.base import Tool
from benchmark.tools.dnspyre import _parse_latency


class DnspyreWorkbench(Tool):
    name = "dnspyre-workbench"
    reports_latency = True

    def build_command(self, config, qps):
        resolver = config["resolver"]
        runtime = config["runtime"]
        threads = config["threads"]
        timeout = config["timeout"]
        # Note: dnspyre-dnsworkbench uses dnsperf_input format
        input_file = config["input_files"]["dnsperf"]

        return (
            f"dnspyre-dnsworkbench --server {resolver}"
            f" --duration {runtime}s -c {threads}"
            f" --rate-limit {qps} --request={timeout}s"
            f" @{input_file}"
        )

    def parse_output(self, stdout):
        result = ToolResult(raw_output=stdout)

        def find_int(pattern):
            m = re.search(pattern, stdout)
            return int(m.group(1)) if m else 0

        def find_float(pattern):
            m = re.search(pattern, stdout)
            return float(m.group(1)) if m else 0.0

        result.queries_sent = find_int(r"Total requests:\s+(\d+)")
        result.queries_completed = find_int(r"DNS success responses:\s+(\d+)")
        result.queries_lost = result.queries_sent - result.queries_completed
        result.achieved_qps = find_float(r"Questions per second:\s+([\d.]+)")

        m = re.search(r"Time taken for tests:\s+([\d.]+)s", stdout)
        if m:
            result.run_time = float(m.group(1))

        def find_latency(label):
            m = re.search(rf"{label}:\s+([\d.]+[µu]?[sm]?s?)", stdout)
            if m:
                return _parse_latency(m.group(1))
            return None

        result.min_latency = find_latency("min")
        result.avg_latency = find_latency("mean")
        result.max_latency = find_latency("max")
        sd = find_latency(r"\[\+/-sd\]")
        if sd is not None:
            result.latency_stddev = sd

        for pct in ["p99", "p95", "p90", "p75", "p50"]:
            m = re.search(rf"{pct}:\s+([\d.]+[µu]?[sm]?s?)", stdout)
            if m:
                result.percentiles[pct] = _parse_latency(m.group(1))

        for m_code in re.finditer(r"(NOERROR|SERVFAIL|NXDOMAIN|REFUSED):\s+(\d+)", stdout):
            result.response_codes[m_code.group(1)] = int(m_code.group(2))

        return result
