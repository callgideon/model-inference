#!/usr/bin/env bash
# I8 slice 4, the delivery proof: ONE clearly marked test alert ("[TEST FIRING] ... NO ACTION
# REQUIRED", a nonce) to the destination P-25 configured, then - with RESOLVE=<nonce> - its
# recovery. The owner confirms receipt of the nonce out of band; a 2xx alone is not delivery.
# BLOCKED (exit 3) until /etc/infrx-alert.env exists (P-25: ALERT_WEBHOOK_URL or
# ALERT_SNS_TOPIC_ARN, ALERT_OWNER, ALERT_ESCALATION; root 0600, written by
# 72-observe-install.sh). The URL is never printed; the SNS topic name is.
set -euo pipefail
repo=${REPO:-/home/ubuntu/model-inference}
for need in infra/observe/deliver.py; do  # absent at the pre-I8 known-good targets (bda1586, 4226315)
  [ -e "$repo/$need" ] || { echo "BLOCKED: this step needs an I8+ checkout (missing $need)" >&2; exit 3; }
done
conf=${ALERT_ENV:-/etc/infrx-alert.env}
[ -f "$conf" ] || { echo "BLOCKED: no $conf (P-25: destination, owner, escalation)" >&2; exit 3; }
while IFS='=' read -r name value; do
  case "$name" in ALERT_WEBHOOK_URL|ALERT_SNS_TOPIC_ARN|ALERT_OWNER|ALERT_ESCALATION) export "$name=$value" ;; esac
done < "$conf"
if [ -n "${RESOLVE:-}" ]; then
  [[ $RESOLVE =~ ^[0-9a-f]{12}$ ]] || { echo "RESOLVE is the 12-hex nonce the test printed" >&2; exit 2; }
  exec python3 "$repo/infra/observe/deliver.py" --test-resolve "$RESOLVE"
fi
exec python3 "$repo/infra/observe/deliver.py" --test
