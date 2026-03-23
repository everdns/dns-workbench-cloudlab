import math
import re

from benchmark.results import ToolResult
from benchmark.tools.base import Tool


def compute_burst_params(target_qps, threads, max_delay_ns, runtime):
    """Calculate burst_size, delay_ns, and number_of_requests for dns64perf++.

    Strategy: pick burst_size so that delay stays <= max_delay_ns.
    Then round number_of_requests up to satisfy the divisibility constraint.
    """
    # burst_size = ceil(target_qps * max_delay_ns / (threads * 1e9))
    burst_size = max(1, math.ceil(target_qps * max_delay_ns / (threads * 1_000_000_000)))

    # delay = burst_size * threads * 1e9 / target_qps
    delay_ns = round(burst_size * threads * 1_000_000_000 / target_qps)
    if delay_ns < 1:
        delay_ns = 1

    # number_of_requests = target_qps * runtime, rounded up to nearest (burst_size * threads)
    chunk = burst_size * threads
    raw_requests = target_qps * runtime
    number_of_requests = math.ceil(raw_requests / chunk) * chunk

    return burst_size, delay_ns, number_of_requests


class Dns64PerfPP(Tool):
    name = "dns64perf++"
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
        subnet = config["subnet"]
        threads = config["threads"]
        ports_per_thread = config["ports_per_thread"]
        timeout = config["timeout"]
        max_delay = config["max_delay_between_bursts"]
        runtime = config["runtime"]

        burst_size, delay_ns, num_requests = compute_burst_params(
            qps, threads, max_delay, runtime
        )

        return (
            f"dns64perf++ {resolver} {port} {subnet}"
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

        # Estimate achieved QPS from sent queries / implied runtime
        # dns64perf++ doesn't report runtime directly, so derive from avg RTT
        result.avg_latency = find_float(r"Average round-trip time:\s+([\d.]+)\s+ms")
        if result.avg_latency:
            result.avg_latency /= 1000.0  # Convert ms to seconds

        result.latency_stddev = find_float(
            r"Standard deviation of the round-trip time:\s+([\d.]+)\s+ms"
        )
        if result.latency_stddev:
            result.latency_stddev /= 1000.0

        return result
