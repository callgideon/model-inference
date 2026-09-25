#!/usr/bin/env bash
# The synthetic inference check (I8 slice 3): one tiny text request and one in-cap tiny
# video request through the PUBLIC edge, as a dedicated test tenant - never customer
# traffic, never an idempotent replay (no Idempotency-Key: each run is fresh work). Bounded:
# max_tokens 8/16, curl --max-time 60/180, the clip refused over CANARY_MAX_BYTES, one
# request of each kind per run (the timer runs it every 10 minutes: 288 requests a day of
# a few hundred tokens, charged to the canary tenant's wallet - P-24 records the approval).
#
#   INFRX_CANARY_KEY   the canary tenant's scoped key (EnvironmentFile /etc/infrx-canary.env,
#                      root 0600); reaches curl in a 0600 header file, never argv
#   CANARY_VIDEO       an in-cap clip on this host (<= MAX_VIDEO_SECONDS 82 s; e.g. the
#                      8-second sop-synth clip), sent inline as a data: URL
#
# Writes $OUT: infrx_canary_up{kind}, infrx_canary_seconds{kind}, and the run's timestamp.
# Prints the status and seconds per kind; never a key, a body or a response.
set -uo pipefail
OUT=${OUT:-/var/lib/infrx/metrics/canary.prom}
BASE=${BASE:-https://marlin2b.callbill.ai}
MODEL=${MODEL:-nemostation/marlin-2b}
CANARY_MAX_BYTES=${CANARY_MAX_BYTES:-4194304}
work=$(mktemp -d)
prom=$OUT.tmp.$$                      # beside $OUT, so the publishing rename is atomic
trap 'rm -rf "$work" "$prom"' EXIT
m() { printf '%s{process="canary"%s} %s\n' "$1" "${3:+,$3}" "$2" >> "$prom"; }
publish() { chmod 0644 "$prom"; mv -f "$prom" "$OUT"; }

if [ -z "${INFRX_CANARY_KEY:-}" ]; then
  m infrx_canary_configured 0
  m infrx_canary_last_run_timestamp_seconds "$(date +%s)"
  publish
  echo "BLOCKED: no INFRX_CANARY_KEY (/etc/infrx-canary.env)" >&2
  exit 3
fi
m infrx_canary_configured 1
( umask 077; printf 'Authorization: Bearer %s\nContent-Type: application/json\n' \
    "$INFRX_CANARY_KEY" > "$work/headers" )

request() {  # request KIND MAX_TIME BODY_FILE
  local kind=$1 limit=$2 body=$3 code seconds
  read -r code seconds < <(curl -s -o "$work/answer" -w '%{http_code} %{time_total}\n' \
      --max-time "$limit" -H "@$work/headers" --data-binary "@$body" \
      "$BASE/v1/chat/completions" || echo "000 $limit")
  local up=0
  if [ "$code" = 200 ] && grep -q '"choices"' "$work/answer"; then up=1; fi
  m infrx_canary_up "$up" "kind=\"$kind\""
  m infrx_canary_seconds "$seconds" "kind=\"$kind\""
  echo "canary $kind http=$code seconds=$seconds up=$up"
  [ "$up" = 1 ]
}

failed=0
printf '{"model":"%s","messages":[{"role":"user","content":"Reply with the word OK."}],"max_tokens":8}' \
  "$MODEL" > "$work/text.json"
request text 60 "$work/text.json" || failed=1

video=${CANARY_VIDEO:-}
size=$( [ -f "$video" ] && stat -c %s "$video" || echo 0 )
if [ -z "$video" ] || [ ! -f "$video" ] || [ "$size" -gt "$CANARY_MAX_BYTES" ] || [ "$size" -eq 0 ]; then
  m infrx_canary_up 0 'kind="video"'
  echo "canary video: CANARY_VIDEO missing, empty or over $CANARY_MAX_BYTES bytes" >&2
  failed=1
else
  { printf '{"model":"%s","max_tokens":16,"messages":[{"role":"user","content":[' "$MODEL"
    printf '{"type":"video_url","video_url":{"url":"data:video/mp4;base64,'
    base64 -w0 "$video"
    printf '"}},{"type":"text","text":"Describe the video in five words."}]}]}'
  } > "$work/video.json"
  request video 180 "$work/video.json" || failed=1
fi
m infrx_canary_last_run_timestamp_seconds "$(date +%s)"
publish
exit "$failed"
