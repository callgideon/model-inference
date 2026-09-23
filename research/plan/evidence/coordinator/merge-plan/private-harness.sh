#!/usr/bin/env bash
# NOT a merge step: the merge-analysis scratch's private D task (never committed). The D harness
# on container infrx-merge-analysis-postgres at 127.0.0.1:55691 (its own lock under $TMPDIR),
# the decoy on 55693; D's Valkey via INFRX_D2_VALKEY_CONTAINER/PORT (55692); Q on 55695.
set -euo pipefail
python3 - <<'PY'
import pathlib
p = pathlib.Path("apps/infrx-api/tests/d/pgharness.py"); s = p.read_text()
old = 'SERVICE = local_services(os.environ.get("INFRX_D_TASK", "d1"))["postgres"]\n'
assert s.count(old) == 1
p.write_text(s.replace(old, 'from infrx.contracts.tasklocal import LocalService  # merge-analysis private task\n'
    'SERVICE = LocalService(service="postgres", container="infrx-merge-analysis-postgres", '
    'host_port=55691, database="infrx_merge_analysis", object_prefix="test/merge-analysis/")\n'))
p = pathlib.Path("apps/infrx-api/tests/d/test_pgharness.py"); s = p.read_text()
old = 'DECOY, DECOY_PORT = decoy(os.environ.get("INFRX_D_TASK", "d1").lower())\n'
assert s.count(old) == 1
p.write_text(s.replace(old, 'DECOY, DECOY_PORT = "infrx-merge-analysis-dharness-postgres", 55693  # private\n'))
PY
