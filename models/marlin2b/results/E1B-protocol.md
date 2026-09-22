# E1B benchmark protocol — predeclared before any tuning

Task E1B (`research/plan/18-marlin-backend-first.md` §E1B). This file is the
**pre-registration** of the Marlin-2B baseline measurement: the workload, the seed,
the reference path, the run matrix and the acceptance criteria are fixed **here**,
before a number is measured, so that tuning cannot move the goalposts afterwards.

Rules for this file:

- A criterion may be **replaced by a measured envelope**, and that replacement is
  recorded in the verification log below with the run that justified it. It may not
  be quietly relaxed, and a provisional row may not be quoted without its label.
- Nothing in here is a target, an SLO or a commitment. No workload owner has
  supplied targets (**P-18**), and `research/workloads/marlin-sop.md` §5.2 says so
  explicitly.
- The measurement slices are **not runnable yet**: they need the allocated GPU
  target (**P-04**), W3's pinned serving version and I2B's deployed endpoint. Every
  such slice is marked *pending* here, never skipped.

## 1. Frozen workload profile

| Axis | Value | Where it comes from |
|---|---|---|
| Performance corpus | `models/marlin2b/corpus/manifest.json`, `--subset full` — 64 distinct clips, 4 CC-BY/public-domain sources, 16 geometries, 2–112 s, 16 prompts, plus 4 corrupt-media negatives | E1; `marlin-sop.md` §5.2 "Representative workload" |
| SOP journey fixture | `models/marlin2b/corpus-synth/manifest.json` (`sop-synth-v1`) — 12 generated clips, 8/30/60/120 s (120.0 s is both the API cap and the real 240-frame worst case), 55 scripted steps, 1 declared-absent step, one ≤1 s pair, one boundary-spanning step, one non-canonical pair | `marlin-sop.md` §3.8 |
| Media forms | `video_b64` and `video_url` now; `infrx-upload:upl_…` once the upload routes exist (R61(1)); `text` slots for the no-media control | `marlin-sop.md` §3.3 |
| Preprocessing profile | `v1`: `frames = clamp(round(2.0 × duration_s), 4, 240)` rounded up to even, `size.longest_edge = frames × 200,704` as a whole-clip budget, `shortest_edge = 4096` | `marlin-sop.md` §1.5 |
| Output lengths | declared distribution, `--max-tokens 128,512,1024` | E1B.a; a single ceiling hides the decode cost |
| Tenants | ≥2, one API key each via `--tenant-keys` (names only) | PERF-ENVELOPE "mixed tenants" |
| Arrival | open-loop Poisson (`--rate`) **and** fixed concurrency, both reported, with `schedule_lag_s`; bursts via `--burst` | `marlin-sop.md` §5.2 "Arrival model" (**method — keep**) |
| Seed | **`--seed 20260922`** for every cell unless a cell's purpose is a different draw | fixed here so the arrival times, clip order, form assignment and item keys are reproducible |
| Dataset identity | `--dataset-version e1b-2026-09-22`, `--profile-version v1`; `Idempotency-Key = sop1.<item_key>` per item | `marlin-sop.md` §3.1 |
| Reference path | `models/marlin2b/reference.py` (`transformers`) on the same clips at the same profile, for caption-**event** parity only | `marlin-sop.md` §5.2 "Quality/parity criterion" |

## 2. Target of record, and what it may not be taken on

Coordinator inventory, 2026-09-22 (read-only, no run):

| Fact | Consequence for this protocol |
|---|---|
| The deployed gateway is the **pre-refactor monolith**: no `/metrics`, no `/readyz`, instrumentation is `usage.jsonl` only (`ttft_s`, `wall_s`, prompt/completion tokens, `video_seconds`, `status`, `stream`) | **The baseline is not taken on the monolith.** Phase timing comes from the refactored gateway's `Server-Timing` headers and usage resource, which **I2B** deploys. Until then `bench.py --report` prints every declared phase as `declared_missing`, which is the honest state, not a gap to fill by arithmetic. |
| The engine is **warm and idle with primed caches** (prefix-cache hit 74.6 %, MM-cache hit 88.2 % from earlier single-clip runs) | A cold/warm claim requires **either** an engine restart immediately before the cell **or** clips the target has never seen. `bench.py --engine-state {restarted,warm,unknown}` records which, and the report **flags** a cold/warm split taken at anything but `restarted` as unsupported — a flag, not a refusal: the client prints the cell and names the limit, and it is this protocol that forbids publishing it. A resumed run reports `cold` as unknown outright, because the interrupted run already warmed the target. Every cell uses distinct corpus clips, never a repeated single clip. |
| Engine flags `--max-num-seqs 32`, `--max-model-len 32768`; **no RepoDigest recorded** | The concurrency sweep stops at 32 because past it the engine queues rather than batches, and that is an engine limit, not a measurement. The serving-version pin is **W3's**; this protocol records the digest W3 supplies and does not invent one. |
| **Hybrid attention: 6 of 24 layers are full attention** | Any KV-capacity or context-headroom arithmetic must use the hybrid layer mix. A dense-layer estimate overstates KV cost by roughly fourfold (`est.`, from the 6-of-24 ratio alone) and is not admissible in this protocol. |
| 1× **L40S, 46,068 MiB = 45.0 GiB**; 39.9 GiB resident while the engine is up and idle; a single GPU is a single point of failure | **Idle residency says nothing about KV capacity.** With `--gpu-memory-utilization 0.90` vLLM pre-allocates the KV pool at start-up, so the resident figure already contains it and the remaining headroom is not a KV budget. KV capacity for this protocol is the engine-reported **`num_gpu_blocks` × block size**, read from the engine's own start-up log, and **W3** supplies it with the serving-version pin. No KV or context-headroom number is computed here. Recovery is **measured and published**; no availability target is stated and single-GPU process recovery is never called high availability (P-16). |
| The direct-engine path does not depend on the gateway | Slice E1B.b's direct-engine measurement can be prepared and run **before** I2B; the paired gateway measurement waits for it, and the pair must use identical input/processor semantics or it is not a pair. |

