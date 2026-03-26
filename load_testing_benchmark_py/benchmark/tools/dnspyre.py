import re

from benchmark.results import ToolResult
from benchmark.tools.base import Tool


def _parse_latency(value_str):
    """Parse a latency string like '43.01us', '158.9us', '15.2ms' to seconds."""
    value_str = value_str.strip()
    if value_str.endswith("ms"):
        return float(value_str[:-2]) / 1000.0
    elif value_str.endswith("µs") or value_str.endswith("us"):
        suffix_len = 2
        return float(value_str[:-suffix_len]) / 1_000_000.0
    elif value_str.endswith("s"):
        return float(value_str[:-1])
    return float(value_str)


class Dnspyre(Tool):
    name = "dnspyre"
    reports_latency = True

    def build_command(self, config, qps):
        server = config["hosts"]["server"]
        runtime = config["runtime"]
        threads = config["dnspyre_workers"]
        timeout = config["timeout"]
        input_file = config["input_files"]["dnspyre"]

        return (
            f"dnspyre --type=A --server {server}"
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

        read_write_errors = find_int(r"Read/Write errors:\s+(\d+)")
        result.queries_sent = find_int(r"Total requests:\s+(\d+)") - read_write_errors
        result.queries_completed = find_int(r"DNS success responses:\s+(\d+)")
        result.queries_lost = result.queries_sent - result.queries_completed
        result.achieved_qps = find_float(r"Questions per second:\s+([\d.]+)")

        m = re.search(r"Time taken for tests:\s+([\d.]+)s", stdout)
        if m:
            result.run_time = float(m.group(1))

        # Parse latency timings
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

        # Parse percentiles
        for pct in ["p99", "p95", "p90", "p75", "p50"]:
            m = re.search(rf"{pct}:\s+([\d.]+[µu]?[sm]?s?)", stdout)
            if m:
                result.percentiles[pct] = _parse_latency(m.group(1))

        # Parse response codes
        for m_code in re.finditer(r"(NOERROR|SERVFAIL|NXDOMAIN|REFUSED):\s+(\d+)", stdout):
            result.response_codes[m_code.group(1)] = int(m_code.group(2))

        return result
