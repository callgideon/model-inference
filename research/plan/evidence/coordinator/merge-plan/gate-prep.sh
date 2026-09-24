#!/usr/bin/env bash
# Focused gate after step 10 (PREP-WORKER) - the whole checkpoint 2 ran on 27af05a; this covers what the
# loop touches: tests/w + its lists, tests/i, contracts quick + F's list subset, tests/g quick, tests/m,
# layer 0, and one layer 3 on e3b2 (--only-suites --canary). Run DETACHED from a scratch clone at the
# merged head with TMPDIR outside the checkout: d3 ports, MinIO 55694, the fake_vllm ports of e3b2 free.
set -uo pipefail
HEAD=$(git rev-parse --short HEAD); LOG=.claude-logs/gate-prep-$HEAD; mkdir -p "$LOG"
TMP=${GATE_TMP:-/tmp/claude-1000/gate-prep-$HEAD}; mkdir -p "$TMP"
API=apps/infrx-api; PY=$API/.venv/bin/python
export TMPDIR=$TMP INFRX_D_TASK=d3 INFRX_D2_VALKEY_PORT=55464 INFRX_D2_VALKEY_CONTAINER=infrx-d3-valkey INFRX_Q_VALKEY_PORT=55490
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_PROFILE AWS_ENDPOINT_URL AWS_ENDPOINT_URL_S3
stage() { echo "=== $1 $(date -u +%H:%M:%SZ)"; }
docker rm -f infrx-gate-minio >/dev/null 2>&1 || true
docker run -d --name infrx-gate-minio -p 127.0.0.1:55694:9000 -e MINIO_ROOT_USER=infrxe2minio -e MINIO_ROOT_PASSWORD=infrx-e2-local-secret \
  quay.io/minio/minio@sha256:14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e server /data --address :9000 >/dev/null && sleep 3
export INFRX_M_S3_ENDPOINT=http://127.0.0.1:55694 INFRX_M_S3_LOCAL_CREDS=1
stage sync;      (cd $API && uv sync --frozen --all-extras > "../../$LOG/sync.log" 2>&1); echo "SYNC_EXIT=$?"
stage suites;    (cd $API && .venv/bin/python -m pytest -q -p no:cacheprovider -rfEs tests/w tests/i tests/g tests/m tests/contracts --ignore=tests/contracts/test_mutants.py --ignore=tests/w/test_w4_mutants.py --ignore=tests/w/test_w3_mutants.py --ignore=tests/w/test_worker_main_mutants.py --ignore=tests/w/test_prep_worker_mutants.py --ignore=tests/g/test_mutants.py --ignore=tests/g/ops/test_mutants.py --ignore=tests/g/uploads/test_uploads_mutants.py --ignore=tests/g/jobs/test_jobs_mutants.py --ignore=tests/i/test_mutants.py --ignore=tests/m/test_mutants.py --ignore=tests/m/test_pilot_mutants.py --ignore=tests/m/test_s3_mutants.py > "../../$LOG/suites.log" 2>&1); echo "SUITES_EXIT=$?"; tail -1 "$LOG/suites.log"
stage d-conformance; (cd $API && .venv/bin/python -m pytest -q -p no:cacheprovider -rfE tests/d/test_jobstore_conformance.py tests/d/test_credit_jobstore_conformance.py tests/d/test_pgharness.py > "../../$LOG/d.log" 2>&1); echo "D_EXIT=$?"; tail -1 "$LOG/d.log"
stage lists;     (cd $API && INFRX_MUTANTS=all .venv/bin/python -m pytest -q -p no:cacheprovider -rfE tests/w/test_prep_worker_mutants.py tests/w/test_worker_main_mutants.py tests/w/test_w3_mutants.py tests/contracts/test_mutants.py tests/i/test_mutants.py tests/m/test_pilot_mutants.py > "../../$LOG/lists.log" 2>&1); echo "LISTS_EXIT=$?"; tail -1 "$LOG/lists.log"
stage layer0;    $PY -m pytest -q -p no:cacheprovider -rfE tests/integration > "$LOG/layer0.log" 2>&1; echo "L0_EXIT=$?"; tail -1 "$LOG/layer0.log"
stage e4b-list;  INFRX_MUTANTS=all $PY -m pytest -q -p no:cacheprovider -rfE tests/integration/backend/test_e4b_mutants.py > "$LOG/e4b.log" 2>&1; echo "E4B_EXIT=$?"; tail -1 "$LOG/e4b.log"
stage layer3-e3b2; INFRX_E2_NAMESPACE=e3b2 $PY tests/integration/run.py --layer 3 --only-suites --canary > "$LOG/layer3.log" 2>&1; echo "L3_EXIT=$?"; tail -3 "$LOG/layer3.log"
docker rm -f infrx-gate-minio >/dev/null 2>&1 || true
echo "EXIT=done $(date -u +%H:%M:%SZ)"
