# shellcheck shell=bash
# The box-side helpers the rollout steps share (sourced from the checked-out release on the box,
# `. "$repo/infra/rollout/box-lib.sh"`, so only a step that runs on a checkout carrying it may
# source it; never run). Replaces the generic half of infra/lab/rollout/lib.sh and
# 72-observe-install.sh's param/write_env (which wrote an empty secret when an SSM read failed).
# Names only: a secret is read on the box from SSM by its parameter NAME into a 0600 file, never
# an argument, never printed. Needs $repo; STEP names the step in say's lines; BOX_LOG (default
# none) also receives them.
R=${INFRX_ROOT:-}                             # empty on the box; a sandbox root in tests

say() {  # one line to stdout and BOX_LOG (never a value: callers print names only)
  local line; line="$(date -u +%FT%TZ) ${STEP:-box} $*"
  mkdir -p "$(dirname "${BOX_LOG:-/dev/null}")" && echo "$line" >> "${BOX_LOG:-/dev/null}"; echo "$line"
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

ready() {  # ready PORT: /readyz 200 within READY_S tries
  local _
  for _ in $(seq "${READY_S:-60}"); do
    curl -fsS -o /dev/null --max-time 5 "http://127.0.0.1:$1/readyz" && return 0
    sleep "${READY_SLEEP_S:-1}"
  done
  return 1
}

caddy_reload() {  # the App edge re-reads /etc/caddy/Caddyfile through its admin socket
  docker exec caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile --address unix//config/admin.sock
}
