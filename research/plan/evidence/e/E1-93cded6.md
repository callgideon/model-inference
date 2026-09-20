# E1 — Distinct corpus and authenticated benchmark client (review round 3)

## Task and status

- **Task:** E1 (track E, verification). Corpus + benchmark client tooling for the
  PERF-PILOT and MEDIA-PARITY oracles. This round fixes the one blocking finding of
  review round 3 (a signed positional video URL written whole to every sink), closes
  its defect class with a property-style leak matrix, and applies the coordinator's
  ffmpeg-pin ruling. It still runs no load test and makes no performance claim.
- **Status:** **implemented, not integrated.** No live GPU, gateway, AWS, Supabase or
  `*.callbill.ai` endpoint was contacted. Every benchmark HTTP request below went to an
  in-process `httpx.MockTransport` fake gateway. The one network request this session
  made was a 40 MB GET of the pinned ffmpeg tarball from `johnvansickle.com`, to
  re-verify the recorded sha256 against a versioned URL (the coordinator's ruling
  allowed up to ~100 MB for exactly this check). Nothing was uploaded anywhere.
- **Owner/session:** Claude Opus 5 implementation session, worktree
  `.claude/worktrees/codex-e1`.
- **Relation to earlier reports:** [`E1-38b07b5.md`](E1-38b07b5.md) (initial),
  [`E1-a1cb4f4.md`](E1-a1cb4f4.md) (round 1) and [`E1-6c58be7.md`](E1-6c58be7.md)
  (round 2) are kept intact; round 2 gains one appended verification-log line naming
  the two claims this round supersedes (the manifest hash and "no known unresolved
  findings").

## Source

| Field | Value |
|---|---|
| Base SHA | `25b9829` (docs: reviewed parallel implementation handoffs) |
| Earlier implementation SHAs | `38b07b5`, `1c7d96d`, `a1cb4f4`, `f702fc0`, `19b96d3`, `6c58be7` |
| Reviewed SHA (round 3) | `a06937a` |
| Implementation SHA (this round) | `10f49b3` (blocking fix + leak matrix) and `93cded6` (ffmpeg pin) |
| Evidence SHA | this commit (separate, follows the commits it cites) |
| Branch / worktree | `codex/e1-corpus-bench` in `.claude/worktrees/codex-e1` |
| Integrated SHA | none — coordinator integrates into `claude/infrx-impl` |

Diff against the reviewed head `a06937a`: `bench.py` +100/−28, `tests/test_leaks.py`
+255 (new), `tests/fake_gateway.py` +13/−1, `tests/test_corpus.py` +3/−0,
`corpus/build.py` +25/−10, `corpus/README.md` +8/−4, `corpus/manifest.json` +7/−1
(the `ffmpeg` block only). No media, no binary, no `results/` change:
`models/marlin2b/results/bench.jsonl` is still byte-identical to base `25b9829`.

## Review findings and root-cause fixes

### Blocking finding (round 3): a signed positional video URL is written with its full query string to stdout, `bench.jsonl` and every raw row

**Confirmed, reproduced with the reviewer's own command, and fixed at the sink, not
at the two call sites.**

The reviewer's proposed two-line fix (`urlsplit(video).path` before `basename()` in
`build_schedule` and `make_config`) would have closed the reported path. It was not
taken, because the *cause* is one level down: `redact()` could only see a URL whose
text still began with a lowercase `https?://`, so **any** transform that dropped or
changed the scheme walked a signature past it. `basename()` was one such transform;
the reviewer's own non-blocking probe found two more (`HTTPS://…` and a scheme-less
`bucket.host/key?X-Amz-Signature=…` echoed by a server). Fixing only the label would
have left the class open, and the leak matrix below shows it in numbers: 8 of the 120
cells leak through server echoes with the label fix alone.

