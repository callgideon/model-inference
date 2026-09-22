#!/usr/bin/env bash
# On the box, after 50-install: units, readiness, least privilege as actually applied,
# listeners, env names. Read-only.
set -euo pipefail
systemctl is-active marlin2b-vllm infrx-valkey infrx-worker marlin2b-gateway infrx-reaper.timer
curl -fsS -o /dev/null --max-time 5 http://127.0.0.1:8001/readyz && echo "readyz 200"
curl -fsS -o /dev/null --max-time 5 http://127.0.0.1:8000/health && echo "engine health 200 (loopback)"
for c in infrx-gateway infrx-worker infrx-valkey marlin2b-8000 caddy; do
  docker inspect --format '{{.Name}} image={{.Image}} user={{.Config.User}} ro={{.HostConfig.ReadonlyRootfs}} capdrop={{.HostConfig.CapDrop}} secopt={{.HostConfig.SecurityOpt}} mem={{.HostConfig.Memory}}' "$c"
done
echo "== non-loopback listeners (expect :80, :443 and sshd only)"
ss -ltnH | awk '{print $4}' | grep -vE '^(127\.0\.0\.1|\[::1\]):' | sort -u || true
echo "== env names"; cut -d= -f1 /etc/marlin2b-gateway.env
grep -c '^INFRX_MODE=pilot$' /etc/marlin2b-gateway.env
! grep -q '^GATEWAY_API_KEY=' /etc/marlin2b-gateway.env && echo "no shared legacy key (R51)"
