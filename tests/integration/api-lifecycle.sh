#!/usr/bin/env bash
# AP-11: the API-only lifecycle runner in isolated mode on task-local key ap11
# (tests/integration/api_lifecycle/runner.py). A fresh 0700 state dir, removed on exit.
# Exit 0 PASS, 1 FAIL, 3 BLOCKED / NOT RUN, 4 INVALID (the runner's code); verdict at <out>/verdict.json.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
state_dir=$(mktemp -d)
chmod 700 "$state_dir"
trap 'rm -rf "$state_dir"' EXIT
rc=0
env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN \
  apps/infrx-api/.venv/bin/python tests/integration/api_lifecycle/runner.py \
  --mode isolated --world ap11 --state "$state_dir/state.json" "$@" || rc=$?
exit "$rc"
