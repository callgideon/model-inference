#!/usr/bin/env bash
# I8 slice 3 on the box: install the monitoring units (infra/observe/systemd/: not in the
# runtime's deploy dir, whose only timer-free reaper path W3 pins) from the checked-out release, write
# their three env files from SSM (values read on the box into 0600 files, never printed, never
# an argument), start the timers and run one cycle. Idempotent. Needs RELEASE (the checkout
# must be it) and CANARY_VIDEO (an in-cap clip on the box, e.g. the 8 s sop-synth clip).
#   CANARY_KEY_PARAM   SSM name of the canary tenant's scoped key (default
#                      /model-inference/e4b_api_key - the certify/E1B key - until a dedicated
#                      canary key exists; P-24 records the spend)
#   ALERT_WEBHOOK_PARAM  SSM name of the P-25 destination; unset = delivery stays BLOCKED
#   ALERT_OWNER, ALERT_ESCALATION   P-25's owner and escalation (names/handles, not secrets)
#   MONITOR_DSN_PARAM  SSM name of D10's read-only monitor DSN; unset = the runtime DSN on 6543
# Rollback: systemctl disable --now infrx-observe.timer infrx-canary.timer; rm the four unit
# files and /etc/infrx-{canary,alert,observe}.env.
set -euo pipefail
: "${RELEASE:?the release commit}" "${CANARY_VIDEO:?an in-cap clip on the box}"
repo=${REPO:-/home/ubuntu/model-inference}
R=${INFRX_ROOT:-}                     # empty on the box; a sandbox root in tests (lib.sh's seam)
[ "$(git -c safe.directory="$repo" -C "$repo" rev-parse HEAD)" = "$RELEASE" ] \
  || { echo "the checkout is not $RELEASE" >&2; exit 2; }
[ -f "$CANARY_VIDEO" ] || { echo "no clip at CANARY_VIDEO" >&2; exit 2; }
param() { aws ssm get-parameter --region us-east-1 --with-decryption --name "$1" \
            --query Parameter.Value --output text; }
write_env() {  # write_env FILE NAME=SSM-PARAM|NAME:=LITERAL ... - 0600 root, by rename
  local file=$1 tmp; shift
  tmp=$(umask 077; mktemp "$file.XXXXXX")
  for spec in "$@"; do
    case "$spec" in
      *:=*) printf '%s=%s\n' "${spec%%:=*}" "${spec#*:=}" >> "$tmp" ;;
      *=*)  printf '%s=%s\n' "${spec%%=*}" "$(param "${spec#*=}")" >> "$tmp" ;;
    esac
  done
  chmod 0600 "$tmp"; mv -f "$tmp" "$file"
  echo "wrote $file: $(cut -d= -f1 "$file" | tr '\n' ' ')"
}
install -d -o 10001 -g 10000 -m 0770 "$R/var/lib/infrx/metrics"
for unit in infrx-observe.service infrx-observe.timer infrx-canary.service infrx-canary.timer; do
  install -m 0644 "$repo/infra/observe/systemd/$unit" "$R/etc/systemd/system/$unit"
done
write_env "$R/etc/infrx-canary.env" "INFRX_CANARY_KEY=${CANARY_KEY_PARAM:-/model-inference/e4b_api_key}" \
  "CANARY_VIDEO:=$CANARY_VIDEO"
alert=("ALERT_OWNER:=${ALERT_OWNER:-UNSET (P-25)}" "ALERT_ESCALATION:=${ALERT_ESCALATION:-UNSET (P-25)}")
[ -n "${ALERT_WEBHOOK_PARAM:-}" ] && alert+=("ALERT_WEBHOOK_URL=$ALERT_WEBHOOK_PARAM")
write_env "$R/etc/infrx-alert.env" "${alert[@]}"
if [ -n "${MONITOR_DSN_PARAM:-}" ]; then write_env "$R/etc/infrx-observe.env" "MONITOR_DATABASE_URL=$MONITOR_DSN_PARAM"; fi
systemctl daemon-reload
systemctl enable --now infrx-observe.timer infrx-canary.timer
systemctl start infrx-canary.service || echo "canary: first run failed (see journalctl -u infrx-canary)"
systemctl start infrx-observe.service || echo "observe: first cycle exit non-zero (3 = delivery BLOCKED on P-25)"
systemctl list-timers 'infrx-*' --no-pager
