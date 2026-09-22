#!/usr/bin/env bash
# Deploy the Marlin backend on the box - install or upgrade, idempotent, run as root:
#
#   sudo INFRX_MODE=pilot [RELEASE=<git sha>] ./apps/infrx-api/deploy/install.sh
#
# Order, stopping at the first failure (infra/README.md §7; the runbook runs drain.sh and
# migrate.py before this, and this contains no SQL):
#   1. the checkout is exactly a commit (RELEASE, if given, is that commit);
#   2. build the pinned runtime image from it; its content id is the pin;
#   3. back up every file this run may replace (rollback.sh restores it);
#   4. preflight.py apply: read every secret, validate, probe INSIDE the image, replace
#      the env file by one rename - or refuse with everything untouched (exit 2);
#   5. install the unit files (inert until restarted), start the index and the engine;
#   6. restart the runtime units, wait for readiness (pilot: /readyz);
#   7. only then the edge: validate the Caddyfile with the pinned Caddy and serve it.
# A refusal at 1-4 leaves the host exactly as it was. A failure at 5-7 leaves a validated
# configuration installed and says which backup to roll back to; it does not roll back
# on its own (a validated file is not replaced by an unvalidated guess).
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo=$(cd "$here/../../.." && pwd)
# shellcheck source=lib.sh
. "$here/lib.sh"
REGION=${AWS_REGION:-us-east-1}
# Before anything else: an unusable mode must not leave half a deploy behind.
case "${INFRX_MODE:-}" in
  dev|test|pilot) ;;
  *) die "INFRX_MODE must be dev, test or pilot (no default); got '${INFRX_MODE:-}'" 2 ;;
esac
mode=$INFRX_MODE
case $mode in pilot) runtime_units=$RUNTIME_UNITS_pilot ;; *) runtime_units=$RUNTIME_UNITS_dev ;; esac
SERVE_SCRIPT=${SERVE_SCRIPT:-$repo/models/marlin2b/serve.sh}
PREFLIGHT=${PREFLIGHT:-$here/preflight.py}
PYTHON=${PYTHON:-python3}           # the installer's interpreter; preflight's apply is stdlib
ENV_OWNER=${ENV_OWNER:-ubuntu}

# 1. exactly a commit
sha=$(git -C "$repo" rev-parse HEAD)
[ -z "$(git -C "$repo" status --porcelain)" ] || die "the checkout has uncommitted changes; deploy a commit" 2
[ -z "${RELEASE:-}" ] || [ "$sha" = "$RELEASE" ] || die "HEAD is $sha, not RELEASE=$RELEASE" 2

# 2. the runtime image
docker build -q -f "$here/Dockerfile" -t "infrx-runtime:$sha" "$repo/apps/infrx-api" >/dev/null
image=$(docker image inspect --format '{{.Id}}' "infrx-runtime:$sha")
echo "release $sha image $image"

# 3. the backup: every path this run may replace, and which of them did not exist
backup=$BACKUPS/$(date -u +%Y%m%dT%H%M%SZ)-$sha
mkdir -p "$backup" && chmod 0700 "$BACKUPS" "$backup"
paths=("${ENV_FILE#/}" etc/caddy/Caddyfile etc/caddy/infrx/Caddyfile etc/caddy/infrx/Caddyfile.maintenance)
for f in $UNIT_FILES; do paths+=("etc/systemd/system/$f"); done
present=()
: > "$backup/absent"
for p in "${paths[@]}"; do
  if [ -e "$ROOT/$p" ]; then present+=("$p"); else echo "$p" >> "$backup/absent"; fi
done
tar -C "${ROOT:-/}" -cpf "$backup/files.tar" --files-from /dev/null "${present[@]}"
echo "backup $backup"

# 4. the env file (the only step that reads secrets); refused -> nothing above mattered
sets=()
for pair in ${INFRX_SET:-}; do sets+=(--set "$pair"); done
"$PYTHON" "$PREFLIGHT" apply --mode "$mode" --env-file "$ROOT$ENV_FILE" --owner "$ENV_OWNER" \
  --region "$REGION" --image "$image" --serve-script "$SERVE_SCRIPT" "${sets[@]}"

# 5. state directories, units, the index and the engine
mkdir -p "$STATE/usage" "$STATE/media"
chown 10001:10000 "$STATE/usage" "$STATE/media"
chmod 0750 "$STATE/usage" && chmod 2750 "$STATE/media"
mkdir -p "$UNIT_DIR"
for f in $UNIT_FILES; do put "$here/$f" "$UNIT_DIR/$f"; done
systemctl daemon-reload
if [ "$mode" = pilot ]; then
  systemctl enable marlin2b-vllm infrx-valkey $runtime_units infrx-reaper.timer
  systemctl start infrx-valkey
else
  systemctl enable marlin2b-vllm $runtime_units
fi
# The engine reads no env file and takes minutes to load, so it is started (a no-op when
# it is up), never restarted by an install; a new engine is W3's separate step.
systemctl start marlin2b-vllm
wait_http http://127.0.0.1:8000/health "${ENGINE_READY_S:-900}" \
  || die "the engine is not healthy; nothing else was restarted (backup $backup)" 4

# 6. the runtime, then readiness
systemctl restart $runtime_units
wait_ready "$mode" || die "the runtime did not become ready; the edge was not changed. Roll back with: $here/rollback.sh $backup" 4
if [ "$mode" = pilot ]; then systemctl start infrx-reaper.timer; fi

# 7. the edge - pilot only (a dev host answering on the pilot's name is an unmetered pilot)
if [ "$mode" = pilot ]; then
  site=(${INFRX_SITE:+-e "INFRX_SITE=$INFRX_SITE"})   # the rehearsal's address; unset on the box
  docker run --rm --network none "${site[@]}" -v "$here/Caddyfile:/etc/caddy/Caddyfile:ro" "$CADDY_IMAGE" \
    caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null
  mkdir -p "$CADDY_DIR/infrx"
  put "$here/Caddyfile" "$CADDY_DIR/infrx/Caddyfile"
  put "$here/Caddyfile.maintenance" "$CADDY_DIR/infrx/Caddyfile.maintenance"
  if [ "$(docker inspect --format '{{.Config.Image}}' caddy 2>/dev/null || true)" = "$CADDY_IMAGE" ]; then
    caddy_site Caddyfile
  else
    put "$CADDY_DIR/infrx/Caddyfile" "$CADDY_DIR/Caddyfile"
    docker rm -f caddy >/dev/null 2>&1 || true
    # Host network (the box's layout); the directory, not the file, is mounted, so a
    # rename of the active site is visible to a reload.
    docker run -d --name caddy --restart unless-stopped --network host "${site[@]}" \
      --cap-drop ALL --cap-add NET_BIND_SERVICE --read-only --tmpfs /tmp \
      -v "$CADDY_DIR:/etc/caddy:ro" -v caddy_data:/data -v caddy_config:/config \
      "$CADDY_IMAGE" >/dev/null
  fi
fi
echo "deployed $sha ($mode); image $image; rollback: $here/rollback.sh $backup"
