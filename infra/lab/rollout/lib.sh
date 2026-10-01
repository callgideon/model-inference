# shellcheck shell=bash
# LAB-DEPLOY-PREP: what every Lab box step shares (sourced from the checked-out release on the
# box, `infra/lab/rollout/steps/*.sh` sent by `infra/rollout/ssm.sh`). Names only: a secret is
# read on the box from SSM by its parameter NAME into a 0600 file, never an argument, never
# printed. Runbook: research/plan/consumer-v1/08-lab-internal-testing-rollout.md.
. "$repo/infra/rollout/box-lib.sh"            # R, say/die, at_release, param, stage_env, place, ready, caddy_reload
LAB_ETC=$R/etc/infrx-lab
CONTROL_ENV=$R/etc/infrx-lab-control.env
MARKER=$LAB_ETC/enabled                       # infrx-lab-control.service's ConditionPathExists
IMAGE_FILE=$LAB_ETC/image                     # the Lab release image id (20-lab-image.sh)
UNIT_DIR=$R/etc/systemd/system
LAB_UNITS=$repo/apps/infrx-api/deploy/lab
LAB_LOG=${LAB_LOG:-$R/var/log/infrx-lab-rollout.log}
BOX_LOG=$LAB_LOG STEP=${STEP:-lab}               # say's log and line prefix
ROLES=(eval checkpoints judge annotation training rollout datasets)
CONTROL_PORT=8003

lab_image() {
  local id; id=$(cat "$IMAGE_FILE" 2>/dev/null || true)
  [[ $id =~ ^sha256:[0-9a-f]{64}$ ]] || die 2 "no Lab image id in $IMAGE_FILE: run 20-lab-image.sh"
  echo "$id"
}

unit_file() {  # the installed unit of ROLE (or `control`)
  if [ "$1" = control ]; then echo "$UNIT_DIR/infrx-lab-control.service"
  else echo "$UNIT_DIR/infrx-lab-$1.service"; fi
}

health_port() {  # the loopback port the installed unit gives the role (LAB_WORKER_HEALTH_PORT)
  [ "$1" = control ] && { echo "$CONTROL_PORT"; return; }
  sed -n 's/.*-e LAB_WORKER_HEALTH_PORT=\([0-9]*\).*/\1/p' "$(unit_file "$1")"
}
