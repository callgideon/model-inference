#!/usr/bin/env bash
# One monitoring cycle (I8 slice 3), every minute from infrx-observe.timer, as root:
#   1. host-probe.sh      -> host.prom     (GPU, engine, units, disks, cache, edge)
#   2. durable.py         -> durable.prom  (PostgreSQL truth, in the installed runtime image;
#                                           only the DSN crosses in, as a 0600 --env-file)
#   3. the evaluator      (python -m infrx.observe.alerts over the gateway's and the worker's
#                          loopback /metrics and the three textfiles; rules = alerts.json +
#                          operations.json, merged by rules.py)
#   4. deliver.py         (changes only, to ALERT_WEBHOOK_URL - P-25; BLOCKED without it)
# A source that cannot be read is itself an alert (ScrapeFailed), so a dead exporter pages.
# Exit: deliver.py's (0 delivered or nothing to send, 3 BLOCKED, 4 send failed).
set -uo pipefail
repo=${REPO:-/home/ubuntu/model-inference}
dir=${METRICS_DIR:-/var/lib/infrx/metrics}
env_file=${ENV_FILE:-/etc/marlin2b-gateway.env}
run=${RUNTIME_DIRECTORY:-/run}
install -d -o 10001 -g 10000 -m 0770 "$dir"
image=$(sed -n 's/^INFRX_IMAGE=//p' "$env_file")
[[ $image =~ ^sha256:[0-9a-f]{64}$ ]] || { echo "no INFRX_IMAGE in $env_file" >&2; exit 2; }
dsn_env=$(mktemp -p "$run")
firing=$(mktemp -p "$run")
trap 'rm -f "$dsn_env" "$firing"' EXIT
contained=(docker run --rm --network host --read-only --cap-drop ALL
           --security-opt no-new-privileges --memory 512m --pids-limit 64)

OUT=$dir/host.prom bash "$repo/infra/observe/host-probe.sh" || echo "host probe failed" >&2

# The monitor's own read-only login when D10 provides it, else the runtime's DSN (durable.py
# moves it to the transaction port). The value goes file -> docker, never argv or output.
grep -E '^MONITOR_DATABASE_URL=' /etc/infrx-observe.env > "$dsn_env" 2>/dev/null \
  || grep -E '^DATABASE_URL=' "$env_file" > "$dsn_env"
"${contained[@]}" --env-file "$dsn_env" -v "$repo/infra/observe:/observe:ro" -v "$dir:/m" \
  "$image" python /observe/durable.py --out /m/durable.prom || echo "durable exporter failed" >&2
: > "$dsn_env"

rules=$(mktemp -p "$run" rules.XXXXXX.json)
trap 'rm -f "$dsn_env" "$firing" "$rules"' EXIT
python3 "$repo/infra/observe/rules.py" "$repo/infra/alerts" > "$rules"
chmod 0644 "$rules"
"${contained[@]}" -v "$rules:/rules.json:ro" -v "$dir:/m" "$image" \
  python -m infrx.observe.alerts --rules /rules.json \
  --source http://127.0.0.1:8001/metrics --source "http://127.0.0.1:${WORKER_HEALTH_PORT:-8002}/metrics" \
  --source /m/host.prom --source /m/durable.prom --source /m/canary.prom \
  --state /m/alert-state.json --max-age "${MAX_AGE_S:-900}" > "$firing"
version=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$rules")
python3 "$repo/infra/observe/deliver.py" --state "$dir/delivered.json" \
  --undelivered "$dir/undelivered.jsonl" --rules-version "$version" < "$firing"
