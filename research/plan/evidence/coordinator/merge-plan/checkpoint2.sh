#!/usr/bin/env bash
# Checkpoint 2 gate (README §5) on the merged claude/backend-impl tree. Run from the repo root
# AFTER step 9 on a clean tree, DETACHED (setsid nohup bash …/checkpoint2.sh > … &): each stage
# logs under .claude-logs/checkpoint2-<head>/. `make bench-test` is deliberately NOT here: its
# second-Ctrl-C case fails under nohup (SIGINT ignored) - run it in the foreground.
# Private ports: D task d3 (55434), lock Valkey 55464, Q 55490, MinIO `infrx-ckpt2-minio` on
# 55694 with the E2 literals (so the S3 lists and api-test's 14 S3 cases run instead of skipping;
# the cases create their bucket). Layer 3 on namespace e3b2 (`--only-suites --canary`, the
# phase-3 lane's form): expect exit 0, or exit 3 with PENDING only [I2B-R4] until the worker
# lane merges. Coordinator 2026-09-24.
set -uo pipefail
HEAD=$(git rev-parse --short HEAD); LOG=.claude-logs/checkpoint2-$HEAD; mkdir -p "$LOG/tmp"
API=apps/infrx-api; PY=$API/.venv/bin/python
export TMPDIR=$PWD/$LOG/tmp INFRX_D_TASK=d3 INFRX_D2_VALKEY_PORT=55464 \
       INFRX_D2_VALKEY_CONTAINER=infrx-d3-valkey INFRX_Q_VALKEY_PORT=55490
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_PROFILE AWS_ENDPOINT_URL AWS_ENDPOINT_URL_S3
stage() { echo "=== $1 $(date -u +%H:%M:%SZ)"; }
docker rm -f infrx-ckpt2-minio >/dev/null 2>&1 || true
docker run -d --name infrx-ckpt2-minio -p 127.0.0.1:55694:9000 \
  -e MINIO_ROOT_USER=infrxe2minio -e MINIO_ROOT_PASSWORD=infrx-e2-local-secret \
  quay.io/minio/minio@sha256:14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e \
  server /data --address :9000 >/dev/null && sleep 3
export INFRX_M_S3_ENDPOINT=http://127.0.0.1:55694 INFRX_M_S3_LOCAL_CREDS=1
LISTS="tests/contracts/test_mutants.py tests/m/test_mutants.py tests/m/test_pilot_mutants.py tests/m/test_s3_mutants.py \
tests/d/test_migration_mutants.py tests/d/test_code_mutants.py tests/d/test_code_mutants_d3.py tests/d/test_code_mutants_d4.py \
tests/d/test_code_mutants_d5.py tests/g/test_mutants.py tests/g/ops/test_mutants.py tests/g/uploads/test_uploads_mutants.py \
tests/g/jobs/test_jobs_mutants.py tests/i/test_mutants.py"
stage sync;      (cd $API && uv sync --frozen --all-extras > "../../$LOG/sync.log" 2>&1); echo "SYNC_EXIT=$?"
stage api-test;  (cd $API && uv run --frozen pytest -q -p no:cacheprovider -rfEs > "../../$LOG/api-test.log" 2>&1); echo "API_TEST_EXIT=$?"; tail -1 "$LOG/api-test.log"
stage layer0;    $PY -m pytest -q -p no:cacheprovider -rfE tests/integration > "$LOG/layer0.log" 2>&1; echo "L0_EXIT=$?"; tail -1 "$LOG/layer0.log"
stage mutant-lists; (cd $API && INFRX_MUTANTS=all uv run --frozen pytest -q -p no:cacheprovider -rfE $LISTS > "../../$LOG/mutants.log" 2>&1); echo "MUTANTS_EXIT=$?"; tail -1 "$LOG/mutants.log"
stage e4b-mutants; INFRX_MUTANTS=all $PY -m pytest -q -p no:cacheprovider -rfE tests/integration/backend/test_e4b_mutants.py > "$LOG/e4b-mutants.log" 2>&1; echo "E4BM_EXIT=$?"; tail -1 "$LOG/e4b-mutants.log"
stage w3-sigint; (cd $API && INFRX_MUTANTS=all uv run --frozen pytest -q -p no:cacheprovider -rfE tests/w/test_w3_mutants.py -k sigint_not_handled > "../../$LOG/w3-sigint.log" 2>&1); echo "W3_SIGINT_EXIT=$?"; tail -1 "$LOG/w3-sigint.log"
if [ -z "${SKIP_L3:-}" ]; then
  stage layer3-e3b2; INFRX_E2_NAMESPACE=e3b2 $PY tests/integration/run.py --layer 3 --only-suites --canary > "$LOG/layer3.log" 2>&1; echo "L3_EXIT=$?"; tail -3 "$LOG/layer3.log"
else echo "=== layer3 SKIPPED (SKIP_L3 set: e3b2 held by another lane)"; fi
docker rm -f infrx-ckpt2-minio >/dev/null 2>&1 || true
echo "EXIT=done $(date -u +%H:%M:%SZ)"