| Part | Root cause | Fix |
|---|---|---|
| The label | `os.path.basename()` on the positional `video` argument kept `clip.mp4?X-Amz-Credential=…&X-Amz-Signature=…` — scheme and host gone, query intact — as `summary.video` and as every raw row's `clip_id` | `video_label()`: an http(s) URL is passed through **whole** so the sink can reduce it; a local file keeps its basename, as before. `is_url()` (case-insensitive) replaces the three ad-hoc `startswith(("http://", "https://"))` checks |
| The sink | `URL_QUERY = (https?://[^\s"\\]{1,2000}?)\?…` — lowercase, scheme-anchored, query only | `URL` now matches **any** scheme in any case and reduces every URL to `scheme://host/path`: the query string *and* the fragment become `?[redacted-query]`, userinfo becomes `[redacted-userinfo]@`. `SIGNED_QUERY` catches a signature-bearing query with no scheme in front of it |
| Key prefixes | only the full key was replaced | every prefix ≥ `KEY_MIN_PREFIX` (8) is replaced, longest first, so neither a cut nor a partial server echo leaves a usable fragment |
| Sinks outside `redact()` | argparse writes usage and errors straight to stderr, echoing the offending argv token — and the positional argument is the signed URL; an uncaught traceback quotes the request URL | `Parser._print_message` routes all argparse output through `redact()`; `main()` is now a wrapper that redacts a `SystemExit` string or a `traceback.format_exc()` and delegates to `_run()` |

`redact()` is the single choke point for stdout, stderr, `--out`, the raw JSONL,
`sys.exit` strings, argparse output and tracebacks; it still runs **before** every
truncation and is idempotent (asserted).

### Leak matrix (the class sweep)

8 input shapes × 15 outcomes = **120 runs of `bench.main()`** against the fake gateway,
with a canary key (41 characters, containing `/`, `+`, `=`), a canary signed
**positional** video URL, a canary signed **upload destination**, and canary signatures
echoed back inside error bodies. A cell counts as leaking if any of the four captured
sinks (stdout, stderr, `--out`, raw JSONL) contains the canary signature, the canary
credential, `X-Amz-Signature`, `X-Amz-Credential`, or any ≥ 8-character prefix of the
key.

| Tree | Cells | Leaking cells | Sinks hit |
|---|---|---|---|
| `bench.py` @ `a06937a` (reviewed) | 120 | **64** | stdout 64, `--out` 64, raw 64 |
| `bench.py` @ `93cded6` (now) | 120 | **0** | none |

The 64 are the 60 positional-signed-URL cells (4 forms × 15 outcomes) plus the 8
server-echo cells (`HTTPS://` and scheme-less, one per form), overlapping in 4.

Input shapes: `text`, `video_b64`, `upload`, `video_url` — each over corpus clips and
over the signed positional URL. Outcomes: 200 stream; 200 truncated (no `[DONE]`, no
`finish_reason`, no usage); 401 echoing the key and the URL; 401 echoing `HTTPS://…`
and a scheme-less signed query; 401 with a `\uXXXX`-escaped key straddling the 200-char
cut; 401 non-JSON body straddling the cut; 402 with `{"error": "<string>"}`; 429 with a
JSON **list** body; 429 then a successful retry; 500 echoing key and URL; 307 to a
signed URL (body and `Location`); `ConnectError`; `ReadTimeout`; upload `PUT` 403 to a
signed destination; and an SSE `error` event mid-stream carrying the key.

### Non-blocking notes applied

| Note | Fix |
|---|---|
| `URL_QUERY` is case-sensitive and needs a scheme | closed as part of the blocking fix (`URL` + `SIGNED_QUERY`) |
| `err.get` on a non-dict error body raises inside `_send`, so a 429 with a list body is counted **failed** instead of **rejected** | the parsed body is type-checked: `{"error": "<str>"}` becomes a message, a list or bare value falls back to the raw body. The matrix fingerprints `429_list_body` as `rejected=2`, so the miscount cannot come back |
| `resolve_mm_kwargs()` re-probes the clip duration with ffprobe on the event loop for every attempt of the single-video path | resolved once in `make_config()` (`cfg["mm_fixed"]`); a corpus clip still uses its known duration |
| Evidence quoted `PY=.claude/venvs/e1/bin/python` and `CORPUS_CACHE=.claude/corpus-cache` relative to a worktree where neither exists | the Commands section below is absolute and copy-pasteable |
| SSE `error` event code was neither redacted nor cut before reaching a row | same treatment as the HTTP path: `redact(...)[:120]` |

### Coordinator ruling: versioned ffmpeg URL

