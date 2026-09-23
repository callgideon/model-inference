# Shared by install.sh, drain.sh and rollback.sh; sourced, never run.
#
# INFRX_ROOT is the local rehearsal's sandbox (every host path is under it); empty on the
# box. The binaries these scripts call - systemctl, docker, curl, git, chown, tar - come
# from PATH, which is the only seam the tests and the rehearsal use.
ROOT=${INFRX_ROOT:-}
ENV_FILE=${ENV_FILE:-/etc/marlin2b-gateway.env}
UNIT_DIR=$ROOT/etc/systemd/system
CADDY_DIR=$ROOT/etc/caddy
BACKUPS=$ROOT/var/backups/infrx
STATE=$ROOT/var/lib/infrx
CADDY_IMAGE=caddy@sha256:14a9c00d4e833ebc2b65d36515b37bde3b73f0b323a2663aaafc88953d8c4e3f  # 2.11.4
# Start order is systemd's (After=); these lists are what each mode runs.
RUNTIME_UNITS_dev="marlin2b-gateway"
RUNTIME_UNITS_pilot="infrx-worker marlin2b-gateway"
UNIT_FILES="marlin2b-vllm.service marlin2b-gateway.service infrx-worker.service infrx-valkey.service"

die() { echo "$*" >&2; exit "${2:-1}"; }

# The mode of an env file, or "" (the pre-I2B monolith's file has none).
env_mode() { sed -n 's/^INFRX_MODE=//p' "$1" 2>/dev/null | tail -n1; }

# Copy $1 to $2 by rename, so a reader never sees half a file.
put() { cp "$1" "$2.tmp.$$" && chmod "${3:-0644}" "$2.tmp.$$" && mv -f "$2.tmp.$$" "$2"; }

# Poll until an HTTP GET answers 2xx, or give up after $2 seconds.
wait_http() {
  local deadline=$((SECONDS + $2))
  until curl -fsS -o /dev/null --max-time 5 "$1"; do
    [ "$SECONDS" -lt "$deadline" ] || return 1
    sleep "${POLL_S:-2}"
  done
}

# Make $1 (a file under $CADDY_DIR/infrx/) the active site and reload the edge. A
# restarted Caddy reads the active file, so maintenance survives a Caddy restart.
caddy_site() {
  put "$CADDY_DIR/infrx/$1" "$CADDY_DIR/Caddyfile"
  caddy_reload
}

# The admin API is a unix socket in the caddy_config volume (see the Caddyfile), never
# loopback: the gateway and the worker share the host's.
caddy_reload() {
  docker exec caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile --address unix//config/admin.sock
}

# Readiness per mode. pilot: the gateway's /readyz (G2) and the worker's loopback /readyz
# (W3's WorkerService: engine up, pool running, not draining) on WORKER_HEALTH_PORT, whose
# name and default 8002 are W3's request to the coordinator. dev: the generic /health.
WORKER_READY=http://127.0.0.1:${WORKER_HEALTH_PORT:-8002}/readyz
wait_ready() {
  if [ "$1" = pilot ]; then
    wait_http http://127.0.0.1:8001/readyz "${READY_S:-120}" && wait_http "$WORKER_READY" "${READY_S:-120}"
  else wait_http http://127.0.0.1:8001/health "${READY_S:-120}"; fi
}

# Validate the site with the pinned Caddy, install both sites, and serve the normal one:
# reload a running edge of the pinned image, or (re)create it. Host network (the box's
# layout); the directory, not the file, is mounted, so a rename of the active site is
# visible to a reload. INFRX_SITE is the rehearsal's address; unset on the box.
edge_install() {
  local src=$1 site=(${INFRX_SITE:+-e "INFRX_SITE=$INFRX_SITE"})
  docker run --rm --network none "${site[@]}" -v "$src/Caddyfile:/etc/caddy/Caddyfile:ro" \
    "$CADDY_IMAGE" caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null
  mkdir -p "$CADDY_DIR/infrx"
  put "$src/Caddyfile" "$CADDY_DIR/infrx/Caddyfile"
  put "$src/Caddyfile.maintenance" "$CADDY_DIR/infrx/Caddyfile.maintenance"
  if [ "$(docker inspect --format '{{.Config.Image}}' caddy 2>/dev/null || true)" = "$CADDY_IMAGE" ]; then
    caddy_site Caddyfile
  else
    put "$CADDY_DIR/infrx/Caddyfile" "$CADDY_DIR/Caddyfile"
    docker rm -f caddy >/dev/null 2>&1 || true
    docker run -d --name caddy --restart unless-stopped --network host "${site[@]}" \
      --cap-drop ALL --cap-add NET_BIND_SERVICE --read-only --tmpfs /tmp \
      -v "$CADDY_DIR:/etc/caddy:ro" -v caddy_data:/data -v caddy_config:/config \
      "$CADDY_IMAGE" >/dev/null
  fi
}
