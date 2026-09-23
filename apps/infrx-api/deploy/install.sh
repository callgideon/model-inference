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
#   6. restart the runtime units, wait for readiness (pilot: gateway and worker /readyz);
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
# root runs this against ubuntu's checkout: name it safe for this one command, or git
# refuses ("dubious ownership") and the deploy stops here.
g() { git -c safe.directory="$repo" -C "$repo" "$@"; }
sha=$(g rev-parse HEAD)
[ -z "$(g status --porcelain)" ] || die "the checkout has uncommitted changes; deploy a commit" 2
[ -z "${RELEASE:-}" ] || [ "$sha" = "$RELEASE" ] || die "HEAD is $sha, not RELEASE=$RELEASE" 2

# 2. the runtime image
# No provenance attestation: with the containerd image store it carries a build timestamp
# into the index, so the same commit would get a new id on every build.
docker build -q --provenance=false -f "$here/Dockerfile" -t "infrx-runtime:$sha" "$repo/apps/infrx-api" >/dev/null
image=$(docker image inspect --format '{{.Id}}' "infrx-runtime:$sha")
echo "release $sha image $image"

# 3. the backup: every path this run may replace, and which of them did not exist. It holds
# the previous env file - secrets - so it is root-only (0700, the archive 0600), and a run
# that step 4 refuses removes it again.
# Never an existing directory: a second run writing into it would overwrite that run's copy,
# and a refused one would delete it. The name carries nanoseconds and still sorts by time.
backup=$BACKUPS/$(date -u +%Y%m%dT%H%M%S.%NZ)-$sha
mkdir -p "$BACKUPS"
mkdir "$backup" 2>/dev/null || die "backup $backup exists already; nothing was changed" 2
chmod 0700 "$BACKUPS" "$backup"
paths=("${ENV_FILE#/}" etc/caddy/Caddyfile etc/caddy/infrx/Caddyfile etc/caddy/infrx/Caddyfile.maintenance)
for f in $UNIT_FILES; do paths+=("etc/systemd/system/$f"); done
present=()
: > "$backup/absent"
for p in "${paths[@]}"; do
  if [ -e "$ROOT/$p" ]; then present+=("$p"); else echo "$p" >> "$backup/absent"; fi
done
( umask 077; tar -C "${ROOT:-/}" -cpf "$backup/files.tar" --files-from /dev/null "${present[@]}" )
echo "backup $backup"

# 4. the env file (the only step that reads secrets); refused -> nothing above mattered
sets=()
for pair in ${INFRX_SET:-}; do sets+=(--set "$pair"); done
"$PYTHON" "$PREFLIGHT" apply --mode "$mode" --env-file "$ROOT$ENV_FILE" --owner "$ENV_OWNER" \
  --region "$REGION" --image "$image" --release "$sha" --serve-script "$SERVE_SCRIPT" "${sets[@]}" \
  || { code=$?; rm -rf "$backup"; exit "$code"; }

# 5. state directories, units, the index and the engine
# The usage spill is on the root EBS volume (row M-SCRATCH); the media root is recreated by
# the units themselves at every start, because the NVMe it lives on is wiped by a stop.
mkdir -p "$STATE/usage"
chmod 0750 "$STATE/usage"
chown 10001:10000 "$STATE/usage"
mkdir -p "$UNIT_DIR"
for f in $UNIT_FILES; do put "$here/$f" "$UNIT_DIR/$f"; done
systemctl daemon-reload
if [ "$mode" = pilot ]; then
  systemctl enable marlin2b-vllm infrx-valkey $runtime_units
  systemctl start infrx-valkey
else
  systemctl enable marlin2b-vllm $runtime_units
fi
# The engine takes minutes to load, so it is started (a no-op when it is up) and not
# restarted - unless ENGINE=restart, which the runbook's cutover sets: a running engine
# keeps its old image, flags and mounts until it restarts.
if [ "${ENGINE:-start}" = restart ]; then systemctl restart marlin2b-vllm
else systemctl start marlin2b-vllm; fi
wait_http http://127.0.0.1:8000/health "${ENGINE_READY_S:-900}" \
  || die "the engine is not healthy; nothing else was restarted (backup $backup)" 4

# 6. the runtime, then readiness
systemctl restart $runtime_units
wait_ready "$mode" || die "the runtime did not become ready; the edge was not changed. Roll back with: $here/rollback.sh $backup" 4

# 7. the edge - pilot only (a dev host answering on the pilot's name is an unmetered pilot)
if [ "$mode" = pilot ]; then edge_install "$here"; fi
echo "deployed $sha ($mode); image $image; rollback: $here/rollback.sh $backup"
