import argparse
import os
import yaml


DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.yaml")


def load_config(config_path=None):
    """Load configuration from YAML file."""
    path = config_path or DEFAULT_CONFIG_PATH
    with open(path) as f:
        return yaml.safe_load(f)


def apply_cli_overrides(config, args):
    """Apply CLI argument overrides to config dict."""
    overrides = {
        "server_interface": args.server_interface,
        "client_interface": args.client_interface,
        "threads": args.threads,
        "dnspyre_workers": args.dnspyre_workers,
        "ports_per_thread": args.ports_per_thread,
        "timeout": args.timeout,
        "subnet": args.subnet,
        "runtime": args.runtime,
        "dns_responder_margin": args.dns_responder_margin,
        "pause_between_runs": args.pause_between_runs,
        "max_delay_between_bursts": args.max_delay_between_bursts,
        "dns_responder_batch_size": args.dns_responder_batch_size,
    }
    if args.server:
        config.setdefault("hosts", {})["server"] = args.server
    if args.client:
        config.setdefault("hosts", {})["client"] = args.client
    if args.dnsperf_input:
        config.setdefault("input_files", {})["dnsperf"] = args.dnsperf_input
    if args.dnspyre_input:
        config.setdefault("input_files", {})["dnspyre"] = args.dnspyre_input

    for key, value in overrides.items():
        if value is not None:
            config[key] = value

    if args.tools:
        config["tools"] = args.tools
    if getattr(args, "recieve_only", None):
        config["dns_responder_recieve_only"] = True

    return config


def add_common_args(parser):
    """Add common CLI arguments shared across all scripts."""
    parser.add_argument("--config", default=None, help="Path to config YAML file")
    parser.add_argument("--server", help="Server host (user@host)")
    parser.add_argument("--client", help="Client host (user@host or localhost)")

    parser.add_argument("--server-interface", help="Network interface on server (for dns_responder)")
    parser.add_argument("--client-interface", help="Network interface on client (for kxdpgun)")
    parser.add_argument("--dnsperf-input", help="Path to dnsperf input file")
    parser.add_argument("--dnspyre-input", help="Path to dnspyre input file")
    parser.add_argument("--threads", type=int, help="Number of threads")
    parser.add_argument("--dnspyre-workers", type=int, help="Number of workers for dnspyre tools (replaces threads)")
    parser.add_argument("--ports-per-thread", type=int, help="Ports per thread")
    parser.add_argument("--timeout", type=int, help="Query timeout in seconds")
    parser.add_argument("--subnet", help="Subnet for dns64perf++")
    parser.add_argument("--runtime", type=int, help="Test runtime in seconds")
    parser.add_argument("--dns-responder-margin", type=int, help="Extra seconds for dns_responder after test")
    parser.add_argument("--pause-between-runs", type=int, help="Pause between runs in seconds")
    parser.add_argument("--max-delay-between-bursts", type=int, help="Max delay between bursts in ns")
    parser.add_argument("--tools", nargs="+", help="Subset of tools to test")
    parser.add_argument("--output-dir", default="results", help="Output directory for results")
    parser.add_argument("--dry-run", action="store_true", help="Print commands without executing")
    parser.add_argument("--recieve-only", action="store_true", default=None, help="Run dns_responder in receive-only mode (no responses sent)")
    parser.add_argument("--dns-responder-batch-size", type=int, help="Batch size for dns_responder (-b flag)")

def add_script1_args(parser):
    """Add Script 1 (max throughput) specific arguments."""
    parser.add_argument("--start-qps", type=int, help="Starting QPS")
    parser.add_argument("--qps-step", type=int, help="QPS increment per iteration")
    parser.add_argument("--max-qps", type=int, help="Maximum QPS to test")
    parser.add_argument("--trials", type=int, help="Number of trials per QPS level")


def add_script2_args(parser):
    """Add Script 2 (QPS accuracy) specific arguments."""
    parser.add_argument("--accuracy-min-qps", type=int, help="Minimum QPS for accuracy test")
    parser.add_argument("--accuracy-max-qps", type=int, help="Maximum QPS for accuracy test")
    parser.add_argument("--accuracy-step", type=int, help="QPS step for accuracy test")
    parser.add_argument("--trials", type=int, help="Number of trials per QPS per tool")
    parser.add_argument("--crop", type=float, help="Seconds to trim from start and end of timestamps before computing metrics")


def add_script3_args(parser):
    """Add Script 3 (load impact) specific arguments."""
    parser.add_argument("--impact-min-qps", type=int, help="Minimum QPS for impact test")
    parser.add_argument("--impact-max-qps", type=int, help="Maximum QPS for impact test")
    parser.add_argument("--impact-qps-step", type=int, help="QPS step for impact test")
    parser.add_argument("--impact-trials", type=int, help="Number of trials per test")
    parser.add_argument("--dns-services", nargs="+", help="DNS services to test")
    parser.add_argument(
        "--clear-cache",
        dest="clear_cache",
        action="store_true",
        default=None,
        help="Clear the resolver cache before each tool run (resolvers only)",
    )
    parser.add_argument(
        "--no-clear-cache",
        dest="clear_cache",
        action="store_false",
        help="Disable per-run cache clearing",
    )


def apply_script1_overrides(config, args):
    """Apply Script 1 CLI overrides."""
    s1 = config.setdefault("script1", {})
    if args.start_qps is not None:
        s1["start_qps"] = args.start_qps
    if args.qps_step is not None:
        s1["qps_step"] = args.qps_step
    if args.max_qps is not None:
        s1["max_qps"] = args.max_qps
    if getattr(args, "trials", None) is not None:
        s1["trials"] = args.trials
    return config


def apply_script2_overrides(config, args):
    """Apply Script 2 CLI overrides."""
    s2 = config.setdefault("script2", {})
    if args.accuracy_min_qps is not None:
        s2["accuracy_min_qps"] = args.accuracy_min_qps
    if args.accuracy_max_qps is not None:
        s2["accuracy_max_qps"] = args.accuracy_max_qps
    if args.accuracy_step is not None:
        s2["accuracy_step"] = args.accuracy_step
    if args.trials is not None:
        s2["trials"] = args.trials
    if getattr(args, "crop", None) is not None:
        s2["crop_s"] = args.crop
    return config


def apply_script3_overrides(config, args):
    """Apply Script 3 CLI overrides."""
    s3 = config.setdefault("script3", {})
    if args.impact_min_qps is not None:
        s3["min_qps"] = args.impact_min_qps
    if args.impact_max_qps is not None:
        s3["max_qps"] = args.impact_max_qps
    if args.impact_qps_step is not None:
        s3["qps_step"] = args.impact_qps_step
    if args.impact_trials is not None:
        s3["trials"] = args.impact_trials
    if args.dns_services is not None:
        config.setdefault("dns_services", {})["services"] = args.dns_services
    if getattr(args, "clear_cache", None) is not None:
        s3["clear_cache"] = args.clear_cache
    return config
