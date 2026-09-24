#!/usr/bin/env bash
# I8 slice 6, box, read-only: can the box reinstall TARGET right now, and what do the install
# backups really hold? A rollback target is chosen by infra/rollout/known-good.py (its tree +
# the evidence record), then checked here: its release bundle is on the NVMe and matches its
# sha256, the checkout has the commit, and whether its runtime image is cached (a rebuild
# otherwise: minutes, not seconds). The backups are listed with the release each one's env
# file holds - a backup is NAMED for the release that was being installed and HOLDS the one
# before it (F7: the only backup holds 27af05a). Only INFRX_RELEASE_SHA is read from a backup.
# Needs TARGET (40 hex). Exit 0 ready, 1 not ready (the W1 fetch step first).
set -euo pipefail
: "${TARGET:?the release to roll back to}"
[[ $TARGET =~ ^[0-9a-f]{40}$ ]] || { echo "TARGET is a full commit id" >&2; exit 2; }
repo=${REPO:-/home/ubuntu/model-inference}
releases=${RELEASES_DIR:-/opt/dlami/nvme/releases}
backups=${BACKUPS:-/var/backups/infrx}
ready=0
if [ -f "$releases/$TARGET.bundle" ] && ( cd "$releases" && sha256sum -c --status "$TARGET.sha256" ); then
  echo "bundle: $releases/$TARGET.bundle matches its sha256"
else
  echo "bundle: MISSING or not matching - run the release's fetch step (W1) first"; ready=1
fi
if git -c safe.directory="$repo" -C "$repo" cat-file -e "$TARGET^{commit}" 2>/dev/null; then
  echo "checkout: has $TARGET"
else
  echo "checkout: does NOT have $TARGET"; ready=1
fi
if docker image inspect "infrx-runtime:$TARGET" >/dev/null 2>&1; then
  echo "image: infrx-runtime:$TARGET cached (install reuses it)"
else
  echo "image: not cached - install.sh builds it (minutes; the edge stays in maintenance meanwhile)"
fi
echo "== install backups: named for -> hold"
for dir in "$backups"/*/; do
  [ -f "$dir/files.tar" ] || continue
  held=$(tar -xOf "$dir/files.tar" etc/marlin2b-gateway.env 2>/dev/null | sed -n 's/^INFRX_RELEASE_SHA=//p' | tail -n1)
  name=$(basename "$dir")
  echo "$name -> ${held:-a pre-pilot runtime (no INFRX_RELEASE_SHA)}"
done
exit "$ready"
