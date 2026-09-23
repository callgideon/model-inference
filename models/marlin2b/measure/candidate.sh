#!/usr/bin/env bash
# W4 item 3 - run ONE engine candidate on the pilot box, then put back the engine it found.
#
# Coordinator-run inside a logged maintenance window (it stops the installed engine), shipped
# base64-wrapped over SSM as W3's scripts are (research/plan/evidence/w/W3-d8a7878.md, "The
# measurement half"). The candidate is a NAME from the closed table below, never free-form
# flags. The protocol it executes is measure/W4-protocol.md §4:
#
#   1. refuse (exit 2, a `refused:` line, nothing stopped, nothing written but the lock)
#      without the restart consent W4_ENGINE_RESTART_OK=1, an unlisted CANDIDATE or any
#      argument, an OUT outside $NVME/w4-* or not canonical, a READY_S that is not a whole
#      number of seconds, another run holding $NVME/w4-candidate.lock, a unit that is not
#      active, no running engine container, a unit whose ExecStart cannot be read, a REPO
#      that is the unit's installed tree (the installed unit passes --max-num-seqs 32, which
#      a checked-out serve.sh refuses: W3 request 12(b)), no checkout, a corpus that does not
#      verify, a parity clip missing from the cache, or requests running or waiting;
#   2. record the engine it found (container args and image, unit state, the units that are
#      PartOf it), then `systemctl stop marlin2b-vllm`;
#   3. gate: one fresh candidate engine (the checkout's serve.sh with PORT, GPU, WEIGHTS,
#      the log level, no media root and ENGINE_MAX_NUM_SEQS=32 pinned here, the candidate's
#      flags appended), capability.sh (cancellation), parity.py;
#   4. for c = 1 2 4 8 16 32: a fresh engine, then concurrency.sh at that one level with
#      ENGINE_STATE=restarted, the engine container's memory sampled every 2 s alongside;
#   5. per engine start: start-to-ready seconds, the container's own args and the start-up
#      KV/encoder lines; per cell:
#      the engine's own error lines (`docker logs --since` the cell start) and whether it is
#      still running;
#   6. on EXIT, whatever happened: stop the candidate, start the unit (and its PartOf units
#      that were active), wait for /health, print `restored=yes|no` with an args diff.
#
#   CANDIDATE=e1 W4_ENGINE_RESTART_OK=1 bash candidate.sh
#   (REPO=/opt/dlami/nvme/w3-checkout CORPUS_CACHE=/opt/dlami/nvme/w3-corpus
#    PY=/opt/pytorch/bin/python3 READY_S=900 are the defaults; CLIP= a short mp4 for
#    capability.sh's media probes, optional)
# Writes only under $NVME/w4-*. ~7 engine starts plus the cells: run it detached.
set -euo pipefail
NVME=/opt/dlami/nvme
UNIT=marlin2b-vllm
CONTAINER=marlin2b-8000
ENGINE=http://127.0.0.1:8000
LEVELS="1 2 4 8 16 32"
ERRORS='error|exception|traceback|out of memory|exceeds the pre-allocated encoder cache'
REPO=${REPO:-$NVME/w3-checkout}
CORPUS_CACHE=${CORPUS_CACHE:-$NVME/w3-corpus}
PY=${PY:-/opt/pytorch/bin/python3}
READY_S=${READY_S:-900}
OUT=${OUT:-$NVME/w4-${CANDIDATE:-none}-$(date -u +%Y%m%dT%H%M%SZ)}

refuse() { echo "refused: $*" >&2; exit 2; }

[ $# -eq 0 ] || refuse "free-form flags are not accepted ($*): name a CANDIDATE"
case "${CANDIDATE:-}" in
  e0) flags=() ;;
  e1) flags=(--max-num-batched-tokens 32768) ;;
  e3) flags=(--max-num-batched-tokens 32768 --api-server-count 2) ;;
  *) refuse "CANDIDATE must be one of e0 e1 e3 (got '${CANDIDATE:-}')" ;;
esac
[ "${W4_ENGINE_RESTART_OK:-}" = 1 ] || refuse "W4_ENGINE_RESTART_OK=1 (a logged maintenance window) is required: this restarts the engine"
case "$OUT" in "$NVME"/w4-*) ;; *) refuse "OUT must be under $NVME/w4-* (got $OUT)" ;; esac
[ "$(realpath -m -s -- "$OUT")" = "$OUT" ] || refuse "OUT must be absolute and canonical"
[[ $READY_S =~ ^[1-9][0-9]*$ ]] || refuse "READY_S must be a whole number of seconds (got '$READY_S')"
# One run at a time: a second one would take the first one's candidate for the engine to
# restore, and its restore would start the unit under the first run.
exec 9>"$NVME/w4-candidate.lock" || refuse "cannot open $NVME/w4-candidate.lock"
flock -n 9 || refuse "another candidate.sh holds $NVME/w4-candidate.lock"
[ "$(systemctl is-active "$UNIT" 2>/dev/null || true)" = active ] \
  || refuse "$UNIT is not active: no installed engine to restore (another candidate run?)"