## 3. Run matrix

Every cell writes one summary line to `models/marlin2b/results/bench.jsonl` and one
raw line per attempt to `results/raw/<run>.jsonl`; `bench.py --report` builds the
sweep table. Cells are comparable only within one workload profile — the report says
so when they are not.

| Cell | Command shape | Purpose | Status |
|---|---|---|---|
| L0 latency floor | `--target direct -c 1 -n 12 --subset full` | single-stream floor per clip class | **pending P-04 / W3** |
| L1 concurrency sweep | `--target direct -c 1,2,4,8,16,32` (one run each) | batching curve to the engine's own `--max-num-seqs` | **pending P-04 / W3** |
| L2 open-loop rate sweep | `--target gateway --rate r --requests ≥ 20×r` for r on a doubling ladder until rejects or queue growth appear | sustainable arrival rate, with `schedule_lag_s` | **pending I2B** |
| L3 burst | `--rate r --burst 8` at the sustainable r | admission behaviour under lumpy arrivals (429 + `Retry-After`, `MAX_PREPARING_JOBS`) | **pending I2B** |
| L4 paired direct/gateway | L1's best cell run against both targets, identical clips, profile and prompts | the gateway's own overhead, per phase | **pending I2B** |
| L5 cancellation | `--cancel-fraction 0.2 --cancel-after 2` | client disconnect is billable with authoritative usage (R21) and must not be counted as accepted or failed | **pending I2B** |
| L6 resume | a run interrupted mid-sweep, then `--resume <raw>` | MARLIN-SOP: no second accepted item or charge | **implemented against a fake gateway**; pending a real endpoint |
| L7 soak | the sustainable rate, ≥4 h, `--sample-interval 30`, plus one induced restart | flat RSS/GPU memory, no queue growth, bounded preparation disk | **pending P-04**; 4 h is a ⚠️ proposed engineering floor, not an availability decision |
| L8 parity | `reference.py` against the served engine on the same clips | caption-**event** parity (spans and their order), not accuracy | **pending P-04** |

Sample sufficiency is enforced by the client, not by the reader: a reported pN needs
≥3 accepted samples beyond it (p50 ≥6, p95 ≥60, p99 ≥300), so L2's cells are sized
from the tail they intend to quote, and an unsupported tail prints `—`.

## 4. Provisional acceptance criteria (P-18) — quoted with their labels

From `research/workloads/marlin-sop.md` §5.2, which states: "No workload owner has
supplied targets. Everything here is **provisional engineering criteria, labelled as
such, derived only from this repository's own pinned limits and its single L40S
measurement**. None of it is a commitment, an SLO, or a target to tune toward."

