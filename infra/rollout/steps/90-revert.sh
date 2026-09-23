#!/usr/bin/env bash
# Configuration revert: the previous checkout (the monolith and the engine unit run from
# the working tree), the files install.sh backed up, the engine restarted onto the restored
# unit and healthy, then the runtime, then the edge reopened. Needs RELEASE and BACKUP (the
# directory install.sh printed). Exit 4 = the engine or the runtime did not come up: the
# edge stays in maintenance; fix it, then `drain.sh resume` from the same directory. Reverting a pilot to the unmetered monolith is refused by rollback.sh unless
# ROLLBACK_TO_UNMETERED=no-pilot-request-was-accepted is given - true only if no pilot
# request was accepted; otherwise use 95-maintenance.
set -euo pipefail
: "${RELEASE:?the release commit}" "${BACKUP:?the install.sh backup directory}"
repo=/home/ubuntu/model-inference
previous=$(cat "/var/backups/infrx/pre-$RELEASE.head")
d=/root/infrx-deploy-$RELEASE/apps/infrx-api/deploy
# The tree first: rollback.sh restarts the restored units, which run from it. ENGINE=restart:
# the engine first and healthy, because the restored gateway's /health asks it (step 8 may
# have failed on the engine itself).
sudo -u ubuntu git -C "$repo" checkout --quiet --detach "$previous"
ENGINE=restart "$d/rollback.sh" "$BACKUP"
# Step 8's backup was taken after 30-pause made maintenance the active site, so rollback.sh
# restored maintenance: reopen the edge, now that the restored runtime is ready.
"$d/drain.sh" resume
echo "reverted to $previous with $BACKUP; engine and runtime restored and ready; the edge serves"