pre_args=$(docker inspect --format '{{json .Args}}' "$CONTAINER" 2>/dev/null) \
  || refuse "no container $CONTAINER: there is no engine to measure against or restore"
pre_image=$(docker inspect --format '{{.Image}}' "$CONTAINER")
unit_exec=$(systemctl show -p ExecStart --value "$UNIT" 2>/dev/null) || refuse "no unit $UNIT to restore"
unit_script=$(printf '%s\n' "$unit_exec" | sed -n 's/.*path=\([^ ;]*\).*/\1/p' | head -1)
[ -n "$unit_script" ] || refuse "cannot read $UNIT's ExecStart"
serve=$REPO/models/marlin2b/serve.sh
[ "$(realpath -m -- "$serve")" != "$(realpath -m -- "$unit_script")" ] \
  || refuse "REPO is the unit's installed tree ($unit_script); use the measurement checkout"
for f in serve.sh bench.py corpus/manifest.json corpus/build.py corpus-synth/manifest.json \
         corpus-synth/synth.py measure/capability.sh measure/concurrency.sh measure/parity.py; do
  test -f "$REPO/models/marlin2b/$f" || refuse "no checkout: $REPO has no models/marlin2b/$f"
done
export CORPUS_CACHE PYTHONDONTWRITEBYTECODE=1  # no __pycache__ in the checkout
verified=$("$PY" "$REPO/models/marlin2b/corpus/build.py" verify 2>&1) || {
  printf '%s\n' "$verified" | tail -5 >&2
  refuse "the corpus does not verify (corpus/build.py verify)"; }
# The parity set must be answerable (its 120 s clips are sop-synth-v1's). Their pinned bytes
# are recorded, not required: a clip that derives differently on this CPU still pairs by
# its own sha256, candidate against baseline.
parity_ready=$("$PY" "$REPO/models/marlin2b/measure/parity.py" --check --cache "$CORPUS_CACHE" 2>&1) || {
  printf '%s\n' "$parity_ready" | tail -5 >&2
  refuse "the parity set is not in the cache (parity.py --check)"; }
synth=$("$PY" "$REPO/models/marlin2b/corpus-synth/synth.py" verify 2>&1 | grep -m1 '^verified') || true
# last before anything is written or stopped
inflight=$(curl -sS -m 5 "$ENGINE/metrics" 2>/dev/null \
  | awk '/^vllm:num_requests_(running|waiting)[{ ]/{n+=$NF; seen=1} END{if (seen) print n+0}') || true
[ -n "$inflight" ] || refuse "the engine's metrics are unreadable: requests in flight unknown"
[ "$inflight" = 0 ] || refuse "the engine has $inflight request(s) running or waiting"

mkdir -p "$OUT"
exec > >(tee -a "$OUT/candidate.log") 2>&1
echo "candidate=$CANDIDATE flags=${flags[*]:-none} out=$OUT utc=$(date -u +%Y-%m-%dT%H:%M:%SZ) repo_sha=$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo unknown)"
echo "corpus=$(printf '%s\n' "$verified" | tail -1)"
echo "parity_set=$(printf '%s\n' "$parity_ready" | tail -1) synth=${synth:-unverified}"
echo "pre_image=$pre_image"
echo "pre_args=$pre_args"
active=()
for unit in $(systemctl show -p ConsistsOf --value "$UNIT" 2>/dev/null || true); do
  [ "$(systemctl is-active "$unit" 2>/dev/null || true)" = active ] && active+=("$unit")
done
echo "unit=$UNIT state=$(systemctl is-active "$UNIT" 2>/dev/null || true) exec_path=$unit_script partof_active=${active[*]:-none}"

wait_ready() {                    # sets ready_s; fails after READY_S
  local t0=$SECONDS
  until curl -sf -m 5 "$ENGINE/health" >/dev/null 2>&1; do
    [ $((SECONDS - t0)) -lt "$READY_S" ] || return 1
    sleep 2
  done
  ready_s=$((SECONDS - t0))
}

stop_candidate() {                # whatever holds the name, gone before the next start
  docker stop -t 30 "$CONTAINER" >/dev/null 2>&1 || true
  for _ in $(seq 60); do docker inspect "$CONTAINER" >/dev/null 2>&1 || return 0; sleep 1; done
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
}

