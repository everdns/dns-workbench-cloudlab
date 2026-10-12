# DNS Arena leaderboard

Static page ranking DNS server configurations by max sustainable QPS.

    cd leaderboard && python3 -m http.server 8000   # or just open index.html

Add results with `publish.py` (below), which writes `entries/*.json` and regenerates `results.js`. Entries without `setup`/`optimize` still display; p99 and answer rate are optional.

`entries/sample.json` holds placeholder data (badged SAMPLE); delete it once you have real entries.

    {id, software, version, hardware:{name,cpu,arch,cores,ram_gb,nic}, config:{any key: value}, metrics:{max_qps,p99_ms,answer_rate}}

`config` keys are free-form (BIND options, sysctls, thread counts...) and automatically become filterable. Remove `sample:true` from real entries.
Filter and sort state is kept in the URL hash, so views are shareable (`#sort=qps:-1&sw=bind&cfg=xdp=false`).

## Workflow: launch, optimize, publish

1. Launch: `python3 cloudlab_api/launch.py launch --name dns1 --num-testers 1 --server-software bind --server-hw c6525-25g` (or ask Claude Code), then `wait`.
2. Run a search from a tester: `cd optimization && python3 grid_search.py` (or `agent_search.py`).
3. Publish the best point:

       cd leaderboard
       python3 publish.py --results ../optimization/results/grid_search/grid_results.json \
         --search-config ../optimization/grid_search.yaml \
         --hardware-name c6525-25g \
         --software-version 9.18.28 --testers 1

   `--hardware-name` is the CloudLab hardware type (the `--server-hw` you launched with). `--cpu`, `--arch`, `--cores`, `--ram-gb` and `--nic` are optional extras.

   Rows whose load generator fell below 99% QPS fidelity are skipped (they measure the client, not the server). Use `--point <point_id>` to publish a specific row.

The entry stores the launch/setup steps and the exact name-server commands (sysctls, iptables, named.conf.options edits, restart) rendered from the same code the search used, shown when you click a row.

## Importing saved search runs

`import_runs.py` turns result files saved under `data/<env>/` (`1_agent_bounded.json`, `2_coordinate_descent.json`, `3_agent_open.json`) into one entry per machine and search. It keeps the best measurement whose load generator delivered at least 99% of the requested queries, renders the setup steps and the commands that apply that configuration, and rebuilds `results.js`.

    python3 import_runs.py --run-label run1 --clients 1

Entries for machines where a single load generator capped the result (rs620) are marked as lower bounds ("≥") with a note. Use a new `--run-label` for each batch so earlier entries are kept.
