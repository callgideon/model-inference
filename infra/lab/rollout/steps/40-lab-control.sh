#!/usr/bin/env bash
# LAB-DEPLOY-PREP runbook step L5 (box): the Lab control service switch.
#   infra/rollout/ssm.sh infra/lab/rollout/steps/40-lab-control.sh STATE=on RELEASE=<40 hex> \
#     CONTROL_DSN_PARAM=/model-inference/lab/control_database_url \
#     ANON_KEY_PARAM=/model-inference/lab/supabase_anon_key \
#     SUPABASE_URL=https://<project>.supabase.co LAB_ORIGIN=https://lab.callbill.ai
#   infra/rollout/ssm.sh infra/lab/rollout/steps/40-lab-control.sh STATE=off
# on: /etc/infrx-lab-control.env (root, 0600, by rename) from the two SSM parameters (read by
# NAME on the box) and the two https origins, INFRX_LAB_IMAGE from 20-lab-image.sh; then the
# marker, enable + restart, and 127.0.0.1:8003/readyz. off: disable --now, marker removed.
# Exit 2 = refused before any change; 4 = started but not ready (journalctl -u infrx-lab-control).
# Idempotent. The App's units, env file and edge are never touched.
set -euo pipefail
STEP=40-lab-control
repo=${REPO:-/home/ubuntu/model-inference}
. "$repo/infra/lab/rollout/lib.sh"
case "${STATE:-}" in
  off) systemctl disable --now infrx-lab-control.service 2>/dev/null || true
       rm -f "${MARKER:?}"; say "control OFF (unit disabled, marker removed)"; exit 0 ;;
  on) ;;
  *) die 2 "STATE must be on or off" ;;
esac
at_release
: "${CONTROL_DSN_PARAM:?the SSM name of the control login DSN}" "${ANON_KEY_PARAM:?the SSM name of the publishable anon key}"
https='^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?$'
[[ ${SUPABASE_URL:-} =~ $https ]] || die 2 "SUPABASE_URL must be an https origin, no path"
[[ ${LAB_ORIGIN:-} =~ $https ]] || die 2 "LAB_ORIGIN must be the Lab's https origin, no path"
[ -f "$(unit_file control)" ] || die 2 "infrx-lab-control.service is not installed: run 30-lab-units.sh"
image=$(lab_image)
install -d -m 0755 "$LAB_ETC"
staged=$(stage_env "$CONTROL_ENV" "INFRX_LAB_IMAGE:=$image" \
  "INFRX_LAB_DATABASE_URL=$CONTROL_DSN_PARAM" "INFRX_LAB_SUPABASE_URL:=$SUPABASE_URL" \
  "INFRX_LAB_SUPABASE_ANON_KEY=$ANON_KEY_PARAM" "INFRX_LAB_ORIGIN:=$LAB_ORIGIN")
place "$staged" "$CONTROL_ENV" root:root
touch "$MARKER"
systemctl enable infrx-lab-control.service
systemctl restart infrx-lab-control.service
ready "$CONTROL_PORT" || die 4 "control not ready on 127.0.0.1:$CONTROL_PORT (journalctl -u infrx-lab-control); STATE=off turns it back off"
say "control ON: 127.0.0.1:$CONTROL_PORT/readyz 200"
