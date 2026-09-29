#!/usr/bin/env bash
# LAB-LOCAL (E4-ON): every switch ON, the whole Lab composed locally (lab_local/runner.py).
# Exit 0 PASS, 1 FAIL, 3 BLOCKED / NOT RUN, 4 INVALID; the verdict path is the last line.
set -euo pipefail
src=${BASH_SOURCE[0]}; [[ $src == */* ]] || src=./$src   # builtins only: runs on a bare PATH
here=$(cd "${src%/*}" && pwd)
py="$here/../../apps/infrx-api/.venv/bin/python"
if [ ! -x "$py" ]; then
  echo '{"gate": "lab-local", "verdict": "BLOCKED", "detail": "apps/infrx-api/.venv missing: run make api-env"}'
  exit 3
fi
exec "$py" "$here/lab_local/runner.py" "$@"
