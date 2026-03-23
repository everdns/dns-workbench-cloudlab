import re

from benchmark.results import ToolResult
from benchmark.tools.base import Tool
from benchmark.tools.dns64perfpp import compute_burst_params


class Dns64PerfPPWorkbench(Tool):
    name = "dns64perfpp-workbench"
    reports_latency = True

    def validate_params(self, config, qps):
        threads = config["threads"]
        max_delay = config["max_delay_between_bursts"]
        burst_size, delay_ns, num_requests = compute_burst_params(
            qps, threads, max_delay, config["runtime"]
        )
        chunk = burst_size * threads
        if num_requests % chunk != 0:
            raise ValueError(
                f"number_of_requests ({num_requests}) not divisible by "
                f"burst_size*threads ({chunk})"
            )

    def build_command(self, config, qps):
        resolver = config["resolver"]
        port = 53
        input_file = config["input_files"]["dnsperf"]
        threads = config["threads"]
        ports_per_thread = config["ports_per_thread"]
        timeout = config["timeout"]
        max_delay = config["max_delay_between_bursts"]
        runtime = config["runtime"]

        burst_size, delay_ns, num_requests = compute_burst_params(
            qps, threads, max_delay, runtime
        )

        return (
            f"dns64perfpp-workbench {resolver} {port} {input_file}"
            f" {num_requests} {burst_size} {threads}"
            f" {ports_per_thread} {delay_ns} {timeout}"
        )

    def parse_output(self, stdout):
        result = ToolResult(raw_output=stdout)

        def find_int(pattern):
            m = re.search(pattern, stdout)
            return int(m.group(1)) if m else 0

        def find_float(pattern):
            m = re.search(pattern, stdout)
            return float(m.group(1)) if m else 0.0

        result.queries_sent = find_int(r"Sent queries:\s+(\d+)")

        m = re.search(r"Received answers:\s+(\d+)", stdout)
        if m:
            result.queries_completed = int(m.group(1))
        result.queries_lost = result.queries_sent - result.queries_completed

        result.avg_latency = find_float(r"Average round-trip time:\s+([\d.]+)\s+ms")
        if result.avg_latency:
            result.avg_latency /= 1000.0

        result.latency_stddev = find_float(
            r"Standard deviation of the round-trip time:\s+([\d.]+)\s+ms"
        )
        if result.latency_stddev:
            result.latency_stddev /= 1000.0

        return result
