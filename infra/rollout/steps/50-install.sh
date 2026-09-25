#!/usr/bin/env bash
# The cutover: install.sh in pilot mode - image built from RELEASE, secrets read and
# probed inside that image, env file replaced by one rename, units installed, the engine
# RESTARTED onto its pinned image and media root, runtime restarted, gateway and worker
# /readyz, and only then the edge's normal site. Exit 2 = refused, nothing changed (the
# edge stays in maintenance); 4 = not ready (run 90-revert or 95-maintenance). Needs RELEASE,
# ENGINE_MAX_NUM_SEQS (rollout.md §1's pin, no default) and MIGRATION_DIGEST - the plan
# digest step 6 applied, or `nothing-pending` - so the cutover cannot start without the
# operator having run step 6. The box cannot see hosted history, so this is the operator's
# statement, recorded in the output, not a check of it.
set -euo pipefail
: "${RELEASE:?the release commit}"
: "${MIGRATION_DIGEST:?the plan digest step 6 applied, or nothing-pending}"
: "${ENGINE_MAX_NUM_SEQS:?rollout.md section 1 pins it (INSTALL_ARGS); no default}"
[[ $MIGRATION_DIGEST =~ ^([0-9a-f]{64}|nothing-pending)$ ]] \
  || { echo "MIGRATION_DIGEST is neither a plan digest (64 hex) nor nothing-pending" >&2; exit 2; }
echo "cutover $RELEASE after hosted migrations: $MIGRATION_DIGEST"
cd /home/ubuntu/model-inference
[ "$(git -c safe.directory="$PWD" rev-parse HEAD)" = "$RELEASE" ] || { echo "run 40-checkout first" >&2; exit 2; }
# The release's serve.sh reads ENGINE_MAX_NUM_SEQS from the env file. It is required, like
# RELEASE: the old default (32, the pre-release unit's value) silently overrode the runbook's
# pin when the argument was left out. PROCESSING_CACHE_DIR is written by preflight's default.
INFRX_MODE=pilot RELEASE="$RELEASE" ENGINE=restart ENV_OWNER=ubuntu \
  INFRX_SET="ENGINE_MAX_NUM_SEQS=$ENGINE_MAX_NUM_SEQS ${INFRX_SET:-}" \
  ./apps/infrx-api/deploy/install.sh