start_candidate() {               # $1 = label: a fresh candidate engine, ready and recorded
  stop_candidate
  # every serve.sh input pinned here, none from the operator's environment; media goes
  # inline (video_b64), so no media root is mounted
  PORT=8000 GPU=0 WEIGHTS=$NVME/marlin2b VLLM_LOGGING_LEVEL=INFO PROCESSING_CACHE_DIR= \
    ENGINE_MAX_NUM_SEQS=32 nohup bash "$serve" "${flags[@]}" > "$OUT/engine-$1.log" 2>&1 9>&- &
  if ! wait_ready; then
    echo "start=$1 ready=no after ${READY_S}s"
    tail -20 "$OUT/engine-$1.log"
    return 1
  fi
  echo "start=$1 start_to_ready_s=$ready_s"
  { echo "args=$(docker inspect --format '{{json .Args}}' "$CONTAINER" 2>/dev/null)"
    grep -E -i 'KV cache size|Maximum concurrency|Encoder cache|max_num_batched_tokens' \
      "$OUT/engine-$1.log" || true
    curl -sS -m 5 "$ENGINE/metrics" 2>/dev/null | grep '^vllm:cache_config_info' || true
  } > "$OUT/startup-$1.log"
  cat "$OUT/startup-$1.log"
}

capture() {                       # $1 = cell, $2 = its start (UTC), $3 = its engine's label
  { docker logs --since "$2" "$CONTAINER" 2>&1 || cat "$OUT/engine-$3.log"; } \
    | grep -E -i "$ERRORS" > "$OUT/engine-errors-$1.log" || true
  local running
  running=$(docker inspect --format '{{.State.Running}}' "$CONTAINER" 2>/dev/null || true)
  echo "cell=$1 engine_running=$([ "$running" = true ] && echo yes || echo no) error_lines=$(wc -l < "$OUT/engine-errors-$1.log")"
}

host_sampler() {                  # the engine container's memory, every 2 s until killed
  set +e
  while :; do
    printf '%s\t%s\n' "$(date -u +%s)" \
      "$(docker stats --no-stream --format '{{.MemUsage}}' "$CONTAINER" 2>/dev/null)"
    sleep 2
  done
}

restore() {
  local status=$?
  trap - EXIT
  set +e                          # load-bearing: a failed step must not skip the report
  echo "restore: stopping the candidate, starting $UNIT ${active[*]:-}"
  stop_candidate
  systemctl start "$UNIT" "${active[@]}"
  local healthy=no post_args post_image
  wait_ready && healthy=yes
  post_args=$(docker inspect --format '{{json .Args}}' "$CONTAINER" 2>/dev/null)
  post_image=$(docker inspect --format '{{.Image}}' "$CONTAINER" 2>/dev/null)
  if [ "$healthy" = yes ] && [ "$post_args" = "$pre_args" ] && [ "$post_image" = "$pre_image" ]; then
    echo "restored=yes ready_s=$ready_s"
  else
    echo "restored=no healthy=$healthy"
    [ "$post_args" = "$pre_args" ] || echo "args_diff: before=$pre_args after=$post_args"
    [ "$post_image" = "$pre_image" ] || echo "image_diff: before=$pre_image after=$post_image"
    [ "$status" -ne 0 ] || status=4
  fi
  exit "$status"
}

trap restore EXIT
trap 'exit 130' INT TERM HUP
echo "stopping $UNIT"
systemctl stop "$UNIT"
stop_candidate

start_candidate gate
since=$(date -u +%Y-%m-%dT%H:%M:%SZ)
CONTAINER=$CONTAINER ENGINE=$ENGINE CLIP=${CLIP:-} \
  bash "$REPO/models/marlin2b/measure/capability.sh" > "$OUT/capability.txt" 2>&1 \
  || echo "capability_exit=$?"
grep '^probe=cancellation' "$OUT/capability.txt" || true
capture capability "$since" gate
since=$(date -u +%Y-%m-%dT%H:%M:%SZ)
"$PY" "$REPO/models/marlin2b/measure/parity.py" --engine "$ENGINE" --cache "$CORPUS_CACHE" \
  --out "$OUT/parity.jsonl" || echo "parity_exit=$?"
capture parity "$since" gate

for c in $LEVELS; do
  start_candidate "c$c"
  since=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  host_sampler > "$OUT/host-mem-c$c.tsv" 9>&- &
  sampler=$!
  rc=0
  LEVELS=$c ENGINE_STATE=restarted REPO=$REPO CORPUS_CACHE=$CORPUS_CACHE PY=$PY OUT=$OUT/sweep \
    bash "$REPO/models/marlin2b/measure/concurrency.sh" > "$OUT/c$c.concurrency.log" 2>&1 || rc=$?
  kill "$sampler" 2>/dev/null || true
  wait "$sampler" 2>/dev/null || true
  capture "c$c" "$since" "c$c"
  grep -E '^level=|^refused|bench_exit' "$OUT/c$c.concurrency.log" || true
  [ "$rc" -eq 0 ] || { echo "level=$c sweep_exit=$rc"; exit 3; }
done
echo "candidate=$CANDIDATE complete artifacts=$OUT"
