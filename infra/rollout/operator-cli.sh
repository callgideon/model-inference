#!/usr/bin/env bash
# Run one `infrx.operations.cli` verb with the operator environment read from SSM by NAME:
#   OPERATIONS_DATABASE_URL <- /model-inference/pg_journal_url (the owner login)
#   INFRX_OPERATOR_KEY      <- /model-inference/operator_key
# Nothing is printed and no secret travels on argv (the CLI refuses tokens that look like keys).
#   infra/rollout/operator-cli.sh <verb> [args…]        # from the repo root, after make api-env
#   e.g. infra/rollout/operator-cli.sh flag --name signup_grant --dry-run
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/host-lib.sh"
need_venv
[ $# -ge 1 ] || { echo "usage: infra/rollout/operator-cli.sh <verb> [args…]" >&2; exit 2; }
for a in "$@"; do case "$a" in *sk-*|*postgres://*|*postgresql://*) echo "refusing an argv token that looks like a secret" >&2; exit 2;; esac; done
OPERATIONS_DATABASE_URL=$(ssm_value /model-inference/pg_journal_url)
INFRX_OPERATOR_KEY=$(ssm_value /model-inference/operator_key)
[ -n "$OPERATIONS_DATABASE_URL" ] && [ -n "$INFRX_OPERATOR_KEY" ] || { echo "SSM read failed (names: pg_journal_url, operator_key)" >&2; exit 3; }
export OPERATIONS_DATABASE_URL INFRX_OPERATOR_KEY PYTHONPATH=apps/infrx-api
exec "$PY" -m infrx.operations.cli "$@"
