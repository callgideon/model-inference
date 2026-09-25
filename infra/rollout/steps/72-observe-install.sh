#!/usr/bin/env bash
# I8 slice 3 on the box: install the monitoring units (infra/observe/systemd/: not in the
# runtime's deploy dir, whose only timer-free reaper path W3 pins) from the checked-out release, write
# their three env files from SSM (values read on the box into 0600 files, never printed, never
# an argument), start the timers and run one cycle. Idempotent. Needs RELEASE (the checkout
# must be it), CANARY_VIDEO (an in-cap clip on the box, e.g. the 8 s sop-synth clip) and
#   CANARY_KEY_PARAM   SSM name of the canary tenant's scoped key - no default: the canary
#                      spends (288 requests a day) in whichever tenant that key is, and the
#                      certify/E1B tenant is the one E4C reconciles
#   P24_APPROVED       the P-24 approval reference for that recurring spend; unset = the
#                      canary timer is NOT enabled (BLOCKED (P-24)), everything else installs
#   ALERT_WEBHOOK_PARAM  SSM name of a P-25 webhook destination (a credential), or
#   ALERT_SNS_TOPIC_ARN  a P-25 SNS topic ARN (not a secret; the instance role needs
#                        sns:Publish on it). Exactly one; neither = delivery stays BLOCKED
#   ALERT_OWNER, ALERT_ESCALATION   P-25's owner and escalation (names/handles, not secrets)
#   MONITOR_DSN_PARAM  SSM name of D10's read-only monitor DSN; unset = the runtime DSN on 6543
# Rollback: systemctl disable --now infrx-observe.timer infrx-canary.timer; rm the four unit
# files and /etc/infrx-{canary,alert,observe}.env.
set -euo pipefail
: "${RELEASE:?the release commit}" "${CANARY_VIDEO:?an in-cap clip on the box}"
: "${CANARY_KEY_PARAM:?the SSM name of the canary tenant key - no default, P-24 bounds its spend}"
repo=${REPO:-/home/ubuntu/model-inference}
R=${INFRX_ROOT:-}                     # empty on the box; a sandbox root in tests (lib.sh's seam)
[ "$(git -c safe.directory="$repo" -C "$repo" rev-parse HEAD)" = "$RELEASE" ] \
  || { echo "the checkout is not $RELEASE" >&2; exit 2; }
[ -f "$CANARY_VIDEO" ] || { echo "no clip at CANARY_VIDEO" >&2; exit 2; }
if [ -n "${ALERT_SNS_TOPIC_ARN:-}" ]; then
  [ -z "${ALERT_WEBHOOK_PARAM:-}" ] \
    || { echo "ALERT_WEBHOOK_PARAM and ALERT_SNS_TOPIC_ARN are both set: exactly one destination" >&2; exit 2; }
  [[ $ALERT_SNS_TOPIC_ARN =~ ^arn:aws[a-z-]*:sns:[a-z0-9-]+:[0-9]{12}:[A-Za-z0-9_-]{1,256}$ ]] \
    || { echo "ALERT_SNS_TOPIC_ARN is not an SNS topic ARN" >&2; exit 2; }
fi
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
# The monitor's own copy of its scripts and rules, replaced by one rename: the units run
# from it, so a rollback of the runtime checkout to a release without infra/observe (or with
# older rules) leaves the monitoring as installed. Re-run this step to upgrade it.
pinned=$R/opt/infrx/observe
install -d -m 0755 "$(dirname "$pinned")"
next=$(mktemp -d "$pinned.next.XXXXXX")
mkdir -p "$next/infra"
cp -r "$repo/infra/observe" "$repo/infra/alerts" "$next/infra/"
echo "$RELEASE" > "$next/RELEASE"
chmod -R go-w "$next"; chmod 0755 "$next"
rm -rf "$pinned.previous"; [ -d "$pinned" ] && mv "$pinned" "$pinned.previous"
mv "$next" "$pinned"
echo "monitor pinned at $pinned from $RELEASE"
for unit in infrx-observe.service infrx-observe.timer infrx-canary.service infrx-canary.timer; do
  install -m 0644 "$repo/infra/observe/systemd/$unit" "$R/etc/systemd/system/$unit"
done
write_env "$R/etc/infrx-canary.env" "INFRX_CANARY_KEY=$CANARY_KEY_PARAM" "CANARY_VIDEO:=$CANARY_VIDEO"
alert=("ALERT_OWNER:=${ALERT_OWNER:-UNSET (P-25)}" "ALERT_ESCALATION:=${ALERT_ESCALATION:-UNSET (P-25)}")
[ -n "${ALERT_WEBHOOK_PARAM:-}" ] && alert+=("ALERT_WEBHOOK_URL=$ALERT_WEBHOOK_PARAM")
[ -n "${ALERT_SNS_TOPIC_ARN:-}" ] && alert+=("ALERT_SNS_TOPIC_ARN:=$ALERT_SNS_TOPIC_ARN")
write_env "$R/etc/infrx-alert.env" "${alert[@]}"
if [ -n "${MONITOR_DSN_PARAM:-}" ]; then write_env "$R/etc/infrx-observe.env" "MONITOR_DATABASE_URL=$MONITOR_DSN_PARAM"; fi
systemctl daemon-reload
systemctl enable --now infrx-observe.timer
if [ -n "${P24_APPROVED:-}" ]; then
  echo "canary: recurring spend approved by $P24_APPROVED"
  systemctl enable --now infrx-canary.timer
  systemctl start infrx-canary.service || echo "canary: first run failed (see journalctl -u infrx-canary)"
else
  echo "canary: BLOCKED (P-24) - its timer is not enabled; re-run with P24_APPROVED=<ref>" >&2
fi
systemctl start infrx-observe.service || echo "observe: first cycle exit non-zero (3 = delivery BLOCKED on P-25)"
systemctl list-timers 'infrx-*' --no-pager
