#!/usr/bin/env bash
# LAB-DEPLOY-PREP runbook step L8 (box, read-only): the smoke after every switch. Each switch
# that is ON answers its /readyz (the control service when the marker exists, a role when its
# env file exists), and the App's gateway and worker still answer theirs - the Lab never
# changes the App. Exit 1 = any of them does not answer (the table says which).
#   infra/rollout/ssm.sh infra/lab/rollout/steps/60-lab-smoke.sh
set -euo pipefail
STEP=60-lab-smoke
repo=${REPO:-/home/ubuntu/model-inference}
. "$repo/infra/lab/rollout/lib.sh"
READY_S=${READY_S:-10}
bad=0
check() {  # check NAME PORT
  if ready "$2"; then say "PASS $1 127.0.0.1:$2/readyz"; else say "FAIL $1 127.0.0.1:$2/readyz"; bad=1; fi
}
check "App gateway" 8001
check "App worker" 8002
if [ -e "$MARKER" ]; then check "Lab control" "$CONTROL_PORT"; fi
for role in "${ROLES[@]}"; do
  if [ -f "$LAB_ETC/$role.env" ]; then check "Lab $role" "$(health_port "$role")"; fi
done
exit "$bad"
