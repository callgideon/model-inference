#!/usr/bin/env bash
# Abort before 50-install, after 91-abort brought the previous gateway back: the edge back
# to the file 25-save-edge.sh saved, checked against the sha256 it printed, rewritten in
# place (a single-file bind mount keeps its inode) and reloaded through the admin socket.
# Needs SAVED and SAVED_SHA256.
set -euo pipefail
: "${SAVED:?the path 25-save-edge.sh printed}" "${SAVED_SHA256:?the sha256 it printed}"
echo "$SAVED_SHA256  $SAVED" | sha256sum -c -
cat "$SAVED" > /etc/caddy/Caddyfile
docker exec caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile --address unix//config/admin.sock
echo "edge restored from $SAVED"
