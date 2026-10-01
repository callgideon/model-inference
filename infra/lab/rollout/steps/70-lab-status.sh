#!/usr/bin/env bash
# LAB-DEPLOY-PREP step L5d (box, read-only): why a Lab unit is not ready — its unit state, the
# container, and the last journal lines (lines that could carry a value are dropped by NAME).
#   infra/rollout/ssm.sh infra/lab/rollout/steps/70-lab-status.sh RELEASE=<40 hex> [ROLE=control|<role>]
# ROLE (default control) names the unit and its loopback port as lib.sh derives them; UNIT and
# PORT still override either; an unknown ROLE is exit 2.
set -euo pipefail
STEP=70-lab-status
repo=${REPO:-/home/ubuntu/model-inference}
. "$repo/infra/lab/rollout/lib.sh"
at_release
role=${ROLE:-control}
case " control ${ROLES[*]} " in *" $role "*) ;; *) die 2 "unknown ROLE $role (control ${ROLES[*]})" ;; esac
unit=${UNIT:-$(basename "$(unit_file "$role")" .service)}
say "$unit: $(systemctl is-enabled "$unit" 2>/dev/null || true) / $(systemctl is-active "$unit" 2>/dev/null || true)"
systemctl show "$unit" -p ExecMainStatus -p NRestarts -p ActiveEnterTimestamp --no-pager || true
docker ps -a --filter "name=$unit" --format '{{.Names}} {{.Status}} {{.Image}}' || true
say "journal (last 60 lines; value-bearing lines dropped)"
journalctl -u "$unit" -n 60 --no-pager -o short-iso 2>/dev/null | grep -viE 'password|secret|anon_key|bearer|DATABASE_URL=|postgres(ql)?://' || true
port=${PORT:-$(health_port "$role" 2>/dev/null || true)}
say "readiness body on 127.0.0.1:$port (value-bearing lines dropped)"
curl -sS --max-time 5 "http://127.0.0.1:$port/readyz" 2>&1 | grep -viE 'password|secret|anon_key|bearer|postgres(ql)?://' | head -c 2000 || true; echo