`https://johnvansickle.com/ffmpeg/releases/ffmpeg-7.0.2-amd64-static.tar.xz` exists as
a **versioned** artifact beside the moving alias. Downloaded 2026-09-20 (41,888,096
bytes, `Last-Modified: Sat, 24 Aug 2024 16:01:05 GMT`): sha256
`abda8d77ce8309141f83ab8edf0596834087c52467f6badf376a6a2a4c87cf67` — **exactly** the
recorded pin — and md5 `7fa72b652e19bf84c9461e332ea1cdf3`, matching upstream's own
`.md5` sidecar and the alias byte-for-byte. Nothing is pending here.

So `FFMPEG_PIN.tarball_url` is now the versioned URL, with
`tarball_url_fallbacks` = [the unversioned `ffmpeg-release-amd64-static.tar.xz` alias,
`old-releases/ffmpeg-7.0.2-amd64-static.tar.xz`]. The `old-releases/` path is where the
file will move when 7.0.2 is retired; **it is not there yet** (that directory tops out
at 6.0.1, checked this session), so it is recorded as the documented destination, not
as a URL that resolves today. `ensure_ffmpeg()` tries the list in order and the sha256
decides, whichever answered. `tarball_md5` and `tarball_bytes` are recorded as
secondary identity. The manifest's `ffmpeg` block mirrors `FFMPEG_PIN` exactly and
`test_manifest_validates_and_has_the_required_scale` now asserts both the mirror and
that the primary URL names the version.

### Coordinator ruling: `make bench-test` must work with only httpx + pytest

Confirmed: `python -m pytest models/marlin2b/tests -q` from the repo root passes with
`CORPUS_CACHE` pointed at an **empty** directory and with `socket.connect`,
`socket.create_connection` and `socket.getaddrinfo` patched to raise — 27 passed, 0
skipped, `NETWORK ATTEMPTS: []`. The venv contains httpx 0.28.1, pytest 9.1.1 and their
dependencies only. **No test needs built media**, so there is nothing to skip and
nothing to list as "not run": `test_corpus.py` reads the committed `manifest.json`
only, and `test_bench.py`/`test_leaks.py` synthesise fake clip bytes in a temporary
directory. The one test that touches the real loader
(`test_real_load_corpus_reads_the_committed_manifest`) asserts manifest-derived paths
without opening them.

## Requirement coverage

| Test (file::name) | Invariant demonstrated |
|---|---|
| `test_leaks.py::test_no_form_and_no_outcome_can_emit_a_canary` | the class: 8 input shapes × 15 outcomes × 4 sinks, zero canary key / ≥8-char key prefix / signature / credential bytes; each outcome fingerprinted by (accepted, rejected, failed, error class) so the sweep cannot pass by emitting nothing |
| `test_leaks.py::test_a_signed_positional_url_is_reduced_in_every_sink` | the reported finding: `summary.video`, the `--out` line and every raw `clip_id` are `https://host/path?[redacted-query]`; a local file still reports its basename |
| `test_leaks.py::test_redact_reduces_every_url_shape_and_leaves_no_key_prefix` | scheme-preserving reduction of uppercase, scheme-less, userinfo and fragment shapes; a clean URL survives intact; `redact()` is idempotent; a query is dropped even with no key set |
| `test_leaks.py::test_no_canary_survives_a_random_context_property_sweep` | 400 seeded random contexts (JSON-encoded and raw, hostile filler) × 4 truncation points: no canary and no ≥8-char key prefix survives, before **or** after a cut |
| `test_bench.py::test_server_controlled_fields_cannot_leak_the_key_or_a_signed_url` | round-1/2 shapes still held: `error.code`, straddled 120/200 cuts, escaped keys, rejected signed PUT |
| `test_bench.py::test_api_key_never_appears_in_any_output` | the key is sent as a Bearer header and appears in no sink, including an upstream exception that quotes the header |
| `test_bench.py` (9 others) | unchanged invariants: deterministic schedule independent of latency, open-loop lag, truncated-200 ≠ accepted, retry accounting, rejected/failed denominators, TTFT from the first content delta, usage-only token counts, percentile suppression, request forms and upload flow, real manifest load, historical CLI |
| `test_corpus.py::test_manifest_validates_and_has_the_required_scale` | manifest validates, ≥64 built / ≥32 fast clips, **and** the ffmpeg identity mirrors `FFMPEG_PIN` with a versioned primary URL |
| `test_corpus.py` (10 others) | unchanged invariants: content/recipe distinctness and non-overlap, diversity, labels vs probed pixels, `source_upscaled` on both axes, validator rejections, licence fields, negative fixtures, unbuilt accounting, no silent re-pin, `plan` preserving pins and `built_at_utc` |

