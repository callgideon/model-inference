#!/usr/bin/env bash
# Configuration revert: the previous checkout (the monolith and the engine unit run from
# the working tree) and the files install.sh backed up, then the engine restarted onto the
# restored unit. Needs RELEASE and BACKUP (the directory install.sh printed). Reverting a
# pilot to the unmetered monolith is refused by rollback.sh unless ROLLBACK_TO_UNMETERED=
# no-pilot-request-was-accepted is given - true only if no pilot request was accepted;
# otherwise use 95-maintenance.
set -euo pipefail
: "${RELEASE:?the release commit}" "${BACKUP:?the install.sh backup directory}"
repo=/home/ubuntu/model-inference
previous=$(cat "/var/backups/infrx/pre-$RELEASE.head")
d=/root/infrx-deploy-$RELEASE/apps/infrx-api/deploy
# The tree first: rollback.sh restarts the restored gateway unit, which runs from it.
sudo -u ubuntu git -C "$repo" checkout --quiet --detach "$previous"
"$d/rollback.sh" "$BACKUP"
systemctl restart marlin2b-vllm
echo "reverted to $previous with $BACKUP; the engine restarts onto the restored unit"
