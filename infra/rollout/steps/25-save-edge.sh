#!/usr/bin/env bash
# Before 30-pause: keep the edge exactly as it serves now (the W4 box lane's pattern), so an
# abort before the cutover puts it back byte for byte (93-restore-edge.sh). Read-only for
# the running services. Record the printed path and sha256 in the lock record.
set -euo pipefail
d=/opt/dlami/nvme/w4-logs
mkdir -p "$d"
saved=$d/Caddyfile.live-$(date -u +%Y%m%dT%H%M%SZ)
cp -p /etc/caddy/Caddyfile "$saved"
sha256sum "$saved"
docker inspect caddy --format 'caddy image={{.Config.Image}} mounts={{range .Mounts}}{{.Source}}->{{.Destination}} {{end}}'
echo "saved=$saved"
