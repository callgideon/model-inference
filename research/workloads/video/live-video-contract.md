# Live-video workload and trial contract — discovery draft (X1)

**Task X1**, lane `discovery`, wave LW1, 2026-09-27. Slices X1.a–c of
[14-expansion-gates.md](../../plan/14-expansion-gates.md) §X1. Oracle **VIDEO-CONTRACT**
([04-verification.md](../../plan/04-verification.md):113). Pending input **P-13**
([15-pending-inputs.md](../../plan/15-pending-inputs.md):23).

**Manifest status:** planned
**Trial status:** BLOCKED: P-13 — no workload owner has supplied the live-input task, its deadline, clock source, overload policy or causal labelling rule (§4). Discovery only; X2 may not start.

Conventions: [`research/METHODOLOGY.md`](../../METHODOLOGY.md) — `meas.` is a committed
measurement with a source, `est.` is derived, **⚠️ TO BE VERIFIED** names the method that
closes it. **No GPU run, cloud operation, purchase or code change was made for this
document.** It is not an approved trial contract: approval needs a real workload owner and
measured requirements ([tasks.json](../../plan/tasks.json) X1 `acceptance`). Every
acceptance field below is either sourced from this repository or marked
`BLOCKED: P-13 — <what is missing>`; none is invented.

---

## 0. Summary

1. **What exists today is finite-clip inference.** One video part per request, prepared
   before inference, at the frozen profile `v1` (2 fps, 4–240 frames, 200,704 px/frame) and a
   qualified duration cap of **82 s** (`infrx/contracts/limits.py:62`; P-20 decided
   2026-09-23, [15-pending-inputs.md](../../plan/15-pending-inputs.md):84,100). Output SSE is
   output streaming, not live input ([07-api-contracts.md](../../platforms/07-api-contracts.md)
   "Streaming distinctions"; [marlin-sop.md](../marlin-sop.md) §5.4).
2. **A live workload on Marlin-2B is a windowing adapter over that finite path**, not a model
   capability: the model is stateless, so every window re-encodes its overlap and nothing is
   remembered between windows ([05-lab-spec.md](../../platforms/05-lab-spec.md):52).
3. **The arithmetic that sizes such an adapter is fixed by the profile** (§2.3, `est.`), but
   every acceptance number — deadline, freshness, quality, throughput — belongs to P-13 and
   is BLOCKED.
4. **Three candidate tasks** are on record ([08-decisions-and-sources.md](../../platforms/08-decisions-and-sources.md):39):
   periodic description, recent-window QA, SOP event detection. Choosing one is P-13.

---

## 1. X1.a — Workload record

| Item | What is known | What is missing |
|---|---|---|
| Task | Candidates: periodic description, recent-window QA, SOP event detection (08-decisions-and-sources.md:39). The lead product workload is SOP verification over **recorded** robotics video ([03-app-spec.md](../../platforms/03-app-spec.md):11) | BLOCKED: P-13 — the provider-selected live task and its owner |
| Input cadence and cameras | Profile `v1` samples **2 fps** regardless of source rate (`infrx/config.py:55`); one video part per request (`gateway/routes/validate.py:100`) — so one camera per window | BLOCKED: P-13 — source frame rate, resolution, codec and camera count per stream |
| Window and hop | Window length `W` is bounded by the profile: **2 s ≤ W ≤ 82 s** (4-frame minimum at 2 fps, `infrx/media/video.py:111-116`; 82 s cap `limits.py:62`) | BLOCKED: P-13 — the task's `W` and hop `H` |
| Timestamps | Marlin emits `<start - end>` spans in seconds **relative to the clip start** ([marlin-sop.md](../marlin-sop.md) §1.4); no per-frame wall-clock timestamp enters the model | BLOCKED: P-13 — the capture clock source (PTP/NTP/device) and the permitted skew between cameras and the server |
| Retention | Finite path today: result 24 h, processing cache 7 d (`limits.py:102-103`) | BLOCKED: P-13 — frame, window and event retention for live input; content rights stay under P-09 |
| Response shape | Free text with spans; no structured event schema is served ([marlin-sop.md](../marlin-sop.md) §5.3 "Output/event schema") | BLOCKED: P-13 — the event record the consumer's system reads |
| Deployment location | Pilot runs one GPU on one host ([marlin-sop.md](../marlin-sop.md) §5.2 "Availability / recovery") | BLOCKED: P-13 — camera site, network path to the serving host and allowed placement |

