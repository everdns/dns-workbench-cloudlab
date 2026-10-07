#!/usr/bin/env python3
"""Launch and manage CloudLab experiments for this repository via the Portal API.

    python3 launch.py launch --name dns1 --num-testers 2 --dry-run
    python3 launch.py launch --name dns1 --num-testers 2
    python3 launch.py wait <experiment_id>
    python3 launch.py status <experiment_id>
    python3 launch.py list
    python3 launch.py extend <experiment_id> --hours 2 --reason "long search"
    python3 launch.py terminate <experiment_id>
"""
import argparse
import json
import sys

import ops
from client import CloudLabError


def emit(obj):
    print(json.dumps(obj, indent=2, default=str))


def print_nodes(info):
    print(f"status: {info['status']}")
    for n in info["nodes"]:
        print(f"  {n['client_id']:<14} {n['hardware_type'] or '?':<10} {n['ssh'] or '(no login yet)'}")


def cmd_launch(args, config, client):
    body = ops.build_launch_body(
        config, args.name, project=args.project, profile_name=args.profile_name,
        profile_project=args.profile_project, duration=args.duration, refspec=args.refspec,
        num_testers=args.num_testers, name_server_software=args.server_software,
        name_server_hardware=args.server_hw, tester_hardware=args.tester_hw,
        allow_interswitch_links=True if args.interswitch else None)
    if args.dry_run:
        print("DRY RUN: would POST /experiments with:")
        emit(body)
        return
    emit(client.create_experiment(body))


def cmd_status(args, config, client):
    info = ops.describe(client, args.experiment_id)
    print_nodes(info) if not args.json else emit(info)


def cmd_wait(args, config, client):
    print_nodes(ops.wait_ready(client, args.experiment_id, args.timeout, args.interval))


def cmd_list(args, config, client):
    emit(client.list_experiments(project=args.project or config.get("project") or None,
                                 creator=args.creator))


def cmd_profiles(args, config, client):
    emit(client.list_profiles())


def cmd_manifest(args, config, client):
    emit(client.manifests(args.experiment_id))


def cmd_extend(args, config, client):
    emit(ops.extend(client, config, args.experiment_id, args.hours, args.reason))


def cmd_terminate(args, config, client):
    emit(client.terminate(args.experiment_id))
    print(f"terminated {args.experiment_id}", file=sys.stderr)


def cmd_refresh(args, config, client):
    emit(client.refresh_token())
    print("Store the new token (the old one is now revoked) in CLOUDLAB_API_TOKEN or "
          "~/.config/cloudlab/token.", file=sys.stderr)


def build_parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", help="config file (default: cloudlab_api/config.yaml)")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("launch", help="start an experiment from the repo profile")
    s.add_argument("--name", required=True)
    s.add_argument("--project")
    s.add_argument("--profile-name")
    s.add_argument("--profile-project")
    s.add_argument("--duration", type=int, help="hours")
    s.add_argument("--refspec", help="git ref the nodes clone, e.g. refs/heads/fully_agentic")
    s.add_argument("--num-testers", type=int)
    s.add_argument("--server-software", choices=ops.SOFTWARE)
    s.add_argument("--server-hw", help="name server hardware type, e.g. c6525-25g or any")
    s.add_argument("--tester-hw")
    s.add_argument("--interswitch", action="store_true", help="allow interswitch links")
    s.add_argument("--dry-run", action="store_true", help="print the request without sending it")
    s.set_defaults(func=cmd_launch)

    for name, func, helptext in (("status", cmd_status, "experiment status and node ssh commands"),
                                 ("manifest", cmd_manifest, "raw manifests"),
                                 ("terminate", cmd_terminate, "terminate an experiment (irreversible)")):
        s = sub.add_parser(name, help=helptext)
        s.add_argument("experiment_id")
        if name == "status":
            s.add_argument("--json", action="store_true")
        s.set_defaults(func=func)

    s = sub.add_parser("wait", help="block until the experiment is ready")
    s.add_argument("experiment_id")
    s.add_argument("--timeout", type=int, default=3600)
    s.add_argument("--interval", type=int, default=30)
    s.set_defaults(func=cmd_wait)

    s = sub.add_parser("extend", help="extend an experiment")
    s.add_argument("experiment_id")
    s.add_argument("--hours", type=int, required=True)
    s.add_argument("--reason", required=True)
    s.set_defaults(func=cmd_extend)

    s = sub.add_parser("list", help="list experiments")
    s.add_argument("--project")
    s.add_argument("--creator")
    s.set_defaults(func=cmd_list)

    sub.add_parser("profiles", help="list profiles").set_defaults(func=cmd_profiles)
    sub.add_parser("refresh-token", help="replace the API token").set_defaults(func=cmd_refresh)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        config = ops.load_config(args.config)
        client = ops.make_client()
        args.func(args, config, client)
    except CloudLabError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
