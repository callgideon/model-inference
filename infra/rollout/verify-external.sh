#!/usr/bin/env bash
# From outside the box (the coordinator's host), after 50-install: the public surface is
# exactly the edge; the engine, the gateway, the index and the admin API are unreachable;
# health is sanitized; operator paths are hidden; credentials and bounds are enforced on
# the deployed path. Read-only against the service. Key material comes from the
# environment (names only here), is never printed, and reaches curl through a 0600
# header file (`-H @file`), never its argv (`ps`, /proc/*/cmdline, `set -x`):
#   INFRX_TEST_KEY      a scoped key G6B issued for the check (pending G6B/A1)
#   INFRX_REVOKED_KEY   a key G6B issued and then revoked (pending G6B/A1)
#   LEGACY_KEY          the pre-cutover shared key (/model-inference/marlin2b_api_key), if any
set -uo pipefail
HOST=${HOST:-marlin2b.callbill.ai}
IP=${IP:-100.57.145.167}
base=https://$HOST
fails=0
hdr=$(mktemp)                      # 0600
out=$(mktemp)                      # response bodies: not a fixed, pre-plantable /tmp path
trap 'rm -f "$hdr" "$out"' EXIT
ok()  { echo "PASS $*"; }
bad() { echo "FAIL $*"; fails=$((fails + 1)); }
expect() {  # expect NAME WANT-STATUS CURL-ARGS...
  local name=$1 want=$2; shift 2
  local got; got=$(curl -s -o "$out" -w '%{http_code}' --max-time 30 "$@")
  if [ "$got" = "$want" ]; then ok "$name ($got)"; else bad "$name: got $got, want $want"; fi
}
# printf is a builtin: the key is written to the file without becoming anyone's argument.
auth() { printf 'Authorization: Bearer %s\n' "$1" > "$hdr"; echo "@$hdr"; }

expect "public health" 200 "$base/health"
[ "$(cat "$out")" = '{"ok":true}' ] && ok "health body is exactly {\"ok\":true}" || bad "health body leaks detail"
expect "/metrics hidden" 404 "$base/metrics"
expect "/readyz hidden" 404 "$base/readyz"
for port in 8000 8001 6379 2019; do
  if curl -s -o /dev/null --max-time 5 "http://$IP:$port/" ; then bad "port $port answers from outside"
  else ok "port $port unreachable from outside"; fi
done
body='{"model":"nemostation/marlin-2b","messages":[{"role":"user","content":"hi"}],"max_tokens":8}'
expect "chat without a key" 401 -H 'Content-Type: application/json' -d "$body" "$base/v1/chat/completions"
expect "chat with a made-up key" 401 -H 'Content-Type: application/json' -H "$(auth sk-infrx-not-a-real-key-0000000000)" -d "$body" "$base/v1/chat/completions"
if [ -n "${LEGACY_KEY:-}" ]; then
  expect "the shared legacy key no longer authenticates (R51)" 401 -H 'Content-Type: application/json' -H "$(auth "$LEGACY_KEY")" -d "$body" "$base/v1/chat/completions"
fi
if [ -n "${INFRX_TEST_KEY:-}" ]; then
  expect "chat with a scoped key" 200 -H 'Content-Type: application/json' -H "$(auth "$INFRX_TEST_KEY")" -d "$body" "$base/v1/chat/completions"
  grep -qi '^server-timing:' <(curl -s -D - -o /dev/null --max-time 60 -H 'Content-Type: application/json' -H "$(auth "$INFRX_TEST_KEY")" -d "$body" "$base/v1/chat/completions") \
    && ok "Server-Timing present (E1B phases)" || bad "no Server-Timing header (G2)"
  meta='{"model":"nemostation/marlin-2b","messages":[{"role":"user","content":[{"type":"video_url","video_url":{"url":"http://169.254.169.254/latest/meta-data/"}}]}]}'
  expect "a blocked media URL is refused" 400 -H 'Content-Type: application/json' -H "$(auth "$INFRX_TEST_KEY")" -d "$meta" "$base/v1/chat/completions"
else
  echo "PENDING scoped-key, Server-Timing and blocked-URL checks: INFRX_TEST_KEY not given (G6B/A1)"
fi
if [ -n "${INFRX_REVOKED_KEY:-}" ]; then
  expect "a revoked key" 401 -H 'Content-Type: application/json' -H "$(auth "$INFRX_REVOKED_KEY")" -d "$body" "$base/v1/chat/completions"
else
  echo "PENDING revoked-key check: INFRX_REVOKED_KEY not given (G6B/A1)"
fi
expect "a declared oversize body" 413 -H 'Content-Type: application/json' -H 'Content-Length: 100663297' --data-binary @/dev/null "$base/v1/chat/completions"
echo "failures: $fails"
exit "$fails"
