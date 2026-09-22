#!/usr/bin/env bash
# Inside the window: the working tree becomes exactly RELEASE (clean, detached). The
# previous HEAD was recorded by 30-pause for the revert. Needs RELEASE.
set -euo pipefail
: "${RELEASE:?the release commit}"
repo=/home/ubuntu/model-inference
g() { sudo -u ubuntu git -C "$repo" "$@"; }
[ -z "$(g status --porcelain)" ] || { echo "the checkout is dirty; not touching it" >&2; exit 2; }
g checkout --quiet --detach "$RELEASE"
[ "$(g rev-parse HEAD)" = "$RELEASE" ]
echo "checked out $RELEASE"