---

## 2. X1.b — Causal benchmark definition

### 2.1 Definitions (method, not targets)

- **Window.** For stream start `t0`, window `k` covers capture times `[t_k − W, t_k]` with
  `t_k = t0 + k·H`. It may be submitted only once the frame at `t_k` has **arrived**; a frame
  with capture time `> t_k` never enters window `k` (causality).
- **Latency.** `L_k = t_emit(k) − t_k` = frame transport + window assembly + preparation +
  queue + prefill + decode.
- **Event-to-emit delay.** An event at capture time `e` is first visible in the first window
  with `t_k ≥ e`, so its delay is `(t_k − e) + L_k ∈ [L_k, H + L_k)`.
- **Freshness at emission.** `t_emit − t_k`, reported per window; a window whose latency
  exceeds the deadline is **stale** and is reported as stale, never silently delivered.
- **Loss.** Every window is exactly one of fresh / stale / dropped (overload) / failed; the
  denominators follow the E1 rule that a retry may not hide a rejection
  ([marlin-sop.md](../marlin-sop.md) §5.2 "Denominators").
- **Causal labelling rule.** Ground truth labels an event at its onset in capture time; a
  detection credited to window `k` must have onset `≤ t_k`, and a detection whose span lies
  in the overlap of `k−1` and `k` is one event, not two.

### 2.2 Decisions the owner must make

| Decision | Options discovery can list | Status |
|---|---|---|
| Deadline per event class | — | BLOCKED: P-13 |
| Duplicate-event policy | (a) keep the earliest emission and suppress matches in later overlapping windows; (b) merge spans by IoU ≥ a threshold; (c) emit all with a stable event key and let the consumer dedup | BLOCKED: P-13 |
| Overload policy | (a) drop the oldest queued window of the stream (keeps freshness, loses coverage); (b) reject new windows with an explicit `dropped` record; (c) lower the hop rate per stream. Buffers are bounded in every option | BLOCKED: P-13 |
| Ground truth | per-stream event labels with onset/offset in capture time and who produced them; SOP ground truth is already missing for the finite path ([marlin-sop.md](../marlin-sop.md) §5.3) | BLOCKED: P-13 (and P-07 for SOP tasks) |
| Quality threshold | none exists: there is no accuracy baseline for Marlin-2B at all ([marlin-sop.md](../marlin-sop.md) §5.3 "Baseline") | BLOCKED: P-13 |
| Throughput threshold | streams per GPU at the chosen `W`, `H`, resolution and output length | BLOCKED: P-13 |

### 2.3 Sizing arithmetic fixed by the profile (`est.`)

At profile `v1` one second of video is 2 frames = one temporal patch = **196 video tokens**
(`tokens = total_pixels ÷ 2048`, [marlin-sop.md](../marlin-sop.md) §1.5); the measured 10.1 s
clip gave grid `[10,28,28]` = 1,960 video tokens (`meas.` 2026-09-19,
`models/marlin2b/results/bench.jsonl` row 1).

