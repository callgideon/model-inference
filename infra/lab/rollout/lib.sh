# shellcheck shell=bash
# LAB-DEPLOY-PREP: what every Lab box step shares (sourced from the checked-out release on the
# box, `infra/lab/rollout/steps/*.sh` sent by `infra/rollout/ssm.sh`). Names only: a secret is
# read on the box from SSM by its parameter NAME into a 0600 file, never an argument, never
# printed. Runbook: research/plan/consumer-v1/08-lab-internal-testing-rollout.md.
R=${INFRX_ROOT:-}                             # empty on the box; a sandbox root in tests
LAB_ETC=$R/etc/infrx-lab
CONTROL_ENV=$R/etc/infrx-lab-control.env
MARKER=$LAB_ETC/enabled                       # infrx-lab-control.service's ConditionPathExists
IMAGE_FILE=$LAB_ETC/image                     # the Lab release image id (20-lab-image.sh)
UNIT_DIR=$R/etc/systemd/system
LAB_UNITS=$repo/apps/infrx-api/deploy/lab
LAB_LOG=${LAB_LOG:-$R/var/log/infrx-lab-rollout.log}
ROLES=(eval checkpoints judge annotation training rollout datasets)
CONTROL_PORT=8003

say() {  # one line to stdout and the rollout log (never a value: callers print names only)
  local line; line="$(date -u +%FT%TZ) ${STEP:-lab} $*"
  mkdir -p "$(dirname "$LAB_LOG")" && echo "$line" >> "$LAB_LOG"; echo "$line"
}
die() { say "STOP($1): $2" >&2; exit "$1"; }

at_release() {  # the checkout the units and scripts come from must be RELEASE
  [[ ${RELEASE:-} =~ ^[0-9a-f]{40}$ ]] || die 2 "RELEASE must be the full release commit"
  [ "$(git -c safe.directory="$repo" -C "$repo" rev-parse HEAD)" = "$RELEASE" ] \
    || die 2 "the checkout $repo is not $RELEASE"
}

param() {  # an SSM SecureString by NAME; its value only on stdout (into a file, never echoed)
  aws ssm get-parameter --region us-east-1 --with-decryption --name "$1" \
    --query Parameter.Value --output text
}

stage_env() {  # stage_env FILE SPEC... -> a 0600 staged copy's path (NAME=/ssm/name | NAME:=literal)
  local file=$1 tmp spec value; shift
  tmp=$(umask 077; mktemp "$file.staged.XXXXXX")
  for spec in "$@"; do
    case "$spec" in
      *:=*) printf '%s=%s\n' "${spec%%:=*}" "${spec#*:=}" >> "$tmp" ;;
      *=/*) value=$(param "${spec#*=}") && [ -n "$value" ] \
              || { rm -f "${tmp:?}"; die 2 "cannot read SSM parameter ${spec#*=} (for ${spec%%=*})"; }
            printf '%s=%s\n' "${spec%%=*}" "$value" >> "$tmp" ;;
      *) rm -f "${tmp:?}"; die 2 "not NAME=/ssm/name or NAME:=literal: ${spec%%=*}" ;;
    esac
  done
  chmod 0600 "$tmp"; echo "$tmp"
}

place() {  # place STAGED FILE OWNER - by one rename; prints the names it holds
  chown "$3" "$1" 2>/dev/null || true         # the sandbox (tests) is not root
  mv -f "$1" "$2"
  say "wrote $2: $(cut -d= -f1 "$2" | tr '\n' ' ')"
}

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

ready() {  # ready PORT: /readyz 200 within READY_S tries
  local _
  for _ in $(seq "${READY_S:-60}"); do
    curl -fsS -o /dev/null --max-time 5 "http://127.0.0.1:$1/readyz" && return 0
    sleep "${READY_SLEEP_S:-1}"
  done
  return 1
}
