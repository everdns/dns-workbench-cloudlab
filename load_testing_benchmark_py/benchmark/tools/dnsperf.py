import re

from benchmark.results import ToolResult
from benchmark.tools.base import Tool


class Dnsperf(Tool):
    name = "dnsperf"
    reports_latency = True

    def validate_params(self, config, qps):
        threads = config["threads"]
        max_outstanding = 65536 * threads
        # Just a sanity check; the constraint is on the value we pass
        if max_outstanding < 1:
            raise ValueError(f"Invalid max_outstanding: {max_outstanding}")

    def build_command(self, config, qps):
        server = config["hosts"]["server"]
        runtime = config["runtime"]
        input_file = config["input_files"]["dnsperf"]
        threads = config["threads"]
        ports_per_thread = config["ports_per_thread"]
        timeout = config["timeout"]
        max_outstanding = 65536 * threads
        clients = threads * ports_per_thread

        return (
            f"dnsperf -s {server} -l {runtime} -d {input_file}"
            f" -c {clients} -T {threads}"
            f" -Q {qps} -q {max_outstanding}"
            f" -O suppress=timeout -O qps-threshold-wait=0 -t {timeout}"
        )

    def parse_output(self, stdout):
        result = ToolResult(raw_output=stdout)

        def find_int(pattern):
            m = re.search(pattern, stdout)
            return int(m.group(1)) if m else 0

        def find_float(pattern):
            m = re.search(pattern, stdout)
            return float(m.group(1)) if m else 0.0

        result.queries_sent = find_int(r"Queries sent:\s+(\d+)")
        result.queries_completed = find_int(r"Queries completed:\s+(\d+)")
        result.queries_lost = find_int(r"Queries lost:\s+(\d+)")
        result.run_time = find_float(r"Run time \(s\):\s+([\d.]+)")
        result.achieved_qps = find_float(r"Queries per second:\s+([\d.]+)")
        result.avg_latency = find_float(r"Average Latency \(s\):\s+([\d.]+)")

        m = re.search(r"Average Latency \(s\):\s+[\d.]+\s+\(min\s+([\d.]+),\s+max\s+([\d.]+)\)", stdout)
        if m:
            result.min_latency = float(m.group(1))
            result.max_latency = float(m.group(2))

        result.latency_stddev = find_float(r"Latency StdDev \(s\):\s+([\d.]+)")

        # Parse response codes
        for m in re.finditer(r"(NOERROR|SERVFAIL|NXDOMAIN|REFUSED)\s+(\d+)", stdout):
            result.response_codes[m.group(1)] = int(m.group(2))

        return result
