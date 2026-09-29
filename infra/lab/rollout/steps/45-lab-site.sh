#!/usr/bin/env bash
# LAB-DEPLOY-PREP runbook step L6 (box): the Lab control origin on the App edge (I2L README §5).
#   infra/rollout/ssm.sh infra/lab/rollout/steps/45-lab-site.sh STATE=on RELEASE=<40 hex>
#   infra/rollout/ssm.sh infra/lab/rollout/steps/45-lab-site.sh STATE=off
# on: the release's lab-control.caddy is validated WITH the App's live Caddyfile in a staged
# copy by the pinned Caddy (no network); only then renamed into /etc/caddy/lab and the edge
# reloaded. A file the pinned Caddy refuses never reaches the edge (exit 4, the edge unchanged).
# off: the site removed and the edge reloaded. Needs WR-I2L-1's import line in the App Caddyfile.
# Idempotent. Exit 2 = refused before any change (not RELEASE, no import line).
set -euo pipefail
STEP=45-lab-site
repo=${REPO:-/home/ubuntu/model-inference}
. "$repo/infra/lab/rollout/lib.sh"
caddy_dir=$R/etc/caddy
site=$caddy_dir/lab/lab-control.caddy
reload() { docker exec caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile --address unix//config/admin.sock; }
case "${STATE:-}" in
  off) if [ -f "$site" ]; then rm -f "${site:?}"; reload; say "site removed; edge reloaded"
       else say "no Lab site installed; nothing to do"; fi; exit 0 ;;
  on) ;;
  *) die 2 "STATE must be on or off" ;;
esac
at_release
grep -qx 'import /etc/caddy/lab/\*.caddy' "$caddy_dir/Caddyfile" \
  || die 2 "the live App Caddyfile has no 'import /etc/caddy/lab/*.caddy' (WR-I2L-1): install an App release that carries it first"
image=$(sed -n 's/^CADDY_IMAGE=\([^ ]*\).*/\1/p' "$repo/apps/infrx-api/deploy/lib.sh")
stage=$(mktemp -d)
trap 'rm -rf "${stage:?}"' EXIT
cp -r "$caddy_dir/." "$stage/"
install -d "$stage/lab"
cp "$repo/apps/infrx-api/deploy/lab/app/lab-control.caddy" "$stage/lab/lab-control.caddy"
docker run --rm --network none -v "$stage:/etc/caddy:ro" "$image" \
  caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile \
  || die 4 "the App's Caddyfile with the Lab site does not validate with the pinned Caddy; the edge is unchanged"
install -d -m 0755 "$caddy_dir/lab"
cp "$stage/lab/lab-control.caddy" "$site.tmp"
mv -f "$site.tmp" "$site"
reload
say "Lab site installed and the edge reloaded (validated with the App's Caddyfile first)"
