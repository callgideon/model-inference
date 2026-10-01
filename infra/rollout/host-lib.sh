# shellcheck shell=bash
# The coordinator-host helpers (sourced, never run; coordinator host only - box steps stay one
# file each, see box-lib.sh). Replaces the per-script copies of aws() (the stale AWS_* exports
# dropped: CLAUDE.md "AWS access from this host"), say() and the venv check, and the inline SSM
# reads. A value read from SSM goes into the environment or a 0600 file, never argv or output.
#   . "$(dirname "${BASH_SOURCE[0]}")/host-lib.sh"
aws() { env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN \
        aws --region "${REGION:-us-east-1}" "$@"; }
say() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "${HOST_LOG:-/dev/null}"; }
need_venv() {  # the host scripts run from the repo root after make api-env
  [ -x apps/infrx-api/.venv/bin/python ] || { echo "run from the repo root after make api-env" >&2; exit 2; }
  PY=apps/infrx-api/.venv/bin/python
}
ssm_value() {  # ssm_value NAME: the SecureString's value on stdout, for VAR=$(ssm_value NAME)
  aws ssm get-parameter --with-decryption --name "$1" --query Parameter.Value --output text
}
ssm_to_file() {  # ssm_to_file NAME FILE: the value into a 0600 FILE; empty or unreadable = failure, no FILE
  if (umask 077; ssm_value "$1" > "$2") && grep -q '[^[:space:]]' "$2"; then return 0; fi
  rm -f -- "$2"; echo "SSM parameter $1 is unreadable or empty" >&2; return 1
}
