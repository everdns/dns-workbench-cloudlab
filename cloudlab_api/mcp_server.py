#!/usr/bin/env python3
"""Stdio MCP server that lets Claude Code manage CloudLab experiments for this repo.

Same protocol subset as optimization/agent_mcp_proxy.py (newline-delimited
JSON-RPC 2.0: initialize, ping, tools/list, tools/call), standard library only.
Registered in .mcp.json at the repository root.

Launching, extending and terminating use allocation on a shared testbed.
cloudlab_launch therefore defaults to dry_run=true: the agent must show the
request and then call again with dry_run=false.
"""
import json
import sys
import threading

import ops
from client import CloudLabError

DEFAULT_PROTOCOL = "2025-06-18"
_write_lock = threading.Lock()

_ID = {"experiment_id": {"type": "string", "description": "Experiment id from cloudlab_launch/cloudlab_list"}}

TOOLS = [
    {"name": "cloudlab_launch",
     "description": "Start a CloudLab experiment from this repo's profile (1 name server + N testers). "
                    "Defaults to dry_run=true, which only returns the request body; confirm with the user, "
                    "then call again with dry_run=false.",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string", "description": "Experiment name (lowercase letters, digits, dashes)"},
         "dry_run": {"type": "boolean", "default": True},
         "num_testers": {"type": "integer", "minimum": 1, "maximum": 200},
         "name_server_software": {"type": "string", "enum": ops.SOFTWARE},
         "name_server_hardware": {"type": "string"},
         "tester_hardware": {"type": "string"},
         "allow_interswitch_links": {"type": "boolean"},
         "duration": {"type": "integer", "description": "hours"},
         "refspec": {"type": "string", "description": "git ref to clone, e.g. refs/heads/fully_agentic"},
         "project": {"type": "string"}},
         "required": ["name"]}},
    {"name": "cloudlab_status",
     "description": "Experiment status plus each node's hostname, hardware type and ssh command.",
     "inputSchema": {"type": "object", "properties": _ID, "required": ["experiment_id"]}},
    {"name": "cloudlab_wait",
     "description": "Block until the experiment is ready (or failed/timed out). Can take 10+ minutes.",
     "inputSchema": {"type": "object", "properties": {**_ID, "timeout": {"type": "integer", "default": 1800}},
                     "required": ["experiment_id"]}},
    {"name": "cloudlab_list",
     "description": "List experiments, optionally filtered by project.",
     "inputSchema": {"type": "object", "properties": {"project": {"type": "string"}}}},
    {"name": "cloudlab_profiles",
     "description": "List available profiles.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "cloudlab_extend",
     "description": "Extend an experiment. Ask the user first.",
     "inputSchema": {"type": "object", "properties": {**_ID, "hours": {"type": "integer"},
                                                      "reason": {"type": "string"}},
                     "required": ["experiment_id", "hours", "reason"]}},
    {"name": "cloudlab_terminate",
     "description": "Terminate an experiment. Irreversible: all node state is lost. Ask the user first.",
     "inputSchema": {"type": "object", "properties": _ID, "required": ["experiment_id"]}},
]


def call_tool(name, a):
    config = ops.load_config()
    client = ops.make_client()
    if name == "cloudlab_launch":
        body = ops.build_launch_body(
            config, a["name"], project=a.get("project"), duration=a.get("duration"),
            refspec=a.get("refspec"), num_testers=a.get("num_testers"),
            name_server_software=a.get("name_server_software"),
            name_server_hardware=a.get("name_server_hardware"),
            tester_hardware=a.get("tester_hardware"),
            allow_interswitch_links=a.get("allow_interswitch_links"))
        if a.get("dry_run", True):
            return {"dry_run": True, "would_post": body}
        return client.create_experiment(body)
    if name == "cloudlab_status":
        return ops.describe(client, a["experiment_id"])
    if name == "cloudlab_wait":
        return ops.wait_ready(client, a["experiment_id"], timeout=a.get("timeout", 1800))
    if name == "cloudlab_list":
        return client.list_experiments(project=a.get("project") or config.get("project") or None)
    if name == "cloudlab_profiles":
        return client.list_profiles()
    if name == "cloudlab_extend":
        return ops.extend(client, config, a["experiment_id"], a["hours"], a["reason"])
    if name == "cloudlab_terminate":
        return client.terminate(a["experiment_id"]) or {"terminated": a["experiment_id"]}
    raise LookupError(name)


def send(message):
    with _write_lock:
        sys.stdout.write(json.dumps(message) + "\n")
        sys.stdout.flush()


def handle(message):
    method = message.get("method")
    params = message.get("params") or {}
    if method == "initialize":
        return {"protocolVersion": params.get("protocolVersion", DEFAULT_PROTOCOL),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "cloudlab", "version": "1.0"}}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        try:
            result = call_tool(params.get("name"), params.get("arguments") or {})
            return {"content": [{"type": "text", "text": json.dumps(result, indent=2, default=str)}],
                    "isError": False}
        except LookupError:
            raise
        except (CloudLabError, KeyError, ValueError, OSError) as e:
            return {"content": [{"type": "text", "text": f"error: {e}"}], "isError": True}
    raise LookupError(method)


def serve(message):
    msg_id = message.get("id")
    try:
        result = handle(message)
    except LookupError:
        if msg_id is not None:
            send({"jsonrpc": "2.0", "id": msg_id,
                  "error": {"code": -32601, "message": f"method not found: {message.get('method')}"}})
        return
    if msg_id is not None:
        send({"jsonrpc": "2.0", "id": msg_id, "result": result})


def main():
    workers = []
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
            continue
        if "method" not in message:
            continue
        # cloudlab_wait can take many minutes; keep answering pings meanwhile.
        t = threading.Thread(target=serve, args=(message,), daemon=True)
        t.start()
        workers.append(t)
    for t in workers:  # stdin closed: let in-flight calls answer before exiting
        t.join()
    return 0


if __name__ == "__main__":
    sys.exit(main())
