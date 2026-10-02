/**
 * A3: the copyable API examples. Every snippet here is executed by `tests/a/examples.test.ts`
 * (bash + curl + jq, python3 stdlib, node fetch) against a gateway fake that refuses what the
 * published capability refuses, and the requests they make are held to the gateway's route
 * table and the record's parameter set. A snippet that cannot run as written fails CI.
 *
 * `{{BASE_URL}}` is the `Snippet` component's one placeholder (the API origin). The model id and
 * token cap come from the published record, never from a constant here.
 *
 * UX-04 (C-02/C-06, audit UX-A06/A09; `tests/ux/first-call/examples.test.ts`): no snippet carries a
 * key in any form - no prefix, ellipsis or placeholder in the credential position. Each reads
 * `INFRX_API_KEY` from the environment and stops before any request when it is unset. A video URL is
 * the reader's own input (`videoUrlInput`, local validation only), written once at the top of the
 * snippet as an escaped literal and referenced from there (curl builds its JSON with `jq --arg`);
 * without one it is `VIDEO_URL_PLACEHOLDER`, which is visibly not a URL and which the API refuses.
 * The async example waits each Retry-After hint, stops at a visible `MAX_POLLS` bound and reads a
 * result only after `succeeded`.
 *
 * Imported by `node --test`: relative `.ts` imports only (R48).
 */
import type { PublishedModel } from "../../../lib/contracts/v2/published-model.ts";

export type Language = "curl" | "python" | "javascript";

export type Example = {
  id: string;
  title: string;
  blurb: string;
  snippets: Record<Language, string>;
};

/** The dense-captioning prompt Marlin was tuned on (provider document), first line. */
export const CAPTION_PROMPT = "Provide a spatial description of this clip followed by time-ranged events.";
/** What a video example shows until the reader supplies a URL: visibly incomplete, refused by the API. */
export const VIDEO_URL_PLACEHOLDER = "<REPLACE with your https clip URL>";
/** The local file the upload and inline examples read. */
export const CLIP_FILE = "clip.mp4";
/** Output tokens the examples ask for; within any published cap we would serve (checked). */
export const EXAMPLE_MAX_TOKENS = 512;
/** The async example's client-side bound on status reads, shown as a variable the reader may raise. */
export const MAX_POLLS = 150;
const KEY_MISSING = "Set INFRX_API_KEY to your full API key first";
const VIDEO_NOTE = "your finished clip at an https URL the API can fetch";

export type VideoUrl = { ok: true; url: string } | { ok: false; message: string };

/**
 * The video URL field: a local check, never a fetch. An absolute https URL without credentials,
 * returned normalised (the form the examples write); the API applies its own limits on submission.
 */
export function videoUrlInput(raw: string): VideoUrl {
  let url: URL;
  try {
    url = new URL(raw.trim());
  } catch {
    return { ok: false, message: "Enter the full https:// address of your clip." };
  }
  if (url.protocol !== "https:") return { ok: false, message: "The API fetches https URLs only." };
  if (url.username || url.password) return { ok: false, message: "Remove the user name and password from the URL." };
  return { ok: true, url: url.href };
}

/** A POSIX shell single-quoted literal: nothing inside is expanded. */
const shellQuote = (text: string) => `'${text.replaceAll("'", `'\\''`)}'`;

const CURL_ENV = `: "\${INFRX_API_KEY:?${KEY_MISSING}}"
BASE="{{BASE_URL}}"`;

const PY_HEAD = `import json, os, urllib.error, urllib.request

BASE = "{{BASE_URL}}"
KEY = os.environ.get("INFRX_API_KEY")
if not KEY:
    raise SystemExit("${KEY_MISSING}")


