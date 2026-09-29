#!/usr/bin/env bash
# LAB-DEPLOY-PREP runbook step L7 (box): one Lab worker role's switch - its env file IS the
# switch (the unit's ConditionPathExists).
#   infra/rollout/ssm.sh infra/lab/rollout/steps/50-lab-role.sh STATE=on ROLE=eval RELEASE=<40 hex> \
#     SPEC="LAB_DATABASE_URL=/model-inference/lab/eval_database_url LAB_S3_BUCKET:=<bucket> ..."
#   infra/rollout/ssm.sh infra/lab/rollout/steps/50-lab-role.sh STATE=off ROLE=eval
# SPEC: `NAME=/ssm/name` (read by NAME on the box, the value only in the file) or
# `NAME:=literal` (a non-secret). Refused before any change (exit 2): an unknown role; a name
# the role does not read; a secret given as a literal; INFRX_IMAGE or the health port (the
# step's and the unit's); no LAB_DATABASE_URL; JUDGE_MODE other than dry_run (live judging is
# a separately authorized paid budget). Then (exit 3, nothing replaced): the annotation /
# training / rollout preflight and the Lab pooler budget on the staged file. Then the file by
# rename (ubuntu, 0600), enable + restart, and the unit's 127.0.0.1:<port>/readyz.
# Exit 5 = the role refused by name (exit 2, R198/R211: its work source is not on this release;
# it is not restarted - STATE=off clears it); 4 = started but not ready. Idempotent.
set -euo pipefail
STEP=50-lab-role
repo=${REPO:-/home/ubuntu/model-inference}
. "$repo/infra/lab/rollout/lib.sh"
role=${ROLE:-}
[[ " ${ROLES[*]} " == *" $role "* ]] || die 2 "ROLE must be one of: ${ROLES[*]}"
unit=infrx-lab-$role.service
env_file=$LAB_ETC/$role.env
case "${STATE:-}" in
  off) systemctl disable --now "$unit" 2>/dev/null || true
       rm -f "${LAB_ETC:?}/${role:?}.env"; say "$role OFF (unit disabled, env file removed)"; exit 0 ;;
  on) ;;
  *) die 2 "STATE must be on or off" ;;
esac
at_release
common="LAB_DATABASE_URL LAB_S3_BUCKET LAB_S3_ENDPOINT LAB_EGRESS_ALLOW"
case "$role" in    # what the role's entry point reads (infrx.lab.workers NEEDS, the unit)
  eval) names="$common LAB_S3_PREFIX LAB_EVAL_ENDPOINT_URL LAB_EVAL_ENDPOINT_KEY LAB_EVAL_CONCURRENCY" ;;
  checkpoints) names="$common LAB_S3_PREFIX LAB_CHECKPOINTS_CONCURRENCY" ;;
  judge) names="LAB_DATABASE_URL JUDGE_PROVIDER_URL CLICKHOUSE_URL S3_TRACE_BUCKET JUDGE_MODE" ;;
  datasets) names="$common LAB_S3_PREFIX CLICKHOUSE_URL S3_TRACE_BUCKET LAB_DATASETS_CONCURRENCY" ;;
  annotation) names="$common LAB_ANNOTATION_CONCURRENCY LAB_ANNOTATION_TEACHER LAB_ANNOTATION_TEACHER_URL LAB_ANNOTATION_TEACHER_TOKEN LAB_ANNOTATION_BUDGET_USD LAB_ANNOTATION_PAYER_REF" ;;
  training) names="$common LAB_TRAINING_CONCURRENCY LAB_TRAINING_CONNECTOR LAB_TRAINING_CONNECTOR_URL LAB_TRAINING_CONNECTOR_TOKEN LAB_TRAINING_BUDGET_USD LAB_TRAINING_PAYER_REF" ;;
  rollout) names="$common LAB_ROLLOUT_CONCURRENCY" ;;
esac
secrets=" LAB_DATABASE_URL LAB_EVAL_ENDPOINT_KEY LAB_ANNOTATION_TEACHER_TOKEN LAB_TRAINING_CONNECTOR_TOKEN CLICKHOUSE_URL "
read -r -a specs <<< "${SPEC:-}"
seen=" "
for spec in "${specs[@]}"; do
  case "$spec" in *:=*) name=${spec%%:=*}; literal=1 ;; *=*) name=${spec%%=*}; literal= ;;
                  *) die 2 "SPEC entry is not NAME=/ssm/name or NAME:=literal: ${spec%%=*}" ;; esac
  [[ " $names " == *" $name "* ]] || die 2 "$name is not a setting the $role role reads"
  [ -z "$literal" ] || [[ $secrets != *" $name "* ]] || die 2 "$name is a secret: give its SSM parameter NAME ($name=/...), never a literal"
  [ "$name" != JUDGE_MODE ] || [ "${spec#*:=}" = dry_run ] || die 2 "JUDGE_MODE: only dry_run here; live judging is a separately authorized paid budget"
  seen+="$name "
done
[[ $seen == *" LAB_DATABASE_URL "* ]] || die 2 "SPEC must name LAB_DATABASE_URL (the role's own Lab login, by SSM name)"
[ -f "$(unit_file "$role")" ] || die 2 "$unit is not installed: run 30-lab-units.sh"
image=$(lab_image)
install -d -m 0755 "$LAB_ETC"
staged=$(stage_env "$env_file" "INFRX_IMAGE:=$image" "${specs[@]}")
budget=$(mktemp -d)
trap 'rm -f "${staged:?}"; rm -rf "${budget:?}"' EXIT
case "$role" in annotation|training|rollout)
  python3 "$repo/infra/lab/workers/training/preflight.py" --role "$role" --env-file "$staged" \
    || die 3 "the $role preflight refused the staged file; nothing replaced" ;;
esac
for other in "$LAB_ETC"/*.env; do [ -f "$other" ] && cp -p "$other" "$budget/"; done
cp -p "$staged" "$budget/$role.env"
python3 "$repo/infra/lab/workers/eval/pool_budget.py" --consumer-env-file "$R/etc/marlin2b-gateway.env" \
  --lab-env-dir "$budget" || die 3 "the Lab pooler budget refused $role; nothing replaced"
place "$staged" "$env_file" ubuntu:ubuntu
systemctl enable "$unit"
systemctl restart "$unit"
port=$(health_port "$role")
if ! ready "$port"; then
  status=$(systemctl show -p ExecMainStatus --value "$unit" 2>/dev/null || true)
  [ "$status" != 2 ] || die 5 "$role refused by name (exit 2, R198/R211: its work source is not on this release; journalctl -u $unit); STATE=off clears it"
  die 4 "$role not ready on 127.0.0.1:$port (journalctl -u $unit); STATE=off turns it back off"
fi
say "$role ON: 127.0.0.1:$port/readyz 200"
