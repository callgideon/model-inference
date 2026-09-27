# E1B WC-6a (E1B-protocol §7.2; D-13 option A): caption-event parity, reference half, on the
# in-cap clips. A copy of research/plan/evidence/w/box/box-lane/l8ref.sh (append-only, unchanged)
# with three differences: CLIPS = sop00-sop08 (every sop-synth-v1 clip within the 82 s cap) plus
# the two 10 s samples, refused (exit 2, nothing touched) unless all 11 are on the box; the
# engine stop is DECLARED - infrx-observe.timer (the alert cycle) is stopped for the span, with
# infrx-observe.service, so a cycle already running is ended before the engine stops (systemctl
# stop returns once the oneshot is down; SWEEP-2 SW1-RV-3); the timer is started again after
# the restore, and the restore prints one drills.md-format line (E4C-runbook §6; its ssm=FILL
# takes ssm.sh's command id); WC-7 starts right after restored=yes. Run as root:
#   infra/rollout/ssm.sh models/marlin2b/e1b/l8ref.sh EUTC=<UTC stamp>   (WC-6b's l8served.sh: the same EUTC)
set -uo pipefail
: "${EUTC:?}"
NVME=/opt/dlami/nvme; UNIT=marlin2b-vllm; C=marlin2b-8000; ENGINE=http://127.0.0.1:8000
REPO=$NVME/w3-checkout; PY=/opt/pytorch/bin/python3; OUT=$NVME/e1b-logs/L8-$EUTC
OBSERVE=infrx-observe.timer
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
refuse() { echo "refused: $*"; exit 2; }
missing=$(while IFS= read -r clip; do [ -s "$clip" ] || echo "$clip"; done <<< "$CLIPS")
[ -z "$missing" ] || refuse "WC-6 clips not on the box (sop00-sop08 and the two 10 s samples):" $missing
exec 9>$NVME/w4-candidate.lock || refuse "cannot open the lock"
flock -n 9 || refuse "a candidate.sh run holds $NVME/w4-candidate.lock"
[ "$(systemctl is-active $UNIT)" = active ] || refuse "$UNIT is not active"
pre_args=$(docker inspect --format '{{json .Args}}' $C 2>/dev/null) || refuse "no container $C"
pre_image=$(docker inspect --format '{{.Image}}' $C)
inflight=$(curl -sS -m 5 $ENGINE/metrics | awk '/^vllm:num_requests_(running|waiting)[{ ]/{n+=$NF; s=1} END{if (s) print n+0}')
[ "$inflight" = 0 ] || refuse "requests in flight: '${inflight}'"
observe=$(systemctl is-active $OBSERVE)
mkdir -p $OUT
exec > >(tee -a $OUT/reference.log) 2>&1
echo "utc=$(date -u +%Y-%m-%dT%H:%M:%SZ) repo_sha=$(git -c safe.directory='*' -C $REPO rev-parse --short HEAD)"
echo "pre_image=$pre_image"; echo "pre_args=$pre_args"
$PY -c 'import transformers, torch; print("transformers", transformers.__version__, "torch", torch.__version__)'
wait_ready() { local t0=$SECONDS; until curl -sf -m 5 $ENGINE/health >/dev/null 2>&1; do [ $((SECONDS-t0)) -lt 900 ] || return 1; sleep 2; done; ready_s=$((SECONDS-t0)); }
restore() {
  local status=$?; trap - EXIT; set +e
  local start; start=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  echo "restore: starting $UNIT at $start"
  systemctl start $UNIT
  local healthy=no verdict=FAIL; ready_s=${ready_s:-0}; wait_ready && healthy=yes
  local ready; ready=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  post_args=$(docker inspect --format '{{json .Args}}' $C 2>/dev/null); post_image=$(docker inspect --format '{{.Image}}' $C 2>/dev/null)
  if [ "$observe" = active ]; then systemctl start $OBSERVE; fi
  echo "silence=$OBSERVE from=$stopped to=$(date -u +%Y-%m-%dT%H:%M:%SZ) (declared engine stop; was $observe)"
  if [ $healthy = yes ] && [ "$post_args" = "$pre_args" ] && [ "$post_image" = "$pre_image" ]; then
    echo "restored=yes ready_s=$ready_s"; [ "$ready_s" -le 300 ] && verdict=PASS
    correctness=PASS
  else
    echo "restored=no healthy=$healthy"; echo "post_args=$post_args"; echo "post_image=$post_image"; [ $status -ne 0 ] || status=4
    correctness="FAIL: restored=no healthy=$healthy"
  fi
  echo "drill=wc6a-declared-engine-stop runbook=models/marlin2b/results/E1B-protocol.md#72-the-cells ssm=FILL t0=$start recovered=$ready measured_s=$ready_s bound_s=300 verdict=$verdict correctness=$correctness"
  exit $status
}
stopped=$(date -u +%Y-%m-%dT%H:%M:%SZ)
trap restore EXIT; trap 'exit 130' INT TERM HUP
echo "declared stop: $OBSERVE ($observe) and $UNIT at $stopped"
[ "$observe" = active ] && systemctl stop $OBSERVE infrx-observe.service
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