## Environment

| Field | Value |
|---|---|
| Host | local Linux dev box (`Linux 7.0.0-1010-aws`), **not** the g6e dev instance; no GPU used |
| Classification | local development only. No staging, no production, no cloud API call |
| Python | 3.12.3, venv `/home/rey/workspace/rey/code/model-inference/.claude/venvs/e1` |
| Packages | httpx 0.28.1, httpcore 1.0.9, h11 0.16.0, anyio 4.15.1, certifi 2026.7.22, idna 3.20, pytest 9.1.1, pluggy 1.6.0, iniconfig 2.3.0, packaging 26.3, pygments 2.21.0 (no other third-party package) |
| Gateway | `models/marlin2b/tests/fake_gateway.py` over `httpx.MockTransport`, in-process |
| Corpus cache | `/home/rey/workspace/rey/code/model-inference/.claude/corpus-cache` (git-ignored, outside the worktree), 72 files |
| Media tooling | pinned static `ffmpeg 7.0.2-amd64-static`, tarball sha256 `abda8d77…cf67` (re-downloaded and re-hashed this session from the versioned URL), ffmpeg `e7e7fb30…eb99`, ffprobe `4f231a19…450d`. No clip was rebuilt; no system ffmpeg exists on this host and none was installed; no docker image or container was touched |
| Env var names used | `MARLIN_API_KEY`, `INFRX_API_KEY` (canary values only), `CORPUS_CACHE`, `PYTHONPATH`, `PYTHONDONTWRITEBYTECODE` |

## Commands

Absolute paths, copy-pasteable. `PY=/home/rey/workspace/rey/code/model-inference/.claude/venvs/e1/bin/python`,
`W=/home/rey/workspace/rey/code/model-inference/.claude/worktrees/codex-e1`,
`CACHE=/home/rey/workspace/rey/code/model-inference/.claude/corpus-cache`,
`EMPTY=<scratch>/emptycache` (an empty directory), `PLUG=<scratch>/plug` (a pytest
plugin `nonet.py` that makes `socket.connect`, `socket.create_connection` and
`socket.getaddrinfo` raise and prints every attempt). All runs 2026-09-20,
20:43–20:53 UTC, in `$W`.