def api(method, path, body=None, headers=None, raw=None):
    """One call. Returns (status, headers, parsed JSON or None); an HTTP error is a status too."""
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    hdrs = {"Authorization": f"Bearer {KEY}"}
    if body is not None:
        hdrs["Content-Type"] = "application/json"
    hdrs.update(headers or {})
    request = urllib.request.Request(BASE + path, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(request, timeout=600) as reply:
            text = reply.read()
            return reply.status, reply.headers, json.loads(text) if text else None
    except urllib.error.HTTPError as error:
        text = error.read()
        return error.code, error.headers, json.loads(text) if text else None
`;

const JS_HEAD = `// Node 18+ (global fetch). Save as example.mjs and run: node example.mjs
const BASE = "{{BASE_URL}}";
const KEY = process.env.INFRX_API_KEY;
if (!KEY) throw new Error("${KEY_MISSING}");

async function api(method, path, { body, headers = {}, raw } = {}) {
  const init = { method, headers: { Authorization: \`Bearer \${KEY}\`, ...headers } };
  if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  if (raw !== undefined) init.body = raw;
  const reply = await fetch(BASE + path, init);
  const text = await reply.text();
  return { status: reply.status, headers: reply.headers, json: text ? JSON.parse(text) : null };
}
`;

/** A text-only chat body as JSON (fixed text, so it can sit in a single-quoted shell argument). */
function textBody(model: PublishedModel, text: string) {
  return `{"model": "${model.id}", "max_tokens": ${EXAMPLE_MAX_TOKENS}, "messages": [{"role": "user", "content": ${JSON.stringify(text)}}]}`;
}

/** The video chat body as a jq program over `$url`: curl never splices the URL into JSON or shell. */
function jqVideoBody(model: PublishedModel, extra = "") {
  return `{"model": "${model.id}", "max_tokens": ${EXAMPLE_MAX_TOKENS}${extra}, "messages": [{"role": "user", "content": [{"type": "video_url", "video_url": {"url": $url}}, {"type": "text", "text": "${CAPTION_PROMPT}"}]}]}`;
}

export function buildExamples(model: PublishedModel, inputs: { videoUrl?: string } = {}): Example[] {
  const id = model.id;
  const max = EXAMPLE_MAX_TOKENS;
  const video = inputs.videoUrl ?? VIDEO_URL_PLACEHOLDER;
  // JSON string syntax is a valid Python and JavaScript string literal.
  const curlVideo = `VIDEO_URL=${shellQuote(video)}   # ${VIDEO_NOTE}`;
  const pyVideo = `VIDEO_URL = ${JSON.stringify(video)}  # ${VIDEO_NOTE}`;
  const jsVideo = `const VIDEO_URL = ${JSON.stringify(video)}; // ${VIDEO_NOTE}`;
  const curlBody = (extra = "") => `BODY=$(jq -nc --arg url "$VIDEO_URL" '${jqVideoBody(model, extra)}')`;
  const videoMessages = (url: string) =>
    `[{"role": "user", "content": [
        {"type": "video_url", "video_url": {"url": ${url}}},
        {"type": "text", "text": "${CAPTION_PROMPT}"},
    ]}]`;
  const jsVideoMessages = (url: string) =>
    `[{ role: "user", content: [
    { type: "video_url", video_url: { url: ${url} } },
    { type: "text", text: "${CAPTION_PROMPT}" },
  ] }]`;

  return [
    {
      id: "text",
      title: "Connectivity check (text)",
      blurb:
        "The smallest call: your key and one text message, answered as one JSON body. It checks that your key and the endpoint work; it is not a test of video understanding.",
      snippets: {
        curl: `${CURL_ENV}
curl -sS --fail-with-body "$BASE/v1/chat/completions" \\
  -H "Authorization: Bearer $INFRX_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '${textBody(model, "In one sentence, what is dense video captioning?")}' \\
  | jq -r '.choices[0].message.content'`,
        python: `${PY_HEAD}
status, headers, body = api("POST", "/v1/chat/completions", {
    "model": "${id}",
    "max_tokens": ${max},
    "messages": [{"role": "user", "content": "In one sentence, what is dense video captioning?"}],
})
print(status, headers.get("Inference-Id"))
print(body["choices"][0]["message"]["content"])`,
        javascript: `${JS_HEAD}
const { status, headers, json } = await api("POST", "/v1/chat/completions", {
  body: {
    model: "${id}",
    max_tokens: ${max},
    messages: [{ role: "user", content: "In one sentence, what is dense video captioning?" }],
  },
});
console.log(status, headers.get("Inference-Id"));
console.log(json.choices[0].message.content);`,
      },
    },
    {
      id: "video",
      title: "One finite video by URL",
      blurb:
        "A video part plus the prompt. The gateway fetches the URL, samples it and answers when the whole clip has been processed.",
      snippets: {
        curl: `${CURL_ENV}
${curlVideo}
${curlBody()}
curl -sS --fail-with-body "$BASE/v1/chat/completions" \\
  -H "Authorization: Bearer $INFRX_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d "$BODY" \\
  | jq -r '.choices[0].message.content, .usage'`,
        python: `${PY_HEAD}
${pyVideo}
status, headers, body = api("POST", "/v1/chat/completions", {
    "model": "${id}",
    "max_tokens": ${max},
    "messages": ${videoMessages("VIDEO_URL")},
})
print(status, headers.get("Inference-Id"))
print(body["choices"][0]["message"]["content"])
print(body["usage"])`,
        javascript: `${JS_HEAD}
${jsVideo}
const { status, headers, json } = await api("POST", "/v1/chat/completions", {
  body: {
    model: "${id}",
    max_tokens: ${max},
    messages: ${jsVideoMessages("VIDEO_URL")},
  },
});
console.log(status, headers.get("Inference-Id"));
console.log(json.choices[0].message.content);
console.log(json.usage);`,
      },
    },
    {
      id: "stream",
      title: "Streamed text output (SSE)",
      blurb:
        'With "stream": true the answer arrives as server-sent events. Only the output is streamed: the video is a finished file, not a live feed.',
      snippets: {
        curl: `${CURL_ENV}
${curlVideo}
${curlBody(', "stream": true')}
curl -sS --fail-with-body -N "$BASE/v1/chat/completions" \\
  -H "Authorization: Bearer $INFRX_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d "$BODY"`,
        python: `${PY_HEAD}
${pyVideo}
request = urllib.request.Request(BASE + "/v1/chat/completions", method="POST", headers={
    "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
    data=json.dumps({
        "model": "${id}",
        "max_tokens": ${max},
        "stream": True,
        "messages": ${videoMessages("VIDEO_URL")},
    }).encode())
event = None
with urllib.request.urlopen(request, timeout=600) as reply:
    for line in reply:
        line = line.decode().rstrip("\\n")
        if line.startswith("event: "):
            event = line[7:]            # infrx.progress frames carry job progress, not text
        elif line.startswith("data: "):
            data = line[6:]
            if data == "[DONE]":
                break
            chunk = json.loads(data)
            if event is None:
                for choice in chunk.get("choices", []):
                    print(choice["delta"].get("content", ""), end="", flush=True)
                if chunk.get("usage"):
                    print("\\nusage:", chunk["usage"])
        elif line == "":
            event = None`,
        javascript: `${JS_HEAD}
${jsVideo}
const reply = await fetch(BASE + "/v1/chat/completions", {
  method: "POST",
  headers: { Authorization: \`Bearer \${KEY}\`, "Content-Type": "application/json" },
  body: JSON.stringify({
    model: "${id}",
    max_tokens: ${max},
    stream: true,
    messages: ${jsVideoMessages("VIDEO_URL")},
  }),
});
const decoder = new TextDecoder();
let buffer = "";
for await (const bytes of reply.body) {
  buffer += decoder.decode(bytes, { stream: true });
  let end;
  while ((end = buffer.indexOf("\\n\\n")) !== -1) {
    const frame = buffer.slice(0, end);
    buffer = buffer.slice(end + 2);
    const lines = frame.split("\\n");
    if (lines.some((l) => l.startsWith("event: "))) continue; // infrx.progress, not text
    const data = lines.find((l) => l.startsWith("data: "))?.slice(6);
    if (!data || data === "[DONE]") continue;
    const chunk = JSON.parse(data);
    for (const choice of chunk.choices) process.stdout.write(choice.delta.content ?? "");
    if (chunk.usage) console.log("\\nusage:", chunk.usage);
  }
}`,
      },
    },
    {
      id: "upload",
      title: "Upload a local video, then use it",
      blurb:
        "Create an upload, PUT the bytes to it, complete it, then name it as infrx-upload:<handle> in the video part. The handle belongs to your account only.",
      snippets: {
        curl: `${CURL_ENV}
SIZE=$(wc -c < ${CLIP_FILE} | tr -d ' ')
UPL=$(curl -sS --fail-with-body "$BASE/v1/uploads" \\
  -H "Authorization: Bearer $INFRX_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d "{\\"max_bytes\\": $SIZE, \\"bytes\\": $SIZE, \\"accepted_mime\\": [\\"video/mp4\\"]}" \\
  | jq -r .upload_handle)
curl -sS --fail-with-body -X PUT "$BASE/v1/uploads/$UPL" \\
  -H "Authorization: Bearer $INFRX_API_KEY" \\
  -H "Content-Type: video/mp4" --data-binary @${CLIP_FILE}
curl -sS --fail-with-body -X POST "$BASE/v1/uploads/$UPL/complete" \\
  -H "Authorization: Bearer $INFRX_API_KEY" | jq .media
curl -sS --fail-with-body "$BASE/v1/chat/completions" \\
  -H "Authorization: Bearer $INFRX_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d "{\\"model\\": \\"${id}\\", \\"max_tokens\\": ${max}, \\"messages\\": [{\\"role\\": \\"user\\", \\"content\\": [{\\"type\\": \\"video_url\\", \\"video_url\\": {\\"url\\": \\"infrx-upload:$UPL\\"}}, {\\"type\\": \\"text\\", \\"text\\": \\"${CAPTION_PROMPT}\\"}]}]}" \\
  | jq -r '.choices[0].message.content'`,
        python: `${PY_HEAD}
clip = open("${CLIP_FILE}", "rb").read()
status, _, ticket = api("POST", "/v1/uploads", {
    "max_bytes": len(clip), "bytes": len(clip), "accepted_mime": ["video/mp4"],
})
handle = ticket["upload_handle"]
status, _, _ = api("PUT", f"/v1/uploads/{handle}", raw=clip, headers={"Content-Type": "video/mp4"})
assert status == 204, status
status, _, done = api("POST", f"/v1/uploads/{handle}/complete")
print(status, done["media"])
status, _, body = api("POST", "/v1/chat/completions", {
    "model": "${id}",
    "max_tokens": ${max},
    "messages": ${videoMessages('"infrx-upload:" + handle')},
})
print(body["choices"][0]["message"]["content"])`,
        javascript: `${JS_HEAD}
import { readFile } from "node:fs/promises";

const clip = await readFile("${CLIP_FILE}");
const ticket = await api("POST", "/v1/uploads", {
  body: {
    max_bytes: clip.length,
    bytes: clip.length,
    accepted_mime: ["video/mp4"],
  },
});
const handle = ticket.json.upload_handle;
const put = await api("PUT", \`/v1/uploads/\${handle}\`, { raw: clip, headers: { "Content-Type": "video/mp4" } });
if (put.status !== 204) throw new Error(\`upload PUT answered \${put.status}\`);
const done = await api("POST", \`/v1/uploads/\${handle}/complete\`);
console.log(done.status, done.json.media);
const { json } = await api("POST", "/v1/chat/completions", {
  body: {
    model: "${id}",
    max_tokens: ${max},
    messages: ${jsVideoMessages('"infrx-upload:" + handle')},
  },
});
console.log(json.choices[0].message.content);`,
      },
    },
    {
      id: "inline",
      title: "Inline a small video as base64",
      blurb:
        "For a small clip, send it in the request as a data: URL instead of uploading it. The decoded video and the whole body stay within the limits above.",
      snippets: {
        curl: `${CURL_ENV}
VIDEO=$(base64 < ${CLIP_FILE} | tr -d '\\n')
printf '{"model": "${id}", "max_tokens": ${max}, "messages": [{"role": "user", "content": [{"type": "video_url", "video_url": {"url": "data:video/mp4;base64,%s"}}, {"type": "text", "text": "${CAPTION_PROMPT}"}]}]}' "$VIDEO" \\
  | curl -sS --fail-with-body "$BASE/v1/chat/completions" \\
      -H "Authorization: Bearer $INFRX_API_KEY" \\
      -H "Content-Type: application/json" --data-binary @- \\
  | jq -r '.choices[0].message.content'`,
        python: `${PY_HEAD}
import base64

url = "data:video/mp4;base64," + base64.b64encode(open("${CLIP_FILE}", "rb").read()).decode()
status, _, body = api("POST", "/v1/chat/completions", {
    "model": "${id}",
    "max_tokens": ${max},
    "messages": ${videoMessages("url")},
})
print(status, body["choices"][0]["message"]["content"])`,
        javascript: `${JS_HEAD}
import { readFile } from "node:fs/promises";

const url = "data:video/mp4;base64," + (await readFile("${CLIP_FILE}")).toString("base64");
const { status, json } = await api("POST", "/v1/chat/completions", {
  body: {
    model: "${id}",
    max_tokens: ${max},
    messages: ${jsVideoMessages("url")},
  },
});
console.log(status, json.choices[0].message.content);`,
      },
    },
    {
      id: "async",
      title: "Asynchronous job: submit, poll, fetch the result",
      blurb:
        "POST /v1/jobs answers 202 as soon as the job is durably accepted. Wait the Retry-After it carries before each status read (a 429 or 503 carries its own), stop after a bounded number of reads, and read the result only when the job succeeded, before result_expires_at.",
      snippets: {
        curl: `${CURL_ENV}
${curlVideo}
ITEM_ID="clip-0001"         # your own stable id for this item: reuse it on every retry
MAX_POLLS=${MAX_POLLS}               # stop waiting after this many status reads
HEADERS=$(mktemp) STATUS=$(mktemp)
trap 'rm -f "$HEADERS" "$STATUS"' EXIT
retry_after() { tr -d '\\r' < "$HEADERS" | awk -F': *' 'tolower($1) == "retry-after" { print $2 }'; }
${curlBody()}
JOB=$(curl -sS --fail-with-body -D "$HEADERS" "$BASE/v1/jobs" \\
  -H "Authorization: Bearer $INFRX_API_KEY" \\
  -H "Content-Type: application/json" \\
  -H "Idempotency-Key: $ITEM_ID" \\
  -d "$BODY" | jq -r .job_handle)
WAIT=$(retry_after) STATE=""
for _ in $(seq "$MAX_POLLS"); do
  sleep "\${WAIT:-2}"
  CODE=$(curl -sS -o "$STATUS" -D "$HEADERS" -w '%{http_code}' "$BASE/v1/jobs/$JOB" \\
    -H "Authorization: Bearer $INFRX_API_KEY")
  case $CODE in
    200) STATE=$(jq -r .state "$STATUS") ;;
    429|503) WAIT=$(retry_after); continue ;;   # busy: wait as long as the reply says
    *) cat "$STATUS" >&2; exit 1 ;;
  esac
  case $STATE in succeeded|failed|cancelled|expired) break ;; esac
done
jq '{state, cause, result_expires_at, usage}' "$STATUS"
if [ "$STATE" != succeeded ]; then
  echo "job $JOB is \${STATE:-unknown}: no result to read" >&2
  exit 1
fi
curl -sS --fail-with-body "$BASE/v1/jobs/$JOB/result" -H "Authorization: Bearer $INFRX_API_KEY" \\
  | jq -r '.response.choices[0].message.content'`,
        python: `${PY_HEAD}
import time

${pyVideo}
MAX_POLLS = ${MAX_POLLS}  # stop waiting after this many status reads
item = {
    "model": "${id}",
    "max_tokens": ${max},
    "messages": ${videoMessages("VIDEO_URL")},
}
status, headers, accepted = api("POST", "/v1/jobs", item, headers={"Idempotency-Key": "clip-0001"})
if status != 202:
    raise SystemExit(f"not accepted: {status} {accepted}")
job, wait = accepted["job_handle"], int(headers.get("Retry-After") or 2)
state, job_status = None, {}
for _ in range(MAX_POLLS):
    time.sleep(wait)
    status, headers, reply = api("GET", f"/v1/jobs/{job}")
    if status in (429, 503):  # busy: wait as long as the reply says
        wait = int(headers.get("Retry-After") or wait)
        continue
    if status != 200:
        raise SystemExit(f"status read failed: {status} {reply}")
    job_status, state = reply, reply["state"]
    if state in ("succeeded", "failed", "cancelled", "expired"):
        break
print(state, job_status.get("cause"), job_status.get("result_expires_at"))
if state != "succeeded":
    raise SystemExit(f"job {job} is {state}: no result to read")
status, _, result = api("GET", f"/v1/jobs/{job}/result")
print(result["response"]["choices"][0]["message"]["content"])`,
        javascript: `${JS_HEAD}
${jsVideo}
const MAX_POLLS = ${MAX_POLLS}; // stop waiting after this many status reads
const item = {
  model: "${id}",
  max_tokens: ${max},
  messages: ${jsVideoMessages("VIDEO_URL")},
};
const accepted = await api("POST", "/v1/jobs", { body: item, headers: { "Idempotency-Key": "clip-0001" } });
if (accepted.status !== 202) throw new Error(JSON.stringify(accepted.json));
const job = accepted.json.job_handle;
let wait = Number(accepted.headers.get("Retry-After") ?? "2");
let state = null;
let status = {};
for (let poll = 0; poll < MAX_POLLS; poll++) {
  await new Promise((resolve) => setTimeout(resolve, wait * 1000));
  const reply = await api("GET", \`/v1/jobs/\${job}\`);
  if (reply.status === 429 || reply.status === 503) {
    wait = Number(reply.headers.get("Retry-After") ?? wait); // busy: wait as long as the reply says
    continue;
  }
  if (reply.status !== 200) throw new Error(\`status read failed: \${reply.status} \${JSON.stringify(reply.json)}\`);
  status = reply.json;
  state = status.state;
  if (["succeeded", "failed", "cancelled", "expired"].includes(state)) break;
}
console.log(state, status.cause, status.result_expires_at);
if (state !== "succeeded") {
  console.error(\`job \${job} is \${state}: no result to read\`);
  process.exit(1);
}
const result = (await api("GET", \`/v1/jobs/\${job}/result\`)).json;
console.log(result.response.choices[0].message.content);`,
      },
    },
    {
      id: "resume",
      title: "Resume after a lost connection",
      blurb:
        "Send the same body with the same Idempotency-Key: you get the original job back (idempotency_replayed: true), never a second job or a second charge. Replay its output from the last event id you saw.",
      snippets: {
        curl: `${CURL_ENV}
${curlVideo}
${curlBody()}
ACCEPTED=$(curl -sS --fail-with-body "$BASE/v1/jobs" \\
  -H "Authorization: Bearer $INFRX_API_KEY" \\
  -H "Content-Type: application/json" \\
  -H "Idempotency-Key: clip-0001" \\
  -d "$BODY")
echo "$ACCEPTED" | jq '{job_handle, idempotency_replayed}'
JOB=$(echo "$ACCEPTED" | jq -r .job_handle)
LAST_ID="1-2"               # the id: of the last event you received ("" for all of them)
curl -sS --fail-with-body -N "$BASE/v1/jobs/$JOB/events" \\
  -H "Authorization: Bearer $INFRX_API_KEY" \\
  -H "Last-Event-ID: $LAST_ID"`,
        python: `${PY_HEAD}
${pyVideo}
item = {
    "model": "${id}",
    "max_tokens": ${max},
    "messages": ${videoMessages("VIDEO_URL")},
}
status, headers, accepted = api("POST", "/v1/jobs", item, headers={"Idempotency-Key": "clip-0001"})
print(status, accepted["job_handle"], accepted["idempotency_replayed"])
request = urllib.request.Request(
    BASE + f"/v1/jobs/{accepted['job_handle']}/events",
    headers={"Authorization": f"Bearer {KEY}", "Last-Event-ID": "1-2"})
with urllib.request.urlopen(request, timeout=600) as reply:
    for line in reply:
        print(line.decode(), end="")`,
        javascript: `${JS_HEAD}
${jsVideo}
const item = {
  model: "${id}",
  max_tokens: ${max},
  messages: ${jsVideoMessages("VIDEO_URL")},
};
const accepted = await api("POST", "/v1/jobs", { body: item, headers: { "Idempotency-Key": "clip-0001" } });
console.log(accepted.status, accepted.json.job_handle, accepted.json.idempotency_replayed);
const events = await fetch(\`\${BASE}/v1/jobs/\${accepted.json.job_handle}/events\`, {
  headers: { Authorization: \`Bearer \${KEY}\`, "Last-Event-ID": "1-2" },
});
process.stdout.write(await events.text());`,
      },
    },
    {
      id: "revoke",
      title: "After you revoke a key",
      blurb:
        "Revoke a key on the API Keys page and any new request with it is refused with 401 invalid_api_key.",
      snippets: {
        curl: `${CURL_ENV}
curl -sS -o /dev/null -w '%{http_code}\\n' "$BASE/v1/chat/completions" \\
  -H "Authorization: Bearer $INFRX_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '${textBody(model, "ping")}'`,
        python: `${PY_HEAD}
status, _, body = api("POST", "/v1/chat/completions", {
    "model": "${id}", "max_tokens": ${max}, "messages": [{"role": "user", "content": "ping"}]})
print(status, body["error"]["code"] if status >= 400 else "accepted")`,
        javascript: `${JS_HEAD}
const { status, json } = await api("POST", "/v1/chat/completions", {
  body: { model: "${id}", max_tokens: ${max}, messages: [{ role: "user", content: "ping" }] },
});
console.log(status, status >= 400 ? json.error.code : "accepted");`,
      },
    },
  ];
}
