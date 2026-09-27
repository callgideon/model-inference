#!/usr/bin/env bash
# E1B WC-0 (E1B-protocol §7.2), box, as root: the stage/resource scrape sidecar, started with E4C-runbook
# §4 and stopped after the last window cell (§5.2). §7.2's command as written, detached (setsid), one at a time.
#   infra/rollout/ssm.sh infra/rollout/steps/79-wc0-scrape.sh ACTION=start   # dir=<scrape dir> pids=… lines=<n>
#   infra/rollout/ssm.sh infra/rollout/steps/79-wc0-scrape.sh ACTION=stop
# Read-only for the service: GETs to 127.0.0.1:8000-8002/metrics every 30 s and vmstat every 30 s.
# Exit 2: a second start, or a stop with none running. Exit 3: no scrape line after the first cycle.
set -euo pipefail
logs=${E1B_LOGS:-/opt/dlami/nvme/e1b-logs}; pidf=$logs/wc0.pid
case "${ACTION:?start or stop}" in
  start)
    [ ! -s "$pidf" ] || { echo "WC-0 already running (pids $(tr '\n' ' ' < "$pidf")); stop it first" >&2; exit 2; }
    O=$logs/wc0-$(date -u +%Y%m%dT%H%M%SZ); mkdir -p "$O"
    setsid nohup bash -c 'while :; do t=$(date -u +%s); for p in 8000 8001 8002; do curl -s -m 5 127.0.0.1:$p/metrics | grep -E '"'"'^(vllm:(request_(queue|prefill|decode|inference)_time_seconds|time_to_first_token_seconds|e2e_request_latency_seconds|num_requests_(running|waiting)|kv_cache_usage_perc|(prefix|mm)_cache_(queries|hits))|infrx_(requests_rejected_total|large_body_refused_total|phase_seconds|queue_oldest_wait_seconds|db_pool_(wait_seconds_total|requests_total)|process_resident_bytes|gpu_(memory_bytes|utilization_ratio)|processing_cache_bytes))'"'"' | sed "s/^/$t $p /"; done; sleep 30; done' \
      >> "$O/scrape.log" 2> /dev/null < /dev/null & echo $! > "$pidf"
    setsid nohup vmstat -t 30 >> "$O/vmstat.log" 2> /dev/null < /dev/null & echo $! >> "$pidf"
    sleep 35
    n=$(wc -l < "$O/scrape.log")
    echo "dir=$O pids=$(tr '\n' ' ' < "$pidf") lines=$n"
    [ "$n" -gt 0 ] || { echo "no scrape line after the first cycle (the sidecar keeps running: ACTION=stop)" >&2; exit 3; } ;;
  stop)
    [ -s "$pidf" ] || { echo "WC-0 is not running" >&2; exit 2; }
    while read -r pid; do kill -- "-$pid" 2> /dev/null || kill "$pid" 2> /dev/null || true; done < "$pidf"
    rm -f "$pidf"; ls -l "$logs"/wc0-*/ | tail -n 4 ;;
  *) echo "ACTION is start or stop" >&2; exit 2 ;;
esac
