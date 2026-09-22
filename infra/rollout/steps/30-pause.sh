#!/usr/bin/env bash
# The maintenance window opens: the edge (the release's Caddyfile, pinned Caddy) answers
# 503 dependency_unavailable + Retry-After, then the running gateway stops (drain.sh).
# Runs the release's deploy scripts from `git archive`, because the working tree still
# holds the running code. Saves the old Caddyfile first. Needs RELEASE.
# Abort before 50-install: `drain.sh resume` from the same directory.
set -euo pipefail
: "${RELEASE:?the release commit}"
repo=/home/ubuntu/model-inference
d=/root/infrx-deploy-$RELEASE
rm -rf "$d" && mkdir -p "$d"
sudo -u ubuntu git -C "$repo" archive "$RELEASE" apps/infrx-api/deploy | tar -x -C "$d"
d=$d/apps/infrx-api/deploy
mkdir -p /var/backups/infrx && chmod 0700 /var/backups/infrx
[ ! -f /etc/caddy/Caddyfile ] || cp -p /etc/caddy/Caddyfile "/var/backups/infrx/pre-$RELEASE.Caddyfile"
sudo -u ubuntu git -C "$repo" rev-parse HEAD > "/var/backups/infrx/pre-$RELEASE.head"
( . "$d/lib.sh"; edge_install "$d" )
"$d/drain.sh" pause
echo "paused; previous checkout $(cat "/var/backups/infrx/pre-$RELEASE.head")"
