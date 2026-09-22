#!/usr/bin/env bash
# Outside the maintenance window: fetch the release (the working tree is not touched -
# the running monolith imports from it) and pull the engine image its serve.sh pins, so
# the window does not wait on a multi-GB download. Needs RELEASE.
set -euo pipefail
: "${RELEASE:?the release commit}"
repo=/home/ubuntu/model-inference
g() { sudo -u ubuntu git -C "$repo" "$@"; }
g fetch --quiet origin
g cat-file -e "$RELEASE^{commit}"
image=$(g show "$RELEASE:models/marlin2b/serve.sh" | sed -n 's/^IMAGE=\${IMAGE:-\(.*\)}$/\1/p')
case "$image" in *@sha256:*) ;; *) echo "serve.sh at $RELEASE does not pin a digest: '$image'" >&2; exit 2 ;; esac
docker pull -q "$image"
echo "prepulled $image for $RELEASE"
