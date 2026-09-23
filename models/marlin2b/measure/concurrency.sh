#!/usr/bin/env bash
# W3 measurement 3 of 3 - the concurrency sweep that sets ENGINE_MAX_NUM_SEQS.
#
# E1B's predeclared cell L1 (models/marlin2b/results/E1B-protocol.md §3): bench.py against
# the engine directly, closed-loop concurrency 1,2,4,8,16,32 - one run each, distinct
# corpus clips, seed 20260922, output mix 128/512/1024 - with the engine's own queue and
# KV usage and the GPU's memory sampled every 2 s alongside. This is the one script that
# is NOT read-only: it loads the engine (it restarts nothing and changes no setting).
#
# Preconditions it checks and refuses on, rather than measuring the wrong thing:
#   * the engine runs with --max-num-seqs 32, so the sweep, not the engine, is the cap;
#   * REPO holds bench.py and CORPUS_CACHE holds the built corpus (corpus/build.py verify).
# It records, and does not refuse on, whether the image is the pin (inventory.sh answers
# that; a cell on another image is labelled, not published as the pinned version).
#
#   REPO=/home/ubuntu/model-inference CORPUS_CACHE=/opt/dlami/nvme/corpus-cache \
#   PY=/opt/pytorch/bin/python3 OUT=/opt/dlami/nvme/w3-measure bash concurrency.sh
# ~15-25 min: run it detached and return $OUT/<run>/ (bench.jsonl, raw/, samples.tsv, and
# the printed report).
set -euo pipefail
REPO=${REPO:-/home/ubuntu/model-inference}
PY=${PY:-/opt/pytorch/bin/python3}
ENGINE=${ENGINE:-http://127.0.0.1:8000}
CONTAINER=${CONTAINER:-marlin2b-8000}
LEVELS=${LEVELS:-1 2 4 8 16 32}
run_id=w3-L1-$(date -u +%Y%m%dT%H%M%SZ)
out=${OUT:-/opt/dlami/nvme/w3-measure}/$run_id
bench=$REPO/models/marlin2b/bench.py
manifest=$REPO/models/marlin2b/corpus/manifest.json

args=$(docker inspect --format '{{json .Args}}' "$CONTAINER")
case "$args" in
  *'"--max-num-seqs","32"'*) ;;
  *) echo "refused: the engine is not running with --max-num-seqs 32 ($args)" >&2; exit 2 ;;
esac
test -f "$bench" && test -f "$manifest" || { echo "refused: no bench.py/corpus under REPO=$REPO" >&2; exit 2; }
test -d "${CORPUS_CACHE:?export CORPUS_CACHE (the built corpus)}" || { echo "refused: no CORPUS_CACHE directory" >&2; exit 2; }
export CORPUS_CACHE
mkdir -p "$out"
echo "run=$run_id utc=$(date -u +%Y-%m-%dT%H:%M:%SZ) repo_sha=$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo unknown)"
echo "image=$(docker inspect --format '{{.Image}}' "$CONTAINER") args=$args"

sample() {   # level -> one tab-separated line per 2 s until killed
  set +e     # a scrape that times out is an empty sample, not the end of the sampling
  while :; do
    metrics=$(curl -sS -m 2 "$ENGINE/metrics" 2>/dev/null \
      | awk '/^vllm:num_requests_running/{r+=$NF} /^vllm:num_requests_waiting/{w+=$NF}
             /^vllm:(kv_cache_usage_perc|gpu_cache_usage_perc)/{k=$NF} END{printf "%s\t%s\t%s", r, w, k}')
    gpu=$(nvidia-smi --query-gpu=memory.used,utilization.gpu --format=csv,noheader,nounits | tr -d ' ' | tr ',' '\t')
    printf '%s\t%s\t%s\t%s\n' "$1" "$(date -u +%s)" "$metrics" "$gpu"
    sleep 2
  done
}

printf 'level\tepoch\trunning\twaiting\tkv_usage\tgpu_mem_mib\tgpu_util\n' > "$out/samples.tsv"
for c in $LEVELS; do
  n=$(( c * 4 > 64 ? c * 4 : 64 ))
  sample "$c" >> "$out/samples.tsv" &
  sampler=$!
  "$PY" "$bench" --target direct --base-url "$ENGINE/v1" --corpus "$manifest" --subset full \
    --seed 20260922 --max-tokens 128,512,1024 --engine-state warm -c "$c" -n "$n" \
    --dataset-version e1b-2026-09-22 --profile-version v1 --label "$run_id-c$c" \
    --out "$out/bench.jsonl" --raw "$out/raw/c$c.jsonl" > "$out/c$c.log" 2>&1 \
    || echo "level=$c bench_exit=$? (see $out/c$c.log)"
  kill "$sampler" 2>/dev/null || true; wait "$sampler" 2>/dev/null || true
  awk -F'\t' -v c="$c" '$1==c { if ($4>w) w=$4; if ($5>k) k=$5; if ($6>m) m=$6; if ($3>r) r=$3 }
    END { printf "level=%s peak_running=%s peak_waiting=%s peak_kv_usage=%s peak_gpu_mem_mib=%s\n", c, r, w, k, m }' \
    "$out/samples.tsv"
done

echo "### report"
"$PY" "$bench" --report "$out/bench.jsonl"
echo "### kv capacity at start-up (engine log)"
# no match (a rotated log) is a missing line, not a failed sweep
docker logs "$CONTAINER" 2>&1 | grep -E -i 'GPU KV cache size|Maximum concurrency' | tail -4 || true
echo "artifacts=$out"
