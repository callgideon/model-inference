#!/usr/bin/env bash
# Abort a window in which 50-install refused (exit 2: nothing was installed): the previous
# checkout back, the previous gateway started, and the edge reopened only after it is
# healthy (drain.sh resume). Needs RELEASE.
set -euo pipefail
: "${RELEASE:?the release commit}"
repo=/home/ubuntu/model-inference
sudo -u ubuntu git -C "$repo" checkout --quiet --detach "$(cat "/var/backups/infrx/pre-$RELEASE.head")"
/root/infrx-deploy-"$RELEASE"/apps/infrx-api/deploy/drain.sh resume
