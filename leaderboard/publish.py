#!/usr/bin/env python3
"""Add an optimized configuration to the leaderboard.

Takes the results of grid_search.py / agent_search.py, picks the best point
(or --point), renders the exact commands that reproduce it on the name server,
records the environment it was measured in, and rebuilds results.js.

    python3 publish.py --results ../optimization/results/grid_search/grid_results.json \
        --search-config ../optimization/grid_search.yaml \
        --hardware-name c6525-25g --cpu "AMD EPYC 7302P" --arch x86_64 \
        --cores 16 --ram-gb 128 --nic 25GbE --software-version 9.18.28 --testers 1

    python3 publish.py --rebuild      # only regenerate results.js from entries/
"""
import argparse
import glob
import json
import os
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
OPT = os.path.join(os.path.dirname(HERE), "optimization")
sys.path.insert(0, OPT)

import bind_config  # noqa: E402
import system_config  # noqa: E402

BIND_OPTIONS = "/etc/bind/named.conf.options"
MIN_FIDELITY = 99.0  # below this the load generator, not the server, was the limit


def bind_commands(statements):
    """Idempotent shell that sets each statement inside the options {} block."""
    if not statements:
        return []
    names = "|".join(s.split()[0] for s in statements)
    cmds = [f"sudo cp {BIND_OPTIONS} {BIND_OPTIONS}.orig",
            f"sudo sed -i -E '/^\\s*({names})\\s/d' {BIND_OPTIONS}"]
    for s in statements:
        cmds.append(f"sudo sed -i '0,/^}};/s//    {s}\\n}};/' {BIND_OPTIONS}")
    cmds.append("sudo named-checkconf /etc/bind/named.conf")
    return cmds


def pick_row(rows, point):
    ok = [r for r in rows if r.get("status") == "ok" and r.get("max_qps_passed") not in ("", None)]
    if point:
        ok = [r for r in ok if r["point_id"] == point]
    if not ok:
        sys.exit("no successful rows" + (f" for point {point}" if point else ""))
    good = [r for r in ok if not r.get("min_qps_fidelity_pct")
            or float(r["min_qps_fidelity_pct"]) >= MIN_FIDELITY]
    if not good and not point:
        sys.exit("every row was limited by the load generator (low qps fidelity); not publishing")
    return max(good or ok, key=lambda r: float(r["max_qps_passed"]))


def build(args):
    with open(args.results) as f:
        row = pick_row(json.load(f), args.point)
    with open(args.search_config) as f:
        cfg = yaml.safe_load(f)
    names = [p["name"] for p in cfg["parameters"]]
    config = {n: row[n] for n in names if n in row and row[n] != ""}
    for k, v in list(config.items()):  # CSV-sourced rows carry strings
        if isinstance(v, str) and v.lstrip("-").isdigit():
            config[k] = int(v)

    system = {k: v for k, v in config.items() if system_config.is_system_parameter(k)}
    bind = {k: v for k, v in config.items() if k not in system}
    statements = [bind_config.render_statement(k, v) for k, v in bind.items()]
    software = args.software or cfg.get("dns_service", "ns_bind").removeprefix("ns_")
    dns = cfg.get("dns_services", {})
    stop, start = dns.get("stop_script"), dns.get("start_script")
    svc = cfg.get("dns_service", "ns_bind")

    optimize = [
        {"title": "Host tuning (sysctls, firewall, limits)",
         "commands": system_config.render_commands(system)},
        {"title": f"{software} configuration",
         "commands": bind_commands(statements)},
        {"title": "Restart the DNS service",
         "commands": [f"sudo {stop} {svc}", f"sudo {start} {svc}"] if stop and start else []},
    ]
    optimize = [s for s in optimize if s["commands"]]

    hw = args.hardware_name
    launch = (f"python3 cloudlab_api/launch.py launch --name dns-{hw} --num-testers {args.testers} "
              f"--server-software {software} --server-hw {hw}")
    setup = [
        {"title": "Launch the CloudLab experiment", "commands": [launch,
            "python3 cloudlab_api/launch.py wait <experiment_id>",
            "python3 cloudlab_api/launch.py status <experiment_id>   # note the node ssh commands"]},
        {"title": "Authorize the load-generator host on every node",
         "commands": ["./_setup_ssh_keys.sh"]},
        {"title": "Load the zone data and test inputs (on the tester node)",
         "commands": ["sudo /local/repository/optimization/optimization_setup.sh"]},
        {"title": "Check hosts / client_interface / input_file in the search config",
         "commands": [f"$EDITOR /local/repository/optimization/{os.path.basename(args.search_config)}"]},
    ]
    notes = {
        "tool": cfg.get("tool"), "threads": cfg.get("threads"),
        "zone": args.zone, "testers": args.testers,
        "software_version": args.software_version,
    }
    entry = {
        "id": f"{software}-{hw}-{row['point_id']}"[:100],
        "software": software, "version": args.software_version,
        "hardware": {"name": hw, "cpu": args.cpu, "arch": args.arch, "cores": args.cores,
                     "ram_gb": args.ram_gb, "nic": args.nic},
        "config": config,
        "metrics": {"max_qps": int(float(row["max_qps_passed"]))},
        "setup": setup, "optimize": optimize,
        "measured_with": {k: v for k, v in notes.items() if v is not None},
        "verify": f"python3 max_sustainable_qps.py   # see optimization/README.md; config: {args.search_config}",
        "date": args.date,
    }
    return entry


def rebuild():
    entries = []
    for path in sorted(glob.glob(os.path.join(HERE, "entries", "*.json"))):
        with open(path) as f:
            data = json.load(f)
        entries.extend(data if isinstance(data, list) else [data])
    with open(os.path.join(HERE, "results.js"), "w") as f:
        f.write("// Generated by publish.py from entries/*.json - do not edit.\n")
        f.write("window.RESULTS = " + json.dumps(entries, indent=1) + ";\n")
    print(f"results.js: {len(entries)} entries")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--rebuild", action="store_true")
    p.add_argument("--results", help="grid_results.json / coordinate_descent_results.json")
    p.add_argument("--search-config", help="the YAML the search was run with")
    p.add_argument("--point", help="point_id to publish instead of the best")
    p.add_argument("--software"); p.add_argument("--software-version", default="")
    p.add_argument("--hardware-name"); p.add_argument("--cpu", default="")
    p.add_argument("--arch", default=""); p.add_argument("--cores", type=int, default=0)
    p.add_argument("--ram-gb", type=int, default=0); p.add_argument("--nic", default="")
    p.add_argument("--testers", type=int, default=1)
    p.add_argument("--zone", default="")
    p.add_argument("--date", default="")
    args = p.parse_args()
    if not args.rebuild:
        for r in ("results", "search_config", "hardware_name"):
            if not getattr(args, r):
                p.error(f"--{r.replace('_', '-')} is required")
        entry = build(args)
        with open(os.path.join(HERE, "entries", entry["id"] + ".json"), "w") as f:
            json.dump(entry, f, indent=1)
        print(f"published {entry['id']}: {entry['metrics']['max_qps']:,} QPS")
    rebuild()


if __name__ == "__main__":
    main()
