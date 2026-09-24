#!/usr/bin/env bash
# I8 slice 5, box: restore the served model from the durable mirror (80-mirror-artifacts.sh)
# onto replacement ephemeral storage and time each phase separately.
#   MODE=fetch (default, no service impact): fetch into a fresh directory, verify every file
#       against the mirror's manifest; prints fetch_s and verify_s; CLEANUP=1 removes it.
#   MODE=swap  (MAINTENANCE WINDOW - the single GPU serves nothing while the engine loads):
#       fetch + verify, then the edge to maintenance and the runtime drained (drain.sh pause),
#       the live weights directory moved aside (kept: MODE=undo puts it back), the restored
#       one moved in, the engine restarted - engine_load_s is restart -> /health (the cold
#       start, NOT a readiness-only time) - then one warm-up text and one warm-up video
#       request on loopback (warm_text_s, warm_video_s: the first video is the cold path),
#       then drain.sh resume (runtime_ready_s) and one public request with the canary key
#       (first_usable_s, from the engine restart). Any failure after the move: MODE=undo.
#   MODE=undo: the moved-aside directory back, the engine restarted, drain.sh resume.
# Needs RELEASE, MIRROR_URL; swap/undo also RESTORE_ID (printed by fetch/swap, the
# timestamp that names the directories) and a clip for the video warm-up (CANARY_VIDEO in
# /etc/infrx-canary.env). Timings are measurements for P-18, not targets.
set -euo pipefail
: "${RELEASE:?the release commit}" "${MIRROR_URL:?s3://bucket/prefix/ of the mirror}"
MODE=${MODE:-fetch}
repo=${REPO:-/home/ubuntu/model-inference}
nvme=${NVME:-/opt/dlami/nvme}
live=$nvme/marlin2b
deploy=/root/infrx-deploy-$RELEASE/apps/infrx-api/deploy
id=${RESTORE_ID:-$(date -u +%Y%m%dT%H%M%SZ)}
[[ $id =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || { echo "RESTORE_ID is the timestamp fetch printed" >&2; exit 2; }
staged=$nvme/restore-$id/marlin2b
aside=$nvme/marlin2b.pre-restore-$id
t() { date +%s.%N; }
since() { python3 -c "import sys; print(f'{float(sys.argv[2]) - float(sys.argv[1]):.1f}')" "$1" "$(t)"; }
wait_up() { local deadline=$(( SECONDS + $2 )); until curl -fsS -o /dev/null --max-time 5 "$1"; do
  [ "$SECONDS" -lt "$deadline" ] || return 1; sleep 2; done; }

fetch() {
  mkdir -p "$staged"
  local s; s=$(t)
  aws s3 cp --only-show-errors --region us-east-1 "${MIRROR_URL}manifest.json" "$nvme/restore-$id/manifest.json"
  aws s3 sync --only-show-errors --no-progress --region us-east-1 "${MIRROR_URL}weights/" "$staged/"
  echo "timing fetch_s=$(since "$s") restore_id=$id bytes=$(du -sb "$staged" | cut -f1)"
  s=$(t)
  python3 "$repo/infra/runbooks/artifacts.py" verify --weights "$staged" --manifest "$nvme/restore-$id/manifest.json"
  echo "timing verify_s=$(since "$s")"
}

undo() {
  [ -d "$aside" ] || { echo "nothing moved aside for $id" >&2; exit 2; }
  "$deploy/drain.sh" pause || true
  rm -rf "$live.failed-$id"; [ -d "$live" ] && mv "$live" "$live.failed-$id"
  mv "$aside" "$live"
  systemctl restart marlin2b-vllm
  wait_up http://127.0.0.1:8000/health "${ENGINE_READY_S:-900}" || { echo "the engine did not come back; the edge stays in maintenance" >&2; exit 4; }
  "$deploy/drain.sh" resume
  echo "undone: the previous weights serve again (restored copy kept at $live.failed-$id)"
}

warm() {  # warm KIND BODY-FILE
  local s; s=$(t)
  curl -fsS -o /dev/null --max-time 300 -H 'Content-Type: application/json' \
    --data-binary "@$2" http://127.0.0.1:8000/v1/chat/completions
  echo "timing warm_$1_s=$(since "$s")"
}

case "$MODE" in
  fetch) fetch; [ -n "${CLEANUP:-}" ] && rm -rf "$nvme/restore-$id"; exit 0 ;;
  undo) undo; exit 0 ;;
  swap) ;;
  *) echo "MODE is fetch, swap or undo" >&2; exit 2 ;;
esac
[ -x "$deploy/drain.sh" ] || { echo "no $deploy (30-pause.sh extracts it)" >&2; exit 2; }
fetch
video=$(sed -n 's/^CANARY_VIDEO=//p' /etc/infrx-canary.env 2>/dev/null || true)
[ -f "$video" ] || { echo "no CANARY_VIDEO clip for the video warm-up" >&2; exit 2; }
"$deploy/drain.sh" pause
mv "$live" "$aside"
mv "$staged" "$live"
restart=$(t)
systemctl restart marlin2b-vllm
wait_up http://127.0.0.1:8000/health "${ENGINE_READY_S:-900}" || { echo "the engine did not load the restored weights: MODE=undo RESTORE_ID=$id" >&2; exit 4; }
echo "timing engine_load_s=$(since "$restart")"
work=$(mktemp -d); trap 'rm -rf "$work"' EXIT
printf '{"model":"marlin2b","messages":[{"role":"user","content":"Reply with OK."}],"max_tokens":8}' > "$work/text.json"
{ printf '{"model":"marlin2b","max_tokens":16,"messages":[{"role":"user","content":[{"type":"video_url","video_url":{"url":"data:video/mp4;base64,'
  base64 -w0 "$video"; printf '"}},{"type":"text","text":"Describe the video in five words."}]}]}'; } > "$work/video.json"
warm text "$work/text.json"
warm video "$work/video.json"
s=$(t)
"$deploy/drain.sh" resume
echo "timing runtime_ready_s=$(since "$s")"
( while IFS='=' read -r name value; do
    case "$name" in INFRX_CANARY_KEY|CANARY_VIDEO) export "$name=$value" ;; esac
  done < /etc/infrx-canary.env
  OUT=$work/canary.prom bash "$repo/infra/observe/canary.sh" ) || { echo "the first public request failed: MODE=undo RESTORE_ID=$id" >&2; exit 4; }
echo "timing first_usable_s=$(since "$restart") (engine restart -> a public text and video request served)"
echo "restored $id; the previous weights are kept at $aside until you remove them"