| Quantity | Formula | Example (`est.`) |
|---|---|---|
| Video tokens per window | `196 · W` | W = 10 s → 1,960 |
| Re-encoding factor (stateless overlap) | `W / H` | W = 10 s, H = 2 s → 5× the tokens of the raw stream |
| Prefill tokens per stream-second | `196 · W / H` | 980 tokens/s for W = 10, H = 2 |
| Windows per second per stream | `1 / H` | 0.5 |
| Streams per GPU | `R_window / (1/H)` where `R_window` is the measured sustainable window rate | from the only measurement, 1.569 req/s for 10.1 s 1080p clips at concurrency 8 on 1× L40S (`meas.`, `bench.jsonl`), ≈ 0.8 streams at H = 2 s or ≈ 15.7 at H = 10 s |
| Latency floor | one request at concurrency 1 took ≈ 2.0 s end to end (1 ÷ 0.501 req/s, TTFT p50 0.767 s, ~200 output tokens) (`meas.`, same source) | event-to-emit delay ∈ [≈2.0 s, H + ≈2.0 s) at concurrency 1 |

These rows are **p50-grade, one GPU, two clips sent repeatedly** (so vLLM's multimodal cache
may have absorbed cost) — [marlin-sop.md](../marlin-sop.md) §1.6. They size the problem;
they are not an envelope and are not a target. ⚠️ **TO BE VERIFIED:** sustained window rate
with distinct, non-repeating windows at the chosen `W`/`H`; method: replay the §5 fixture
through `models/marlin2b/bench.py` with `--rate` at the owner's `H` and report fresh/stale/
dropped per window.

---

## 3. X1.c — Serving, transport and capacity

### 3.1 Serving and preprocessor capability (sourced)

- Engine: vLLM with `--limit-mm-per-prompt video:1`, `--max-model-len 32768`
  ([marlin-sop.md](../marlin-sop.md) §2.5); qualified clip cap 82 s = the engine's
  16,384-token encoder-cache ceiling (P-20, 15-pending-inputs.md:84).
- Preprocessing identity is `profile_version` on each `MediaRef` (`contracts/records.py`,
  [marlin-sop.md](../marlin-sop.md) §1.5); a live adapter must reuse it unchanged so windows
  keep finite-clip parity (the VIDEO-CAUSAL oracle, 04-verification.md:114).
- No model session or cross-request state exists; windows are independent requests
  ([marlin-sop.md](../marlin-sop.md) §5.4).

### 3.2 Transport

No live-input transport is mounted or specified. Chat messages are the wrong carrier for
windows and deadlines ([07-api-contracts.md](../../platforms/07-api-contracts.md):16). A
dedicated session transport (source protocol in, bounded segmenter, finite-window requests
through the existing durable runtime, events out) is the only shape consistent with the
plan; its protocol depends on what the cameras emit — BLOCKED: P-13.

### 3.3 Allocated capacity

None allocated. The pilot is a single GPU serving the App ([marlin-sop.md](../marlin-sop.md)
§5.2); a live trial needs its own allocation so it cannot starve finite-clip customers —
BLOCKED: P-13 (trial target); fleet capacity remains P-16.

### 3.4 Proposed X2 split (draft — replaced by the approved contract)

Kept inside the parent X2 slices of 14-expansion-gates.md §X2:

1. X2.a1 window assembler: arrival-ordered bounded ring per stream, causal cut at `t_k`, even-frame rounding identical to `budget_kwargs` (`media/video.py:111-116`).
2. X2.a2 overlap dedup per the owner's policy (§2.2) with a stable event key.
3. X2.b1 scheduler class and overload policy through coordinator-owned hooks; explicit fresh/stale/dropped/failed record per window.
4. X2.c1 replay of the §5 fixture with jitter, reorder, gaps, reconnect and clock jumps.
5. X2.c2 allocated-model trial at the owner's `W`/`H`; support is enabled only for the measured envelope.

---

## 4. Contract fields

The VIDEO-CONTRACT failure oracle names deadline, clock alignment, overload policy and causal
labelling rule; X1.a–c name the rest. `check_discovery.py` in this directory enforces that
every row is sourced or BLOCKED.