| # | Command | Exit | Result |
|---|---|---|---|
| 1 | `env -u MARLIN_API_KEY -u INFRX_API_KEY CORPUS_CACHE=$EMPTY PYTHONPATH=$PLUG $PY -m pytest models/marlin2b/tests -q -p no:cacheprovider -p nonet` | 0 | **27 passed** in 6.51 s, 0 skipped; `NETWORK ATTEMPTS: []` |
| 2 | `CORPUS_CACHE=$EMPTY $PY models/marlin2b/tests/test_bench.py` | 0 | 12 passed (plain script) |
| 3 | `CORPUS_CACHE=$EMPTY $PY models/marlin2b/tests/test_corpus.py` | 0 | 11 passed (plain script) |
| 4 | `CORPUS_CACHE=$EMPTY $PY models/marlin2b/tests/test_leaks.py` | 0 | 4 passed (plain script) |
| 5 | `CORPUS_CACHE=$CACHE $PY models/marlin2b/corpus/build.py validate` | 0 | `0 error(s)` |
| 6 | `CORPUS_CACHE=$CACHE $PY models/marlin2b/corpus/build.py verify` | 0 | `verified 72 file(s), 0 missing, 0 error(s)` (re-hashed 64 clips + 4 negatives + 4 sources) |
| 7 | reviewer's round-3 reproduction: `env -u MARLIN_API_KEY -u INFRX_API_KEY $PY models/marlin2b/bench.py 'https://r3-bucket.invalid/private/clip.mp4?X-Amz-Credential=AKIAR3CANARYCRED&X-Amz-Signature=R3SIGcanary…' -c 1 -n 2 --prompt p --no-warmup --out <scratch>/bench.jsonl --dry-run-transport fake_gateway:transport` | 0 | `"video": "https://r3-bucket.invalid/private/clip.mp4?[redacted-query]"`, same for every raw `clip_id`; canary count **0** in stdout, stderr, `bench.jsonl` and `raw/*.jsonl` (was 1/1/2 at `a06937a`) |
| 8 | seeded dry run ×2 (`--corpus … --subset fast --rate 4 --requests 40 --seed 7 --target gateway --forms video_b64,text,upload --dry-run-transport fake_gateway:transport`), `MARLIN_API_KEY` = 41-char canary, `CORPUS_CACHE=$CACHE` | 0, 0 | 40 accepted / 0 rejected / 0 failed; `distinct_clips` 24, `distinct_clips_scheduled` 32, cold 24, warm 3; `schedule_lag_s.max` 0.1237 s; p95/p99 suppressed; the two runs' (seq, clip_id, form, scheduled_s) lists are **identical** |
| 9 | signed-positional dry run: `$PY models/marlin2b/bench.py '<canary signed URL>' -c 2 -n 6 --prompt p --no-warmup --target gateway --forms video_url …` | 0 | 6 accepted; `"video": "https://r3-bucket.invalid/private/clip.mp4?[redacted-query]"` |
| 10 | leak grep over **all 11** files produced by #8 and #9 (4 canaries + every 8…41-char key prefix) | 0 | `TOTAL LEAKS: 0` — per file: `bench.jsonl` 0, `raw-1.jsonl` 0, `raw-2.jsonl` 0, `stdout-1/2.txt` 0, `stderr-1/2.txt` 0, `url.jsonl` 0, `url-raw.jsonl` 0, `url-stdout.txt` 0, `url-stderr.txt` 0 |
| 11 | new tests against **pre-fix** `bench.py` (`git show a06937a:models/marlin2b/bench.py` into a scratch tree with the current tests): `$PY -m pytest tests/test_leaks.py -q` | 1 | **4 failed** — first failures: `text / 401_echo_uppercase_and_schemeless_url: AKIACANARYCREDENTIAL leaked into stdout`; `'clip.mp4?X-A…' == 'https://r3-b…[redacted-query]'`; `'HTTPS://HOST…' == 'HTTPS://HOST…[redacted-query]'`; the random sweep on a scheme-less query. Same 4 failures as a plain script |
| 12 | leak-matrix counter over both trees (120 cells each, counting instead of asserting) | 0 | pre-fix `a06937a`: **64/120 leaking cells** (stdout 64, `--out` 64, raw 64); now: **0/120** |
| 13 | `curl -sI` + full GET of `https://johnvansickle.com/ffmpeg/releases/ffmpeg-7.0.2-amd64-static.tar.xz`, `sha256sum`, `md5sum`, and `curl -s …/ffmpeg-release-amd64-static.tar.xz.md5` | 0 | 41,888,096 bytes; sha256 `abda8d77…cf67` = the recorded pin; md5 `7fa72b65…cdf3` = upstream's sidecar. `old-releases/ffmpeg-7.0.2-amd64-static.tar.xz` → **404**, that directory tops out at 6.0.1 |
| 14 | `/home/rey/workspace/rey/code/model-inference/.claude/venvs/infrx-api/bin/python -m pytest apps/infrx-api/tests -q` | 0 | 23 passed, 2 warnings (untouched by this branch, run as a regression check) |
| 15 | `git -C $W status --short`; `git diff --quiet 25b9829..HEAD -- models/marlin2b/results/bench.jsonl`; `git diff --numstat a06937a..HEAD` | 0 | working tree clean; `bench.jsonl` unchanged since base; 7 files touched, all E1-owned, no binary |

