#!/usr/bin/env bash
# The cutover: install.sh in pilot mode - image built from RELEASE, secrets read and
# probed inside that image, env file replaced by one rename, units installed, the engine
# RESTARTED onto its pinned image and media root, runtime restarted, gateway and worker
# /readyz, and only then the edge's normal site. Exit 2 = refused, nothing changed (the
# edge stays in maintenance); 4 = not ready (run 90-revert or 95-maintenance). Needs RELEASE.
set -euo pipefail
: "${RELEASE:?the release commit}"
cd /home/ubuntu/model-inference
[ "$(git -c safe.directory="$PWD" rev-parse HEAD)" = "$RELEASE" ] || { echo "run 40-checkout first" >&2; exit 2; }
INFRX_MODE=pilot RELEASE="$RELEASE" ENGINE=restart ENV_OWNER=ubuntu \
  ./apps/infrx-api/deploy/install.sh
