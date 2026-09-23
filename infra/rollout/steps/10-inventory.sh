#!/usr/bin/env bash
# Read-only: what the box is before (and after) a step. Prints names, never values.
set -euo pipefail
repo=/home/ubuntu/model-inference
g() { git -c safe.directory="$repo" -C "$repo" "$@"; }
echo "== host";      hostname; uptime; df -h / /opt/dlami/nvme | sed 1d
echo "== docker";    docker version --format 'client {{.Client.Version}} server {{.Server.Version}}'
                     docker buildx version 2>/dev/null || echo "no buildx: install.sh's --provenance=false needs it"
                     docker info --format 'storage driver {{.Driver}}'
echo "== checkout";  g rev-parse HEAD; g status --porcelain | head -n 20
echo "== units";     systemctl list-units 'marlin2b-*' 'infrx-*' --all --no-pager --plain --no-legend || true
echo "== env names"; if [ -f /etc/marlin2b-gateway.env ]; then cut -d= -f1 /etc/marlin2b-gateway.env; else echo none; fi
echo "== containers"; docker ps -a --format '{{.Names}} {{.Image}} {{.Status}}'
echo "== edge volumes (the certificates live in the /data one)"
docker inspect caddy --format '{{range .Mounts}}{{.Type}} {{.Name}}{{.Source}} -> {{.Destination}}; {{end}}' 2>/dev/null || echo "no caddy container"
echo "== listeners (non-loopback)"; ss -ltnH | awk '{print $4}' | grep -vE '^(127\.0\.0\.1|\[::1\]):' | sort -u || true
echo "== backups";   ls /var/backups/infrx 2>/dev/null || echo none
