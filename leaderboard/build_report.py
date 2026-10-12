#!/usr/bin/env python3
"""Build search_results.html from data/<env>/<search>.json (+ .report.json)."""
import json, os
HERE = os.path.dirname(os.path.abspath(__file__))
ENVS = [("r320", "r320", "16 threads"), ("d710", "d710", "8 threads"), ("rs620", "rs620", "40 threads")]
SEARCHES = ["1_agent_bounded", "2_coordinate_descent", "3_agent_open"]
PARAMS = ["udp-receive-buffer", "udp-send-buffer", "max-udp-size", "udp-conntrack", "rmem",
          "netdev-max-backlog", "netdev-budget", "udp-mem", "optmem-max", "named-nofile"]

def num(v):
    try: return float(v)
    except (TypeError, ValueError): return None

data = {}
for env, name, cpus in ENVS:
    data[env] = {"name": name, "cpus": cpus, "searches": {}}
    for s in SEARCHES:
        rows = json.load(open(f"{HERE}/data/{env}/{s}.json"))
        rp = f"{HERE}/data/{env}/{s}.report.json"
        report = json.load(open(rp)) if os.path.exists(rp) else {}
        out = []
        for r in rows:
            fid = num(r.get("min_qps_fidelity_pct"))
            ok = r["status"] == "ok" and num(r.get("max_qps_passed")) is not None
            if s == "3_agent_open":
                desc = r.get("label") or ""
            else:
                desc = ", ".join(f"{k}={r[k]}" for k in PARAMS if str(r.get(k, "default")) != "default")
            out.append({"i": r["index"], "qps": int(num(r["max_qps_passed"]) or 0) if ok else None,
                        "fid": fid, "status": r["status"], "hours": (num(r.get("search_duration_s")) or 0) / 3600,
                        "desc": desc, "ceiling": r.get("hit_max_qps_ceiling") in (True, "True")})
        data[env]["searches"][s] = {"rows": out, "summary": report.get("summary", "")}

tpl = open(f"{HERE}/search_results.template.html").read()
open(f"{HERE}/search_results.html", "w").write(tpl.replace("__DATA__", json.dumps(data)))
print("wrote search_results.html", os.path.getsize(f"{HERE}/search_results.html"), "bytes")