| Field | Value | Source |
|---|---|---|
| Task | BLOCKED: P-13 — provider-selected live task (one of the §1 candidates or another) and its named owner | — |
| Input cadence and cameras | BLOCKED: P-13 — source fps, resolution, codec and camera count per stream | — |
| Window and hop | BLOCKED: P-13 — the task's `W` and `H` within 2 s ≤ W ≤ 82 s | — |
| Clock alignment | BLOCKED: P-13 — capture clock source and permitted camera↔server skew | — |
| Retention | BLOCKED: P-13 — frame/window/event retention for live input (rights under P-09) | — |
| Response shape | BLOCKED: P-13 — the event record schema the consumer reads | — |
| Deployment location | BLOCKED: P-13 — camera site, network path and allowed serving placement | — |
| Causal labelling rule | Onset in capture time; credited to window `k` only if onset ≤ `t_k`; one event across an overlap (§2.1) | 05-lab-spec.md:52; 07-api-contracts.md "Streaming distinctions" |
| Ground truth | BLOCKED: P-13 — labelled live streams with event onsets in capture time and their producer | — |
| Deadline | BLOCKED: P-13 — end-to-end deadline per event class | — |
| Freshness threshold | BLOCKED: P-13 — maximum `t_emit − t_k` before a window is stale | — |
| Quality threshold | BLOCKED: P-13 — task metric and passing score; no Marlin baseline exists | — |
| Throughput threshold | BLOCKED: P-13 — streams per GPU at the chosen `W`/`H`/resolution/output length | — |
| Duplicate-event policy | BLOCKED: P-13 — choice among §2.2 options (a)–(c) | — |
| Overload policy | BLOCKED: P-13 — choice among §2.2 options (a)–(c) and the buffer bound | — |
| Serving capability | Finite clips only: 1 video part, profile `v1`, 82 s cap, stateless, text output | `limits.py:62`; `validate.py:100`; marlin-sop.md §1.5, §5.4 |
| Transport | BLOCKED: P-13 — the cameras' source protocol that the session transport must ingest | — |
| Allocated capacity | BLOCKED: P-13 — a trial GPU allocation separate from the pilot | — |

---

## 5. Synthetic fixture plan `live-synth-v1` (description; not built)

Permitted immediately after F2P (14-expansion-gates.md §X1). Mirrors `sop-synth-v1`
([marlin-sop.md](../marlin-sop.md) §3.8): provider-owned, generated with the pinned ffmpeg
(colour + tally, no `drawtext`), so no customer content (P-09) is involved.

- One 300 s stream at 10 fps, 640×360, with 12 scripted colour-change events at known
  capture times, three of them inside a 2 s span so overlap dedup is exercised.
- A frame-arrival schedule separate from capture time, with variants: in order; ±150 ms
  jitter; 2 % reordered; a 3 s gap; a 5 s disconnect and reconnect; a +2 s clock jump.
- A label file of event onsets/offsets in capture time for the causal scorer.

It proves adapter mechanics only (causality, dedup, loss accounting). It is **not** accuracy
evidence and not a workload.

---

## 6. What this document does not claim

No native streaming, no live-video support, no stateful session, no accuracy, no latency or
freshness target, and no allocated capacity. Output SSE and finite-clip inference do not
imply any of them (14-expansion-gates.md §X1 "Acceptance").

---

## Audit log

- 2026-09-27 (X1, lane `discovery`, branch `codex/w5-discovery`, base `9a6c3685`): created.
  X1.a–c drafted as discovery. Every acceptance field is BLOCKED on P-13 except the causal
  labelling rule (plan-sourced) and the serving capability (code-sourced). The 82 s cap is
  cited from `limits.py:62` and P-20, superseding the 120 s figure still printed in
  `marlin-sop.md` §1.5/§2.5 (that file is outside this lane; flagged, not edited). Sizing
  rows are `est.` from the single committed L40S measurement. No GPU run, cloud call,
  purchase or code change.
