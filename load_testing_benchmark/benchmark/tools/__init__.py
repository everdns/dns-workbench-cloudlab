from benchmark.tools.dnsperf import Dnsperf
from benchmark.tools.dnsperf_workbench import DnsperfWorkbenchSlice, DnsperfWorkbenchLencse
from benchmark.tools.dnspyre import Dnspyre
from benchmark.tools.dnspyre_workbench import DnspyreWorkbench
from benchmark.tools.dns64perfpp import Dns64PerfPP
from benchmark.tools.dns64perfpp_workbench import Dns64PerfPPWorkbench
from benchmark.tools.kxdpgun import Kxdpgun
from benchmark.tools.kxdpgun_dnsworkbench import KxdpgunWorkbench

TOOL_REGISTRY = {
    "dnsperf": Dnsperf,
    "dnsperf-workbench-slice": DnsperfWorkbenchSlice,
    "dnsperf-workbench-lencse": DnsperfWorkbenchLencse,
    "dnspyre": Dnspyre,
    "dnspyre-workbench": DnspyreWorkbench,
    "dns64perf++": Dns64PerfPP,
    "dns64perfpp-workbench": Dns64PerfPPWorkbench,
    "kxdpgun": Kxdpgun,
    "kxdpgun-dnsworkbench": KxdpgunWorkbench,
}


def get_tools(names=None):
    """Return tool instances, optionally filtered by name."""
    if names is None:
        return [cls() for cls in TOOL_REGISTRY.values()]
    tools = []
    for name in names:
        if name not in TOOL_REGISTRY:
            raise ValueError(f"Unknown tool: {name}. Available: {list(TOOL_REGISTRY.keys())}")
        tools.append(TOOL_REGISTRY[name]())
    return tools
