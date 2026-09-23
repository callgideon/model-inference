#!/usr/bin/env bash
# The cutover: install.sh in pilot mode - image built from RELEASE, secrets read and
# probed inside that image, env file replaced by one rename, units installed, the engine
# RESTARTED onto its pinned image and media root, runtime restarted, gateway and worker
# /readyz, and only then the edge's normal site. Exit 2 = refused, nothing changed (the
# edge stays in maintenance); 4 = not ready (run 90-revert or 95-maintenance). Needs RELEASE
# and MIGRATION_DIGEST - the plan digest step 6 applied, or `nothing-pending` - so the
# cutover cannot start without the operator having run step 6. The box cannot see hosted
# history, so this is the operator's statement, recorded in the output, not a check of it.
set -euo pipefail
: "${RELEASE:?the release commit}"
: "${MIGRATION_DIGEST:?the plan digest step 6 applied, or nothing-pending}"
echo "cutover $RELEASE after hosted migrations: $MIGRATION_DIGEST"
cd /home/ubuntu/model-inference
[ "$(git -c safe.directory="$PWD" rev-parse HEAD)" = "$RELEASE" ] || { echo "run 40-checkout first" >&2; exit 2; }
INFRX_MODE=pilot RELEASE="$RELEASE" ENGINE=restart ENV_OWNER=ubuntu \
  ./apps/infrx-api/deploy/install.sh
