#!/usr/bin/env bash
# Host truth as metrics (I8 slice 3), for the alert evaluator: the GPU as the host sees it
# (the gateway's container has no nvidia-smi, so its infrx_gpu_up is always 0 and is not
# the one the rule reads), the engine's loopback health, each unit, the two filesystems,
# the processing cache, whether the edge is in maintenance (a drain) and whether the public
# edge answers. Every label value is a fixed word from this file. Read-only; run as root by
# infrx-observe.service. Writes $OUT atomically.
set -euo pipefail
OUT=${OUT:-/var/lib/infrx/metrics/host.prom}
CACHE=${PROCESSING_CACHE_DIR:-/opt/dlami/nvme/processing}
PUBLIC=${PUBLIC_HEALTH:-https://marlin2b.callbill.ai/health}
CADDY=${CADDY_DIR:-/etc/caddy}
tmp=$(mktemp "${OUT}.XXXXXX")
trap 'rm -f "$tmp"' EXIT
m() { printf '%s{process="host"%s} %s\n' "$1" "${3:+,$3}" "$2" >> "$tmp"; }
ok() { if "$@" >/dev/null 2>&1; then echo 1; else echo 0; fi; }

m infrx_gpu_up "$(ok timeout 10 nvidia-smi --query-gpu=index --format=csv,noheader)"
m infrx_engine_up "$(ok curl -fsS -o /dev/null --max-time 5 http://127.0.0.1:8000/health)"
for unit in marlin2b-vllm marlin2b-gateway infrx-worker infrx-valkey; do
  m infrx_unit_active "$(ok systemctl is-active --quiet "$unit.service")" "unit=\"$unit\""
done
for pair in root:/ nvme:/opt/dlami/nvme; do
  mount=${pair%%:*} path=${pair#*:}
  ratio=$(df -P "$path" 2>/dev/null | awk 'NR==2 && $2 > 0 {printf "%.6f", $4 / $2}' || true)
  m infrx_disk_free_ratio "${ratio:-0}" "mount=\"$mount\""
done
bytes=$(timeout 30 du -sb "$CACHE" 2>/dev/null | cut -f1 || true)
m infrx_processing_cache_bytes "${bytes:-0}"
maintenance=0
if [ -f "$CADDY/infrx/Caddyfile.maintenance" ] && cmp -s "$CADDY/Caddyfile" "$CADDY/infrx/Caddyfile.maintenance"; then
  maintenance=1
fi
m infrx_edge_maintenance "$maintenance"
m infrx_edge_public_up "$(ok curl -fsS -o /dev/null --max-time 10 "$PUBLIC")"
m infrx_host_probe_timestamp_seconds "$(date +%s)"
chmod 0644 "$tmp"
mv -f "$tmp" "$OUT"
trap - EXIT
