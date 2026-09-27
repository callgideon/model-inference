#!/usr/bin/env bash
# E4C preconditions on the box, as root (E4C-runbook §0, §1 step 4, §2 source_sha; E4B-9aa7ffe.md:430),
# which no other step performs (certify-prep P-a..P-c):
#   A1  the public edge answers 200 from the box (the P4 burst leaves the box and comes back through Caddy)
#   1   the measurement checkout /opt/dlami/nvme/w3-checkout at RELEASE, clean and detached, from W1's
#       verified bundle. certify, e1b-window.sh and l8ref/l8served.sh run from it; 40-checkout.sh moves
#       only the deploy checkout /home/ubuntu/model-inference, which this step never touches
#   2   the certify image infrx-certify:$RELEASE (the runtime image + git), built once
#   3   a fresh engine inventory /opt/dlami/nvme/e4b/inventory.txt (certify --inventory); the previous
#       one is kept as inventory.<UTC>.prev
#   infra/rollout/ssm.sh infra/rollout/steps/76-e4c-prepare.sh RELEASE=<40-hex>
# Idempotent. Exit 2 before anything changes: the edge is not 200, a dirty checkout, a bundle that fails
# its sha256. Exit 3: no engine container or not the pinned image (image_equals_pin=yes absent; the old
# inventory stays).
set -euo pipefail
: "${RELEASE:?the release commit}"
nvme=${NVME:-/opt/dlami/nvme}; c=$nvme/w3-checkout; rel=$nvme/releases; inv=$nvme/e4b/inventory.txt
g() { git -c safe.directory='*' -C "$c" "$@"; }
code=$(curl -s -o /dev/null -m 15 -w '%{http_code}' https://marlin2b.callbill.ai/health || true)
echo "A1 edge /health from the box: $code"
[ "$code" = 200 ] || { echo "the edge is not open (maintenance?): nothing changed" >&2; exit 2; }
if [ -n "$(g status --porcelain)" ]; then
  echo "w3-checkout is dirty (move the files aside as w3-untracked-<UTC>/, then rerun):" >&2
  g status --porcelain | head -10 >&2; exit 2
fi
if [ -s "$rel/$RELEASE.sha256" ]; then echo "bundle $(cut -d' ' -f1 "$rel/$RELEASE.sha256") $RELEASE.bundle"; fi
if [ "$(g rev-parse HEAD)" != "$RELEASE" ]; then
  echo "w3-checkout was at $(g rev-parse HEAD)"
  ( cd "$rel" && sha256sum -c --quiet "$RELEASE.sha256" ) \
    || { echo "the release bundle fails its sha256: nothing changed" >&2; exit 2; }
  g fetch -q "$rel/$RELEASE.bundle" "+refs/infrx/release:refs/infrx/releases/$RELEASE"
  g checkout -q --detach "$RELEASE"
fi
[ "$(g rev-parse HEAD)" = "$RELEASE" ] && [ -z "$(g status --porcelain)" ] \
  || { echo "w3-checkout is not a clean $RELEASE" >&2; exit 2; }
echo "w3-checkout at $RELEASE (clean)"
docker image inspect "infrx-certify:$RELEASE" > /dev/null 2>&1 || \
  printf 'FROM infrx-runtime:%s\nUSER 0\nRUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*\n' \
    "$RELEASE" | docker build -q -t "infrx-certify:$RELEASE" - > /dev/null
echo "certify image infrx-certify:$RELEASE $(docker image inspect --format '{{.Id}}' "infrx-certify:$RELEASE")"
mkdir -p "$nvme/e4b"
rc=0
CONTAINER=marlin2b-8000 ENGINE=http://127.0.0.1:8000 WEIGHTS=$nvme/marlin2b \
  bash "$c/models/marlin2b/measure/inventory.sh" > "$inv.new" 2>&1 || rc=$?
if ! grep -qx 'image_equals_pin=yes' "$inv.new"; then     # inventory.sh's precondition exit 2 prints no pin line
  grep -E '^(precondition|image_equals_pin)=' "$inv.new" >&2 || true
  rm -f "$inv.new"; echo "inventory exit $rc: no container, or the engine is not the pinned image; $inv kept" >&2; exit 3
fi
if [ -s "$inv" ]; then cp -p "$inv" "$nvme/e4b/inventory.$(date -u +%Y%m%dT%H%M%SZ).prev"; fi
mv -f "$inv.new" "$inv"; chmod 644 "$inv"
echo "inventory $(sha256sum "$inv" | cut -d' ' -f1) $(wc -l < "$inv") lines"
grep -E '^(image_equals_pin|args)=' "$inv" | cut -c1-400
