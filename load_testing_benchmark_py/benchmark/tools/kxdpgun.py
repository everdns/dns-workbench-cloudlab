import re

from benchmark.results import ToolResult
from benchmark.tools.base import Tool


class Kxdpgun(Tool):
    name = "kxdpgun"
    reports_latency = False

    def build_command(self, config, qps):
        server = config["hosts"]["server"]
        runtime = config["runtime"]
        input_file = config["input_files"]["dnsperf"]
        interface = config["client_interface"]

        return (
            f"kxdpgun -t {runtime} -Q {qps} -b 1"
            f" -i {input_file} -I {interface}"
            f" {server}"
        )

    def parse_output(self, stdout):
        result = ToolResult(raw_output=stdout)

        # "total queries:     400040 (100010 pps)"
        m = re.search(r"total queries:\s+([\d]+)\s+\((\d+)\s+pps\)", stdout)
        if m:
            result.queries_sent = int(m.group(1))

        # "total replies:     400033 (100008 pps) (99%)"
        m = re.search(r"total replies:\s+([\d]+)\s+\((\d+)\s+pps\)", stdout)
        if m:
            result.queries_completed = int(m.group(1))

        result.queries_lost = result.queries_sent - result.queries_completed

        # "duration: 4 s"
        m = re.search(r"duration:\s+(\d+)\s+s", stdout)
        if m:
            result.run_time = float(m.group(1))

        if result.run_time > 0:
            result.achieved_qps = result.queries_sent / result.run_time

        # Parse response codes
        for m_code in re.finditer(r"responded\s+(\w+):\s+(\d+)", stdout):
            result.response_codes[m_code.group(1)] = int(m_code.group(2))

        return result
