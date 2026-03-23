from abc import ABC, abstractmethod

from benchmark.results import ToolResult


class Tool(ABC):
    """Abstract base class for DNS load testing tool adapters."""

    name: str = ""
    reports_latency: bool = False

    @abstractmethod
    def build_command(self, config, qps):
        """Build the shell command string to run this tool.

        Args:
            config: global config dict
            qps: target queries per second

        Returns:
            Command string ready for shell execution.
        """

    @abstractmethod
    def parse_output(self, stdout):
        """Parse tool stdout into a ToolResult.

        Args:
            stdout: raw stdout string from the tool

        Returns:
            ToolResult instance.
        """

    def validate_params(self, config, qps):
        """Check tool-specific constraints. Raise ValueError on violation."""
        pass
