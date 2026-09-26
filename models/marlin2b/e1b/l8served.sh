# E1B WC-6b (E1B-protocol §7.2; D-13 option A): caption-event parity, served half, after WC-7.
# A copy of research/plan/evidence/w/box/box-lane/l8served.sh (append-only, unchanged) with
# CLIPS = WC-6a's 11 clips (sop00-sop08 plus the two 10 s samples), refused (exit 2, no request)
# unless all are on the box. Same EUTC as WC-6a, so both halves write one L8-$EUTC directory;
# then: python3 research/plan/evidence/w/box/box-lane/l8compare.py <L8 dir> <repo>/models/marlin2b/measure
set -uo pipefail
: "${EUTC:?}"
NVME=/opt/dlami/nvme; REPO=$NVME/w3-checkout; PY=/opt/pytorch/bin/python3; OUT=$NVME/e1b-logs/L8-$EUTC
CLIPS="$NVME/w3-corpus/sop-synth-v1/sop00-8s-640x360.mp4
$NVME/w3-corpus/sop-synth-v1/sop01-8s-854x480-steps_within_one_second.mp4
$NVME/w3-corpus/sop-synth-v1/sop02-8s-1280x720.mp4
$NVME/w3-corpus/sop-synth-v1/sop03-30s-640x360.mp4
$NVME/w3-corpus/sop-synth-v1/sop04-30s-854x480-steps_out_of_order.mp4
$NVME/w3-corpus/sop-synth-v1/sop05-30s-1280x720.mp4
$NVME/w3-corpus/sop-synth-v1/sop06-60s-640x360.mp4
$NVME/w3-corpus/sop-synth-v1/sop07-60s-854x480-step_absent.mp4
$NVME/w3-corpus/sop-synth-v1/sop08-60s-1280x720.mp4
$NVME/samples/sample-10s.mp4
$NVME/samples/Big_Buck_Bunny_360_10s_1MB.mp4"
missing=$(while IFS= read -r clip; do [ -s "$clip" ] || echo "$clip"; done <<< "$CLIPS")
[ -z "$missing" ] || { echo "refused: WC-6 clips not on the box (sop00-sop08 and the two 10 s samples):" $missing; exit 2; }
mkdir -p $OUT
exec > >(tee -a $OUT/served.log) 2>&1
echo "utc=$(date -u +%Y-%m-%dT%H:%M:%SZ) engine_started=$(docker inspect --format '{{.State.StartedAt}}' marlin2b-8000) image=$(docker inspect --format '{{.Image}}' marlin2b-8000)"
echo "engine_args=$(docker inspect --format '{{json .Args}}' marlin2b-8000)"
while IFS= read -r clip; do
  id=$(basename "$clip" .mp4)
  for budget in default v1; do
    kw=''; [ $budget = v1 ] && kw=auto
    HF_HUB_OFFLINE=1 PYTHONDONTWRITEBYTECODE=1 timeout 900 $PY $REPO/models/marlin2b/smoke.py "$clip" \
      --base-url http://127.0.0.1:8000/v1 --weights $NVME/marlin2b --max-tokens 2048 --mm-kwargs "$kw" \
      > $OUT/served-$id-$budget.txt 2> $OUT/served-$id-$budget.err
    echo "clip=$id budget=$budget exit=$? $(tail -1 $OUT/served-$id-$budget.err | head -c 400)"
  done
done <<< "$CLIPS"
echo "served done utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
