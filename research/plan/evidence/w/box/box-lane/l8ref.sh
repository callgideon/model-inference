# Step 6b: E1B cell L8, reference half. The engine unit is stopped (the GPU is the
# reference's), reference.py (transformers, the vendor .caption()) runs on the five clips,
# and the unit is started again on EXIT whatever happened - candidate.sh's restore pattern:
# restored=yes only when healthy with the same args and image. Holds candidate.sh's lock,
# so it can never overlap a candidate run.
set -uo pipefail
: "${EUTC:?}"
NVME=/opt/dlami/nvme; UNIT=marlin2b-vllm; C=marlin2b-8000; ENGINE=http://127.0.0.1:8000
REPO=$NVME/w3-checkout; PY=/opt/pytorch/bin/python3; OUT=$NVME/e1b-logs/L8-$EUTC
CLIPS="$NVME/w3-corpus/sop-synth-v1/sop09-120s-640x360.mp4
$NVME/w3-corpus/sop-synth-v1/sop10-120s-854x480-step_spans_segment_boundary.mp4
$NVME/w3-corpus/sop-synth-v1/sop11-120s-1280x720.mp4
$NVME/samples/sample-10s.mp4
$NVME/samples/Big_Buck_Bunny_360_10s_1MB.mp4"
refuse() { echo "refused: $*"; exit 2; }
exec 9>$NVME/w4-candidate.lock || refuse "cannot open the lock"
flock -n 9 || refuse "a candidate.sh run holds $NVME/w4-candidate.lock"
[ "$(systemctl is-active $UNIT)" = active ] || refuse "$UNIT is not active"
pre_args=$(docker inspect --format '{{json .Args}}' $C 2>/dev/null) || refuse "no container $C"
pre_image=$(docker inspect --format '{{.Image}}' $C)
inflight=$(curl -sS -m 5 $ENGINE/metrics | awk '/^vllm:num_requests_(running|waiting)[{ ]/{n+=$NF; s=1} END{if (s) print n+0}')
[ "$inflight" = 0 ] || refuse "requests in flight: '${inflight}'"
mkdir -p $OUT
exec > >(tee -a $OUT/reference.log) 2>&1
echo "utc=$(date -u +%Y-%m-%dT%H:%M:%SZ) repo_sha=$(git -c safe.directory='*' -C $REPO rev-parse --short HEAD)"
echo "pre_image=$pre_image"; echo "pre_args=$pre_args"
$PY -c 'import transformers, torch; print("transformers", transformers.__version__, "torch", torch.__version__)'
wait_ready() { local t0=$SECONDS; until curl -sf -m 5 $ENGINE/health >/dev/null 2>&1; do [ $((SECONDS-t0)) -lt 900 ] || return 1; sleep 2; done; ready_s=$((SECONDS-t0)); }
restore() {
  local status=$?; trap - EXIT; set +e
  echo "restore: starting $UNIT"
  systemctl start $UNIT
  local healthy=no; wait_ready && healthy=yes
  post_args=$(docker inspect --format '{{json .Args}}' $C 2>/dev/null); post_image=$(docker inspect --format '{{.Image}}' $C 2>/dev/null)
  if [ $healthy = yes ] && [ "$post_args" = "$pre_args" ] && [ "$post_image" = "$pre_image" ]; then echo "restored=yes ready_s=$ready_s"
  else echo "restored=no healthy=$healthy"; echo "post_args=$post_args"; echo "post_image=$post_image"; [ $status -ne 0 ] || status=4; fi
  exit $status
}
trap restore EXIT; trap 'exit 130' INT TERM HUP
echo "stopping $UNIT at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
systemctl stop $UNIT
for _ in $(seq 60); do docker inspect $C >/dev/null 2>&1 || break; sleep 1; done
echo "engine_container=$(docker inspect --format '{{.State.Status}}' $C 2>&1 | head -1)"
nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader
while IFS= read -r clip; do
  id=$(basename "$clip" .mp4)
  echo "clip=$id sha256=$(sha256sum < "$clip" | cut -d' ' -f1) start=$(date -u +%H:%M:%SZ)"
  HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONDONTWRITEBYTECODE=1 \
    $PY $REPO/models/marlin2b/reference.py "$clip" > $OUT/ref-$id.json 2> $OUT/ref-$id.err
  echo "clip=$id exit=$? end=$(date -u +%H:%M:%SZ) $(grep -E '^(loaded|wall)' $OUT/ref-$id.err | tr '\n' ' ')"
done <<< "$CLIPS"
echo "reference done utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