| Axis | Provisional criterion | Status |
|---|---|---|
| Sample sufficiency | a reported pN needs ≥3 accepted samples beyond it: p50 ≥6, p95 ≥60, p99 ≥300 | **method, not a target** — keep |
| Arrival model | open-loop Poisson **and** fixed concurrency, both reported, with `schedule_lag_s` | **method** — keep |
| Denominators | accepted / rejected / failed counted at attempt level; a retry may not hide a 429; a 200 without `[DONE]`/`finish_reason`/usage is `failed` | **method** — keep |
| Baseline to beat | the four committed L40S rows, read as p50-grade: TTFT p50 0.767 s @conc 1 and 3.352 s @conc 8 (1080p, 2,061 prompt tokens); 1.569 req/s @conc 8; TPOT p50 6–8 ms | **`meas.` 2026-09-19, two clips only — not an envelope** |
| Error rate | **provisional:** <1 % platform-caused failures (5xx, `platform_error`, `engine_error`, `lost_after_publication`) over a sweep, with rejections reported separately and not counted as failures | **provisional (P-18)** |
| Latency | **provisional: no criterion.** The only measured tail is p50-grade on one GPU with two clips. E1B must **measure** p95/p99 on the target before any latency number is written down | **explicitly absent** |
| Throughput | **provisional:** report successful **video-seconds processed per second** together with the clip/frame/output profile; do not quote clips/s without the duration mix. One GPU-hour processed 15.9–36.2 video-hours on L40S (`meas.`, corrected 2026-09-20) | **provisional (P-18)** |
| Cost | **provisional:** report cost per successful video-hour at the measured envelope. The committed sketch is $0.06 (360p) – $0.14 (1080p) per video-hour at an **operational** rate of ≈ $2.24/h for the `g6e.2xlarge` dev box. ⚠️ **That rate is an operational figure from `HANDOFF.md:24`, not a priced row: it is not in [`cloud-pricing.md`](../../../research/cross-cutting/cloud-pricing.md)**, which carries L40S rows for other vendors (OCI `BM.GPU.L40S.4` $3.50, and $1.09-$1.57 single-card rows) but **no AWS `g6e` row at all**. Repository convention is that prices come only from `cloud-pricing.md`, so publishing a cost figure requires a sourced row being added there first — owner: whoever publishes it; E1B does not edit that file | **provisional (P-18)** |
| Resources | **provisional:** flat host RSS and flat GPU memory over a soak; no growth in queue depth at steady arrival rate; preparation disk bounded | **provisional (P-18)** |
| Soak duration | **⚠️ TO BE VERIFIED — no owner input.** A provisional engineering floor of 4 h continuous at the sustainable rate plus one induced restart, so E1B/I3B have something to execute; the real duration is an availability decision | **provisional (P-18)** |
| Availability / recovery | **⚠️ TO BE VERIFIED — no owner input.** Measure and publish the recovery window; **do not state an availability target** and do not call single-GPU process recovery high availability | **absent by decision** |
| Quality / parity | caption **events** (the `<start - end>` spans and their order) must match the `transformers` reference path on the same clips at the same profile; scene prose may differ in wording. This is *parity*, not accuracy | **provisional (P-18)**, the only quality gate available without P-07 |

Two hard rules, restated because they are the reason this file exists: **do not
promote a provisional row to a target by quoting it without its label**, and **do not
tune against a row whose p95 was never measured**.

`CREDIT is not USD`, and token fields never carry credit quantities: tokens are
reconciled from `usage`, credits from the usage resource.

## 5. What invalidates a result

A cell that shows any of these is reported as invalid rather than published:

1. a repeated single clip, or a warm cache with `engine_state` not `restarted`, behind
   a cold/warm claim (the report flags this; it does not and cannot refuse to print the
   cell);
2. an undersampled tail quoted anyway (the client suppresses it; a reader must not
   substitute the maximum);
3. hidden rejections — a retry that absorbs a 429, or rejects left out of the
   denominators;
4. queue growth or rising host/GPU memory over the cell;
5. a changed preprocessing profile, prompt set, seed or `--max-tokens` mix between the
   cells being compared (the report prints the profile fingerprint per cell);
6. an accuracy claim of any kind, on the licensed corpus or on `sop-synth-v1`
   (**P-07**);
7. a phase timing inferred from the wall clock rather than published by the target;
8. a cost-per-video-hour figure quoted without the ⚠️ above, i.e. as though ≈ $2.24/h were a
   priced row from `cloud-pricing.md`.

## 6. Pending inputs

| Id | What is missing | Blocks |
|---|---|---|
| **P-04** | allocated GPU target, artifact access and deploy owner | every measured cell in §3 |
| **P-18** | workload owner's latency/throughput/error/quality/cost constraints, soak duration and availability expectations | promoting any §4 row from provisional to a target |
| **P-07** | SOP rubric, event schema, temporal tolerance, ground truth, dataset rights, split, thresholds | any accuracy claim; parity (§4) is the only available quality gate |
| **sourced `g6e` price row** | a sourced AWS `g6e.2xlarge` / L40S row in `research/cross-cutting/cloud-pricing.md`. The ≈ $2.24/h this repository quotes is an operational figure from `HANDOFF.md:24`, and `cloud-pricing.md` has no AWS `g6e` row at all | **publishing any cost-per-video-hour figure.** E1B may measure and report the cost *sketch* with the ⚠️ label above; it may not publish a cost number until the row exists. Owner: whoever publishes it — neither S2M nor E1B edits `cloud-pricing.md` |
| W3 | pinned serving version: runtime image digest, both EOS ids (`[248044, 248046]`), profile-v1 flags, and the engine-reported `num_gpu_blocks` × block size from its start-up log | L0–L4, calling the engine version "pinned" at all, and any KV-capacity statement |
| I2B | the refactored gateway deployed, with `Server-Timing` phases and the usage resource | L2–L5 and all phase timing |

## Verification log

- 2026-09-22 (E1B.a–c): Protocol predeclared before any measurement. The workload,
  seed, dataset identity, run matrix and criteria above were fixed with **no GPU run,
  no cloud operation and no paid call**; the only numbers quoted are the four
  committed L40S rows of 2026-09-19 (two clips, p50-grade) and the coordinator's
  read-only inventory of the current box, both labelled as such. Nothing here has
  been measured on the target.
