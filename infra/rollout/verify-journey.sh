#!/usr/bin/env bash
# I8 slice 6, coordinator host: the rollback drill's proof that the runtime SERVES - after the
# rollback and again after the roll-forward - not that it answers /readyz (RV-10: a 5 s
# readiness said nothing about inference). Sibling of verify-external.sh; read-only for the
# service except the one fresh, in-cap job it submits (charged to the test tenant).
#
#   INFRX_TEST_KEY   a scoped consumer key (read -rs; reaches curl in a 0600 header file)
#   VIDEO_FILE       an in-cap clip (<= MAX_VIDEO_SECONDS 82 s; e.g. the 8 s sop-synth clip)
#   EXPECT=serving   (default) the checks below; EXPECT=maintenance: the window is closed -
#                    a submission answers 503 with Retry-After and nothing is admitted
#
# Checks: (1) the public edge opens within EDGE_LAG_MAX_S of the box's readiness - run this
# right after the install/resume step returns; the 2026-09-24 finding was a public 503 after
# local readiness; (2) POST /v1/jobs with the clip -> 202 and a handle; (3) the job reaches
# `succeeded` within JOB_MAX_S; (4) its result has content and token usage; (5) its usage is
# authoritative (what settlement debits). The settlement itself is a hosted read the
# coordinator runs next: the printed `drift.py --request-id` line (job settled, hold
# settled, no wallet drift). Prints timings and ids (opaque), never a key or a body.
set -uo pipefail
HOST=${HOST:-marlin2b.callbill.ai}
base=https://$HOST
MODEL=${MODEL:-nemostation/marlin-2b}
EXPECT=${EXPECT:-serving}
EDGE_LAG_MAX_S=${EDGE_LAG_MAX_S:-30}
JOB_MAX_S=${JOB_MAX_S:-240}
MAX_CLIP_BYTES=${MAX_CLIP_BYTES:-16777216}
POLL_S=${POLL_S:-2}
: "${INFRX_TEST_KEY:?a scoped consumer key (read -rs)}"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
fails=0
ok()  { echo "PASS $*"; }
bad() { echo "FAIL $*"; fails=$((fails + 1)); }
( umask 077; printf 'Authorization: Bearer %s\nContent-Type: application/json\n' "$INFRX_TEST_KEY" > "$work/h" )
field() { python3 -c 'import json,sys
d = json.load(open(sys.argv[1]))
for k in sys.argv[2].split("."):
    d = d[int(k)] if isinstance(d, list) else d.get(k) if isinstance(d, dict) else None
print("" if d is None else d)' "$1" "$2" 2>/dev/null; }

if [ "$EXPECT" = maintenance ]; then
  code=$(curl -s -D "$work/hd" -o "$work/b" -w '%{http_code}' --max-time 30 -H "@$work/h" \
         --data-binary '{"model":"x","messages":[]}' "$base/v1/jobs")
  if [ "$code" = 503 ] && grep -qi '^retry-after:' "$work/hd"; then
    ok "maintenance: a submission is refused (503 + Retry-After): nothing new is admitted"
  else bad "maintenance: a submission answered $code - the window is not closed"; fi
  echo "failures: $fails"; exit "$fails"
fi

# (1) the edge, from the moment the box said ready
start=$SECONDS
until [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$base/health")" = 200 ]; do
  if [ $((SECONDS - start)) -ge "$EDGE_LAG_MAX_S" ]; then break; fi
  sleep 1
done
lag=$((SECONDS - start))
if [ "$lag" -lt "$EDGE_LAG_MAX_S" ]; then
  ok "public edge open ${lag}s after local readiness"; echo "timing edge_open_s=$lag"
else
  bad "public edge still not 200 ${EDGE_LAG_MAX_S}s after local readiness (the 2026-09-24 503-after-readiness finding)"
fi

# (2) one fresh in-cap video job
size=$( [ -f "${VIDEO_FILE:-}" ] && stat -c %s "$VIDEO_FILE" || echo 0 )
if [ "$size" -eq 0 ] || [ "$size" -gt "$MAX_CLIP_BYTES" ]; then
  bad "VIDEO_FILE missing, empty or over $MAX_CLIP_BYTES bytes"; echo "failures: $fails"; exit "$fails"
fi
{ printf '{"model":"%s","max_tokens":64,"messages":[{"role":"user","content":[' "$MODEL"
  printf '{"type":"video_url","video_url":{"url":"data:video/mp4;base64,'
  base64 -w0 "$VIDEO_FILE"
  printf '"}},{"type":"text","text":"Describe what happens in the video."}]}]}'; } > "$work/body"
printf 'Idempotency-Key: verify-journey-%s\n' "$(python3 -c 'import uuid; print(uuid.uuid4())')" >> "$work/h"
t0=$SECONDS
code=$(curl -s -o "$work/accepted" -w '%{http_code}' --max-time 120 -H "@$work/h" \
       --data-binary "@$work/body" "$base/v1/jobs")
handle=$(field "$work/accepted" job_handle)
request=$(field "$work/accepted" request_id)
if [ "$code" = 202 ] && [ -n "$handle" ]; then ok "video job accepted (202) request_id=$request"
else bad "POST /v1/jobs answered $code"; echo "failures: $fails"; exit "$fails"; fi

# (3) terminal within the bound
state=""
while [ $((SECONDS - t0)) -lt "$JOB_MAX_S" ]; do
  curl -s -o "$work/status" --max-time 30 -H "@$work/h" "$base/v1/jobs/$handle"
  state=$(field "$work/status" state)
  case "$state" in succeeded|failed|cancelled|expired) break ;; esac
  sleep "$POLL_S"
done
echo "timing job_s=$((SECONDS - t0))"
[ "$state" = succeeded ] && ok "job succeeded" || bad "job state '$state' ($(field "$work/status" cause))"

# (4) the result, (5) authoritative usage
code=$(curl -s -o "$work/result" -w '%{http_code}' --max-time 30 -H "@$work/h" "$base/v1/jobs/$handle/result")
content=$(field "$work/result" response.choices.0.message.content)
prompt=$(field "$work/result" usage.prompt_tokens)
completion=$(field "$work/result" usage.completion_tokens)
if [ "$code" = 200 ] && [ -n "$content" ] && [ "${prompt:-0}" -gt 0 ] && [ "${completion:-0}" -gt 0 ]; then
  ok "result fetched: ${#content} characters, usage ${prompt}+${completion} tokens"
else bad "result: http $code, content ${#content} chars, usage ${prompt:-?}+${completion:-?}"; fi
[ "$(field "$work/status" usage_certainty)" = authoritative ] && ok "usage authoritative (settleable)" \
  || bad "usage certainty '$(field "$work/status" usage_certainty)'"
echo "settlement (coordinator, hosted read-only): apps/infrx-api/.venv/bin/python infra/runbooks/drift.py --request-id $request"
echo "failures: $fails"
exit "$fails"
