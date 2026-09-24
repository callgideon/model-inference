#!/usr/bin/env bash
# E2C: the backend-certify gate (see gates.py and ENVIRONMENT.md). Exit 0 PASS, 1 FAIL,
# 3 BLOCKED / NOT RUN, 4 INVALID; the JSON verdict path is the last line printed.
set -euo pipefail
src=${BASH_SOURCE[0]}; [[ $src == */* ]] || src=./$src   # builtins only: runs on a bare PATH
here=$(cd "${src%/*}" && pwd)
py="$here/../../apps/infrx-api/.venv/bin/python"
if [ ! -x "$py" ]; then
  echo '{"gate": "backend-certify", "verdict": "BLOCKED", "detail": "apps/infrx-api/.venv missing: run make api-env"}'
  exit 3
fi
exec "$py" "$here/gates.py" backend-certify "$@"
