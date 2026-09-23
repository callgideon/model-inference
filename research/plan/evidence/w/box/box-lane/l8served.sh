# Step 6c: E1B cell L8, served half: smoke.py (the canonical caption prompt read from the
# checkpoint's modeling_marlin.py - the same text .caption() sends) against the engine on the
# same five clips, greedy, max 2048 tokens, at two budgets: the processor default
# (--mm-kwargs '', what the vendor helper uses on transformers 5.17, results/notes.md
# finding 2) and profile v1 (--mm-kwargs auto). c = 1, one request at a time.
set -uo pipefail
: "${EUTC:?}"
NVME=/opt/dlami/nvme; REPO=$NVME/w3-checkout; PY=/opt/pytorch/bin/python3; OUT=$NVME/e1b-logs/L8-$EUTC
CLIPS="$NVME/w3-corpus/sop-synth-v1/sop09-120s-640x360.mp4
$NVME/w3-corpus/sop-synth-v1/sop10-120s-854x480-step_spans_segment_boundary.mp4
$NVME/w3-corpus/sop-synth-v1/sop11-120s-1280x720.mp4
$NVME/samples/sample-10s.mp4
$NVME/samples/Big_Buck_Bunny_360_10s_1MB.mp4"
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
