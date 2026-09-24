# INTAKE-DRAIN — a mid-body refusal is read by the caller (track G)

## Task and status

| Field | Value |
|---|---|
| Task | **INTAKE-DRAIN** (G track, the gateway intake `apps/infrx-api/infrx/gateway/routes/intake.py`), coordinator brief 2026-09-24 after the E4B box certification run2 |
| Owner / session | implementation agent (Claude Opus 5.5, 1M context) |
| Status | **implemented and tested locally** (uvicorn on loopback, fakes). Not integrated; nothing ran against the box, AWS or hosted Supabase. Not yet validated by a certification overload cell |
| Oracles | PERF-ENVELOPE (E4B overload cell: every overload refusal a read `429` + `Retry-After`, no 5xx, no dropped connection), MEDIA-SEC (the intake stays bounded) |

## Source

| Field | Value |
|---|---|
| Base SHA | `4db74b6` (the integration head, `claude/backend-impl`) |
| Code head | `71fd69e` (`4fa3f4a` implementation, `71fd69e` tests + mutants); this report and the runbook row are committed on top (docs only) |
| Branch / worktree | `codex/intake-drain` / `.claude/worktrees/codex-intake-drain` |
| Integrated SHA | none (coordinator) |

## Box facts (the finding)

Run: `models/marlin2b/results/E4B-box-4226315/run2-20260924T172244Z/work/overload-raw.jsonl`
(untracked on `claude/backend-impl`'s worktree at hand-off time), stage `e4b.b.overload`,
2026-09-24T18:31:25Z, `base_url` `http://127.0.0.1:8001/v1` (direct to the gateway, no Caddy),
32 `video_b64` requests from one key, bodies 0.3-3 MB, httpx over HTTP/1.1.

| Client row (`overload-raw.jsonl`, 32 attempts) | Count |
|---|---|
| `200` | 8 |
| `429 capacity_exhausted`, `retry_after` 2.0 (the large-body gate, `LargeBody.account`) | 4 |
| `429 capacity_exhausted`, `retry_after` 5.0 (job capacity, after the body was read) | 6 |
| no status, `error_class` `ReadError` | **14** |

The gateway's own journal, as reported by the coordinator (not re-read here): 24 × `429`,
9 × `200`. 24 − 10 = 14 refusals the client never read, which is the ReadError count.
(The client has 8 × `200`; the journal's 9 is the coordinator's figure, not reconciled here.)

Cause: `CLOSE_CODES` answers with `Connection: close`. For a refusal raised before the
body was read (the gate claims its slot from the declared `Content-Length` before any
byte; `invalid_api_key` is decided from headers), uvicorn closes a socket with body bytes
still unread; the kernel then sends an RST, and the client's pending writes and reads
fail. The Retry-After 5 refusals come after the whole body was read, so they closed
cleanly - which fits all 14 lost answers being gate refusals (Retry-After 2).

### The same signature at ordinary rates, through the public edge

The coordinator added this after the brief. E1B L2 ladder at release 4226315, through the
public edge (Caddy → gateway). Rows: `models/marlin2b/results/E1B-box-4226315/raw/L2-r0.5.jsonl`
and `L2-r1.0.jsonl`. At this report's writing they are untracked in the `claude/backend-impl`
worktree; commit `edde88c` carries only the coordinator's session note on them. Counted
here from those files:

| Cell | Attempts | `200` | `400` | no status, `ReadError` | ReadError rows |
|---|---|---|---|---|---|
| r = 0.5/s | 120 | 113 | 6 | 1 | c045 `video_b64` |
| r = 1.0/s | 120 | 111 | 6 | 3 | c042, c044, c045 `video_b64` |

The client recorded no 429s in either cell, and every lost answer is a `video_b64` body
on one of the larger clips. So the loss is not limited to the 32-request burst: at
≥ 0.5 clips/s, about 1-3 % of honest `video_b64` requests fail this way until the drain
is deployed. Whether the edge relays the drained 429 unchanged has not been measured
(⚠️ TO BE MEASURED by the next E1B/E4B run through Caddy). The gateway now reads the
declared body before closing, so Caddy's upstream write completes before the response.

## What changed

| Item | Commit | Case | Mutant (death line) |
|---|---|---|---|
| 1 bounded drain in `guard` | `4fa3f4a` | `test_intake_drain.py::test_media_sec__a_drained_refusal_still_closes_the_connection`, `…__a_refused_video_body_is_drained_so_the_client_reads_the_429`, `…__an_unauthenticated_caller_is_drained_only_up_to_1_mib` | `refusal_not_drained`: `[killed] 2 failed, 1 passed, 3 deselected in 1.65s` |
| 1 declared length over the cap is not drained | `4fa3f4a` | `…__a_declared_length_over_the_cap_is_not_drained` | `drain_ignores_the_cap`: `[killed] 1 failed, 5 deselected in 4.28s` |
| 1 chunked body is not drained | `4fa3f4a` | `…__a_chunked_refusal_is_answered_and_closed_without_a_drain` | `chunked_body_drained`: `[killed] 1 failed, 5 deselected in 4.24s` |
| 1 drain ends with the intake window | `4fa3f4a` | `…__a_drain_ends_with_the_intake_window` | `drain_ignores_the_intake_window`: `[killed] 1 failed, 5 deselected in 4.24s` |
| 1 a 401 drains at most 1 MiB | `4fa3f4a` | `…__an_unauthenticated_caller_is_drained_only_up_to_1_mib` | `drain_ignores_the_unauthenticated_bound`: `[killed] 1 failed, 5 deselected in 4.29s` |
| 1 `Connection: close` kept after a drain | `4fa3f4a` | `…__a_drained_refusal_still_closes_the_connection` added to the existing mutant's cases | `refusal_keeps_the_socket`: `[killed] 2 failed, 26 deselected in 4.36s` |
| 2 real-socket harness + list entries | `71fd69e` | the six cases above (`tests/g/test_intake_drain.py`) | five new mutants in `tests/g/mutants.py` |

How the drain works (`intake.py`):

* `guard(mint_request_id, limits=None)` now takes the pilot settings. The ingress, the
  jobs router and the upload routes pass `rt.settings.pilot` (one line each in
  `ingress.py`, `jobs.py`, `uploads.py`). The guard wraps the ASGI `receive` (`watched`)
  so it knows whether the body was read to its end. This has to be the receive and not
  Starlette's `_stream_consumed`: `jobs._as_async` builds a second `Request`.
* When a `CLOSE_CODES` refusal happens before the end of the body, `drain` reads and
  discards the rest, but only if `Content-Length` is declared and ≤ `max_request_bytes`
  (≤ `min(1 MiB, max_request_bytes)` for `invalid_api_key`). It counts chunks and never
  keeps them. It stops at the declared length, at the first exception (a disconnect
  included), and at `started + intake_timeout_s`, where `started` is when the guard
  received the request. So a drain never makes a refusal later than `INTAKE_TIMEOUT_S`,
  and a `deadline_exceeded` refusal (whose window is already spent) effectively does not
  drain. After that, the typed response goes out with `Connection: close` as before.
* A chunked body, a declared length over the bound, or a window that runs out gets
  today's behaviour: answer, close, no drain. The comment on `CLOSE_CODES` and the
  `drain` docstring say why (an endless chunked body has no end we can promise to reach).
* The declared-length parse moved into `declared_length()` so the slot claim and the
  drain share one parser.

Re-anchored (text moved into `declared_length`), each re-run and killed:
`declared_length_ignored_for_slots` `[killed] 1 failed, 64 deselected in 1.02s`,
`content_length_trusted_blindly` `[killed] 1 failed, 64 deselected in 1.01s`,
`content_length_non_ascii_digits` `[killed] 1 failed, 64 deselected in 1.03s`; also
re-run: `close_only_on_401` `[killed] 1 failed, 20 deselected in 0.94s`.

Changed existing case: `test_structure.py::test_media_sec__a_declared_large_body_claims_its_slot_before_it_is_read`
asserted "no read at all" for a refused declared-large body. The invariant it protects is
"never buffered". It now asserts that no read happens before the slot is refused
(`0 not in read`) and that the drain stops at the declared 9,000 bytes (`len(read) <= 2`).
`declared_length_ignored_for_slots` still kills it.

### The real-socket harness

`tests/g/test_intake_drain.py` serves `support.cutover_app` under `uvicorn.Server` in a
thread, on a loopback socket bound to port 0. It uses the deploy unit's server with the
same defaults: h11, because httptools is not installed. The gate refuses every large
body (`LargeBodies(limit=0)`), which gives the box's 429 with Retry-After 2. The listening
socket's `SO_RCVBUF` is pinned to 64 KiB, so a 3 MB body is still in flight when the gate
answers, as it would be across a network or on a loaded box. The body is a 3 MB
`video_b64`-shaped chat request.

* httpx (the certification's client) makes 4 refusals in a row. Each is read as `429`
  with `retry-after: 2`, `connection: close` and `error.code = capacity_exhausted`. A
  `TransportError` is turned into an assertion ("the 429 never reached the client").
* The raw client stops at its first socket error, as the box's client in effect did. It
  reads the 429 and the close for a drained body, and it reads the answer promptly
  (< 3 s client timeout, window 30 s) for a chunked body or an over-cap declared length,
  where there is no drain. A 3 MB body that stalls after 4 KiB with a 1 s window gets its
  429 after the window (0.5 s < t < 3 s). A 500 KB unauthenticated body is drained and
  its 401 read. A 3 MB unauthenticated body is answered at once with no drain.

The same cases with the drain removed (`refusal_not_drained`), three runs by hand: the
raw drained-refusal and unauthenticated cases fail every time (`ConnectionResetError` on
the send, no status read). The httpx case failed once (`RemoteProtocolError`, first
mutant run before the `SO_RCVBUF` pin) and passed in the three later runs. On this host,
httpcore reads the response after a `WriteError`, and the queued 429 survives the RST.
So the httpx case is the acceptance check for the box's client, and the raw cases are
what kill the mutant.

## Runs (exact tails)

| Run | Where | Tail |
|---|---|---|
| `tests/g` whole | code head `71fd69e` | `581 passed, 2 warnings in 143.43s (0:02:23)` |
| `tests/g/test_intake_drain.py` | `71fd69e` | `6 passed in 2.64s` |
| G list, whole (`INFRX_MUTANTS=all pytest -q tests/g/test_mutants.py`) | `71fd69e` | `340 passed in 834.36s (0:13:54)` (every mutant killed, list well-formed, every case covered) |
| targeted mutants (above) | `71fd69e` | all 10 `killed` |

Contracts quick: not run. The error envelope did not change (same codes, statuses,
headers and body; the drain only adds reading before the answer).

## Slot sizing (deliverable 3, a deployment setting; the code default stays 2)

Memory model, meas. local (tracemalloc through `read_body` → `decode_utf8` →
`check_structure` → `parse_object`, one `video_b64` body): 3 MiB body → 9 MiB peak
(3.00x), 0.01 s; 95 MiB body → 285 MiB peak (3.00x), 0.45 s. A body is held three times
over: raw bytes, decoded text, and the parsed string. The recommendation is in
`infra/runbooks/rollout.md` §1, as a row `LARGE_BODY_LIMIT` = `8` plus `LARGE_BODY_LIMIT=8`
in `INSTALL_ARGS`' `INFRX_SET`, which `apply --set` applies:

* worst case 8 × 96 MiB × 3 = 2,304 MiB, against the gateway container's 8 GiB;
* worst-case loop stall of about 8 × 0.45 s = 3.6 s (the class docstring's older "eight
  concurrent 95 MiB bodies, 30 s" was measured before the pre-parse structure checks; not
  re-measured here);
* the pilot's clips (0.3-3 MB): 8 × 3 MB × 3 = 72 MiB;
* 8 = `ENGINE_MAX_NUM_SEQS` = `WORKER_CONCURRENCY`, so a burst is refused by job capacity
  (after the read, closing cleanly) rather than by the intake gate.

⚠️ TO BE MEASURED: the next certification's overload cell validates the value: every
refusal a read 429 with Retry-After, zero ReadError, and gateway RSS under the bound.

## Ruling proposal (unnumbered; next free R108)

> **A refusal that closes the connection drains a declared body first (INTAKE-DRAIN
> proposal).** When the gateway refuses a request with a code that closes the connection
> (`invalid_api_key`, `request_too_large`, `capacity_exhausted`, `deadline_exceeded`)
> before its body has been read to its end, it reads and discards the rest only if
> the request declares a `Content-Length` no larger than `MAX_REQUEST_BYTES` (no larger
> than 1 MiB for `invalid_api_key`: no tenant is known, so an anonymous caller can never
> make the gateway read a video), and only within the `INTAKE_TIMEOUT_S` window that
> began when the request arrived. Then it answers with the typed envelope and
> `Connection: close`. A chunked body, a declared length over the bound, or a window that
> ends first is answered and closed without draining; such a caller may not read the
> refusal, so a client that must read its refusals declares the length. Nothing drained
> is kept or parsed. Overload refusals stay `429` with `Retry-After` and an overload
> code; the connection is never reused after one.

## Limits

* Chunked bodies, declared lengths over the bound, and unauthenticated bodies over 1 MiB
  still close with bytes unread, by design. Those callers can still lose the refusal to
  an RST.
* A body that arrives slower than the window allows (e.g. 96 MiB at 10 Mbit/s against
  30 s) is cut at the window, closed, and may lose its refusal the same way.
* The upload routes' own `closing` wrapper (G4U) adds `Connection: close` to every
  answer ≥ 400. Only the `CLOSE_CODES` refusals drain; the pre-read `403`/`404`/`415`
  upload refusals close with the body unread, as before.
* A `CLOSE_CODES` refusal of a request sent with `Expect: 100-continue` makes uvicorn send
  `100 Continue` when the drain starts reading. The caller then sends its body, which is
  drained (bounded). This is wasteful but correct. Not special-cased.
* Non-`CLOSE_CODES` refusals raised before the read (e.g. the `400` content-type check)
  keep the connection. uvicorn's h11 then discards the rest of the body itself with no
  bound. This existed before and is unchanged; see follow-ups.
* On this host, httpx recovers the 429 after an RST most of the time, so the httpx case
  alone would not have caught the defect. The raw cases do.

## Requests / follow-ups

1. Coordinator: merge `codex/intake-drain`, number the ruling (R108), and apply
   `LARGE_BODY_LIMIT=8` at the next install (row and `INSTALL_ARGS` updated in
   `infra/runbooks/rollout.md`).
2. E track: the next certification's overload cell is the acceptance for both the drain
   and the slot value (the ⚠️ above). The E1B L2 ladder through the edge (r = 0.5 and
   1.0/s) is the acceptance at ordinary rates: zero `ReadError` on `video_b64`.
3. G follow-up (not started, time-boxed out): decide whether pre-read non-`CLOSE_CODES`
   refusals (`400` content type, the upload wrapper's `403`/`404`/`415`) should close
   and drain the same way, rather than being left to uvicorn's unbounded discard or an
   undrained close.

## Verification log

- 2026-09-24: INTAKE-DRAIN implemented at `4fa3f4a`/`71fd69e` on base `4db74b6`; runs as
  listed above; nothing contacted the box, AWS or hosted Supabase; no containers were used.
