#!/usr/bin/env bash
# Run one rollout step on the box, as root, through SSM (coordinator-run; the I2B session
# never runs this). The step travels base64-wrapped, so no quoting survives the trip, and
# is preceded by `export NAME=VALUE` lines for the arguments given - non-secret values only
# (a release SHA, a backup path): secrets are read on the box from SSM by preflight.py.
#
#   infra/rollout/ssm.sh infra/rollout/steps/10-inventory.sh
#   infra/rollout/ssm.sh infra/rollout/steps/50-install.sh RELEASE=<sha>
#
# Prints the step's stdout and stderr (SSM keeps the first 24,000 characters of each) and
# exits 0 only if the invocation's status is Success.
set -euo pipefail
INSTANCE=${INSTANCE:-i-0e8449a4ffca29bab}
REGION=${REGION:-us-east-1}
usage() { echo "usage: ssm.sh <step.sh> [NAME=VALUE ...]"; }
case "${1:-}" in -h|--help) usage; exit 0 ;; esac
step=${1:-}
# an option or a missing path would reach `cat` and send its output to the box
[ -f "$step" ] || { usage >&2; exit 2; }
shift
header=""
for pair in "$@"; do
  case "$pair" in [A-Z_]*=*) header+="export $(printf '%q' "$pair")"$'\n' ;;
                  *) echo "not NAME=VALUE: $pair" >&2; exit 2 ;; esac
done
b64=$( { printf '%s' "$header"; cat -- "$step"; } | base64 -w0)
aws() { env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN \
        aws --region "$REGION" "$@"; }
params=$(printf '{"commands":["echo %s | base64 -d > /root/infrx-step.sh && bash /root/infrx-step.sh; rc=$?; rm -f /root/infrx-step.sh; exit $rc"],"executionTimeout":["%s"]}' \
         "$b64" "${TIMEOUT_S:-3600}")
id=$(aws ssm send-command --instance-ids "$INSTANCE" --document-name AWS-RunShellScript \
       --comment "infrx rollout $(basename "$step")" --timeout-seconds 600 \
       --parameters "$params" --query Command.CommandId --output text)
echo "command $id ($(basename "$step") on $INSTANCE)" >&2
while :; do
  status=$(aws ssm get-command-invocation --command-id "$id" --instance-id "$INSTANCE" \
             --query Status --output text 2>/dev/null || echo Pending)
  case "$status" in Pending|InProgress|Delayed) sleep "${POLL_S:-5}" ;; *) break ;; esac
done
aws ssm get-command-invocation --command-id "$id" --instance-id "$INSTANCE" \
  --query '[StandardOutputContent,StandardErrorContent]' --output text
echo "status $status" >&2
[ "$status" = Success ]