Seeds: `--seed 7` (#8), `--seed 3`/`11`/`42`/`43` inside `test_bench.py`, `20260920`
(the random property sweep), and `random.Random(seed)` is the only source of randomness
in `build_schedule`.

## Results

- **Passed:** 27 tests under pytest (12 `test_bench`, 11 `test_corpus`, 4 `test_leaks`),
  and the same 27 as three plain scripts. 120/120 leak-matrix cells clean.
- **Failures:** none on this tree. The 4 deliberate failures in #11 are the new tests
  run against the pre-fix `bench.py`, recorded to prove they are not vacuous.
- **Skips:** **none.** No test requires built media, network or a corpus cache, so
  nothing is skipped and nothing is claimed as passing without running.
- **Not run (not assumed to pass):** `build.py build`, `build.py verify --rederive`
  (no clip was rebuilt), any real gateway / vLLM / GPU run, any signed-URL server, any
  AWS or Supabase call, licence-page re-fetch, and the `old-releases/` fallback URL
  (404 today, by design).

## Failure drill

Injection is `FakeGateway`, in-process, plus a new `chat_override(request)` knob (one
callable that may return any response or raise any transport exception) and a
`stream_error` knob for an SSE `error` event.

| Injection point | Durable state before | Behaviour | Durable state after |
|---|---|---|---|
| 4xx/5xx body echoing the key and a signed URL (7 shapes incl. uppercase, scheme-less, `\uXXXX`-escaped, straddling the 120/200 cuts, `{"error": "<str>"}`, a JSON list) | `--out` absent or holding earlier lines | rejected/failed classified per `REJECT_STATUS`; message and code redacted then cut | one appended `--out` line + a raw row per attempt, both canary-free |
| 429 with `Retry-After: 0`, `--retries 1` | as above | one retried attempt; `attempts` 4, `rejected_attempts` 2, request counted once as **rejected** (not failed — the list-body crash is gone) | 2 raw rows per request, canary-free |
| 429 then 200 | as above | retry succeeds; `request_latency_s` spans both attempts | 3 raw rows for 2 requests |
| 307 to a signed URL (body + `Location`) | as above | `failed` / `http_307`; the target is reduced to `scheme://host/path` | canary-free |
| `ConnectError` / `ReadTimeout` quoting the URL and the Bearer header | as above | `failed`, `error_class` = the exception type, message redacted **then** cut at 200 | canary-free |
| upload `PUT` 403 to a signed destination | no upload recorded | `RuntimeError("upload PUT rejected with HTTP 403 (signed destination withheld)")`; the status survives, the URL does not | canary-free |
| `upload` form given an http(s) video | — | `FileNotFoundError` quoting the URL → reduced by the sink | canary-free |
| 200 stream truncated (no `[DONE]`/`finish_reason`/usage) | — | `failed` / `truncated_stream`, never accepted; exit code 1 | canary-free |
| SSE `error` event mid-stream carrying the key | — | `failed` / `stream_error_event`, code redacted then cut | canary-free |
| cached clip or source bytes drifting from the pin (round 2, re-run here) | pinned manifest | `sha256_mismatch`, pin kept, `validate` exit 1 | manifest pin intact |

Cleanup: every run writes only to a `tempfile.TemporaryDirectory()` or to the scratch
directory. No test writes to `models/marlin2b/results/`; the committed `bench.jsonl`
is unchanged since base, verified by `git diff --quiet`.

## Artifacts

| Artifact | Path | Identity |
|---|---|---|
| Benchmark client | `models/marlin2b/bench.py` | sha256 `eabe69c991001a2d…`, 748 lines |
| Leak matrix | `models/marlin2b/tests/test_leaks.py` | sha256 `ccc8e1395f7a35d3…`, 255 lines |
| Fake gateway | `models/marlin2b/tests/fake_gateway.py` | sha256 `de45977cc2e55b94…` |
| Corpus builder | `models/marlin2b/corpus/build.py` | sha256 `0cb0770f89724c09…` |
| Corpus manifest | `models/marlin2b/corpus/manifest.json` | sha256 `232bad7ecdfc030883f615e569f3ab2132177dd69da544d3ef67f43314010fe3`, 77,453 bytes (was `5799ad26…ce46`, 77,172 bytes; **only** the `ffmpeg` block changed, +7/−1) |
| Pinned ffmpeg tarball | `https://johnvansickle.com/ffmpeg/releases/ffmpeg-7.0.2-amd64-static.tar.xz` | sha256 `abda8d77ce8309141f83ab8edf0596834087c52467f6badf376a6a2a4c87cf67`, md5 `7fa72b652e19bf84c9461e332ea1cdf3`, 41,888,096 bytes |
| Dry-run outputs (11 files) | scratch only, not committed | canary key and canary signed URLs throughout; `TOTAL LEAKS: 0`. Deliberately **not** committed: they are throwaway canaries, and `results/` stays append-only history |

No credential, customer prompt, real API key, real signed URL or media byte appears in
this branch. Every host in the diff is a licence/source page, `*.invalid` or
`localhost`; every key-shaped literal is a throwaway canary defined in a test file.

## Changes

- **Owned paths touched:** `models/marlin2b/bench.py`, `models/marlin2b/tests/**`
  (`test_leaks.py` new, `fake_gateway.py`, `test_corpus.py`),
  `models/marlin2b/corpus/**` (`build.py`, `manifest.json`, `README.md`),
  `research/plan/evidence/e/` (this report + one appended log line in
  `E1-6c58be7.md`). Nothing else. `models/marlin2b/results/` is untouched.
- **Contract change request:** none. `01-contracts.md` shapes are unchanged; the upload
  handshake and `upload://` reference are as before.
- **Migration / deploy / rollback:** none. This is local tooling with no service, no
  schema and no deployment. Rollback is `git revert` of `10f49b3` and `93cded6`; the
  manifest revert also restores the previous ffmpeg pin block.
- **Behaviour change callers should know about:** for a positional http(s) video, the
  `video` field in `--out` and the `clip_id` in raw rows are now
  `scheme://host/path?[redacted-query]` instead of a bare basename. A local file is
  unchanged (basename). Nothing in `results/bench.jsonl` is rewritten.

## Limits

- Secret hygiene is now **structural** — one choke point, applied before truncation,
  covering stdout, stderr, files, argparse and tracebacks — and it is demonstrated over
  120 form × outcome cells and a 400-case random sweep. It is still not a proof: a
  secret that is neither the configured key (nor a ≥8-character prefix of it) nor part
  of a URL — say a bare bearer token echoed as a plain word in an error body — would
  pass through. Keys shorter than 8 characters are replaced in full only.
- `KEY_MIN_PREFIX = 8` is a judgement call: a 7-character prefix of a key can still
  appear. Lower it if the pilot's key format makes short prefixes sensitive.
- The `upload` flow, the `upload://` handle reference and the `mm_processor_kwargs`
  budget are still **unverified against a real server** (no gateway, no vLLM, no GPU
  exists yet). PERF-PILOT and MEDIA-PARITY remain unproven; this task ships the
  instrument, not a measurement.
- `httpx` still serialises a ~47 MB JSON payload on the event loop inside
  `client.stream(json=…)`; base64 and sha256 are off-loop, this is not. The seeded dry
  run shows `schedule_lag_s.max` 0.124 s at 4 arrivals/s with 35 MB clips. Surfaced
  honestly in the summary; **E4 must enforce a lag threshold** before quoting an
  open-loop cell.
- `cold` is per clip id and per run: a first use that is rejected or fails still marks
  later uses warm. Disclosed, unchanged.
- `build()` still drops dependent clips to `derived=None` when their source is
  `sha256_mismatch` or missing, so a rebuild after the source is restored re-pins those
  clips without comparing to the old pin. Loud (`validate` exits 1), recoverable from
  git, and not touched this round to keep the diff on the leak class — recorded for a
  later round. The clip-level path already keeps its pin.
- The `old-releases/` fallback URL 404s today; it is the documented destination for
  when upstream retires 7.0.2, not a verified mirror. The sha256 is the pin in every
  case. An S3 mirror of the tarball still needs an authorized write this session may
  not perform.
- Licence pages were verified in round 1 and not re-fetched; the NASA STEVE
  third-party-photography caveat stands as documented (nothing redistributed).
- A git-ignored `.claude/corpus-cache` (483 MB) still sits inside this worktree,
  separate from the real cache used above; the coordinator may delete it.

## Handback

- **Unresolved findings:** none known. The round-3 blocking finding is fixed at its
  root cause (the sink, not the two call sites), the reviewer's own reproduction
  re-run, and the defect class swept: 64 leaking cells at `a06937a`, 0 now. Both
  probed-but-non-blocking holes (uppercase scheme, scheme-less query) are closed and
  covered. One non-blocking note is deliberately deferred (the `build()` clip-pin wipe,
  under Limits).
- **Pending coordinator wiring:** (1) `make bench-test` (F2's target) should run
  `models/marlin2b/tests`; verified to pass from the repo root with only httpx + pytest,
  no network and an empty `CORPUS_CACHE`, 0 skipped; (2) an S3 mirror of the pinned
  ffmpeg tarball still needs an authorized write — the versioned upstream URL plus
  sha256 is the pin until then; (3) the stray git-ignored 483 MB cache inside the
  worktree can be removed. Ownership of `models/marlin2b/corpus/**` and
  `models/marlin2b/tests/**` is confirmed by the coordinator and no longer open.
- **Next unblocked task:** E4 can drive this client as soon as a gateway exists. The
  open-loop validity check is `summary.schedule_lag_s.max` (first attempts only) plus
  `percentiles.latency_from_scheduled_s`; coverage is read from `distinct_clips` (media
  actually sent), never `distinct_clips_scheduled`.

## Verification log

- 2026-09-20 (round 3, `10f49b3`/`93cded6`): the round-3 blocking finding confirmed by
  the reviewer's own reproduction and fixed **at the sink** rather than at the two
  reported call sites — `redact()` is now the one choke point every emitted byte passes
  (stdout, stderr, `--out`, raw rows, argparse output, `sys.exit` strings, tracebacks),
  it reduces every URL of any scheme in any case to `scheme://host/path` with query,
  fragment and userinfo replaced by markers, it drops any key prefix ≥ 8 characters, and
  it still runs before every truncation. `video_label()` keeps a URL whole for the sink
  and a local file's basename for history. Class swept with a new property-style leak
  matrix (8 input shapes × 15 outcomes × 4 sinks = 120 runs of `bench.main()` plus a
  400-case random sweep): **64 of 120 cells leaked at `a06937a`, 0 leak now**, and all 4
  new tests fail against the pre-fix `bench.py`. Three non-blocking notes applied
  (non-dict error bodies no longer miscount a 429 as a failure, the single-video
  mm_kwargs budget is probed once, the SSE error code is redacted then cut). Coordinator
  ruling applied: the ffmpeg pin is now a **versioned** URL, re-downloaded (40 MB) and
  confirmed to serve exactly the recorded sha256, with the alias and the `old-releases/`
  destination as fallbacks; the manifest mirrors `FFMPEG_PIN` (new sha256
  `232bad7e…0fe3`) and a test holds both. 27 tests pass under pytest from the repo root
  with an empty `CORPUS_CACHE` and sockets blocked (`NETWORK ATTEMPTS: []`, 0 skipped)
  and as three plain scripts; `validate` 0 errors; `verify` re-hashed 72 of 72 cached
  files with 0 mismatches; the seeded 40-request dry run reproduces bit-for-bit and
  leaks nothing across 11 output files. The only network request this session was the
  ffmpeg tarball GET; no live, GPU, cloud, paid or deployment operation was performed.
  Status is **implemented**, never integrated.
- 2026-09-20 (appended by round 4, `43006d1`/`b8960f8`; nothing above is rewritten):
  claims in this report are superseded, see [`E1-b8960f8.md`](E1-b8960f8.md).
  (1) "reduces every URL of any scheme in any case to `scheme://host/path`" was
  **disproved**: review round 4 reproduced five bypasses of that regex — a query
  containing `'` or `\`, a userinfo with `'` or over 300 characters, a path over 2000
  characters, a scheme-less or escaped-slash echo, and a path-embedded token — plus a
  prefix-only key rule that let an echoed key BODY (`key[9:]`) through whole. Measured on
  the round-4 matrix, `bench.py@73c210c` leaked in 14 of 176 cells and in 4 of 5
  adversarial URL shapes. The design is no longer a filter: the client allowlists what it
  emits (a URL label rebuilt from `urlsplit` parts, no verbatim server strings, no
  exception text, muted library logging, refusal when argv carries the key), and
  `redact()` is defense in depth only. (2) The "class swept / 0 leaking cells" result
  holds only for the 15 outcomes that round tested; the round-4 matrix adds 7 more.
  (3) `manifest.json` is no longer sha256 `232bad7e…0fe3`: `b8960f8` added
  `display_width`/`display_height` per clip, giving
  `386a2d89c26c1bbff4df03004239b3153651901ffcda76f36c3ce5fc23095e18`, 80,773 bytes.
  (4) The "27 tests / 0 skipped" and "row schema" statements are superseded by 35 tests
  and a changed raw-row schema (`error_message` removed; `error_type`, `body_sha256`,
  `body_bytes`, `upload_status` added; `retry_after` numeric; summary gains
  `interrupted`). The versioned ffmpeg pin recorded here is unchanged and still verified.
