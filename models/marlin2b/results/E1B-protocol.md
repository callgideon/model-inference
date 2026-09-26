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

Amendment (E1C, 2026-09-24): a hosted cell runs with `--profile <infrx.run-profile/1> --key-inventory <sanitized inventory>`; `bench.py` refuses a non-local target without a runnable profile (the only opt-out is the explicit, logged `--unprofiled smoke` ≤ 4 requests / ≤ 512 output tokens; an opted-out summary is INVALID). Amended 2026-09-25 (E1C profile flip, CERTIFY-WIRING): the `certify` opt-out no longer exists; `certify.py --run-profile <base> --key-inventory <inv>` stamps the base per cell.

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

9. any replay outside a declared resume of the same keys (the bench parser `e1c.1` reports the cell INVALID; E1C, 2026-09-24);
10. a cell without its own `--dataset-version` (cells that share a dataset identity replay each other: the 4226315 L2/L3/L5 cells were INVALID for exactly this — 57/111/105/57 unexpected replays);
11. a P4 overload cell that saw no 429/503, or that did not enter through the public edge (S3 finding F5; `target.path` must be `public-edge`).

## 6. Pending inputs

| Id | What is missing | Blocks |
|---|---|---|
| **P-04** | allocated GPU target, artifact access and deploy owner | every measured cell in §3 |
| **P-18** | workload owner's latency/throughput/error/quality/cost constraints, soak duration and availability expectations | promoting any §4 row from provisional to a target |
| **P-07** | SOP rubric, event schema, temporal tolerance, ground truth, dataset rights, split, thresholds | any accuracy claim; parity (§4) is the only available quality gate |
| **sourced `g6e` price row** (pending input P-19) | a sourced AWS `g6e.2xlarge` / L40S row in `research/cross-cutting/cloud-pricing.md`. The ≈ $2.24/h this repository quotes is an operational figure from `HANDOFF.md:24`, and `cloud-pricing.md` has no AWS `g6e` row at all | **publishing any cost-per-video-hour figure.** E1B may measure and report the cost *sketch* with the ⚠️ label above; it may not publish a cost number until the row exists. Owner: whoever publishes it — neither S2M nor E1B edits `cloud-pricing.md` |
| W3 | pinned serving version: runtime image digest, both EOS ids (`[248044, 248046]`), profile-v1 flags, and the engine-reported `num_gpu_blocks` × block size from its start-up log | L0–L4, calling the engine version "pinned" at all, and any KV-capacity statement |
| I2B | the refactored gateway deployed, with `Server-Timing` phases and the usage resource | L2–L5 and all phase timing |

## 7. Window cells: E1B inside the E4C window (E1B-PREP, 2026-09-26; prepared, not run)

This section adds cells so that **one GPU window, the E4C window**
([E4C-runbook.md](E4C-runbook.md)), also covers E1B. Nothing in it has run. Wall times,
CREDIT ceilings and USD figures are `est.`, each with its basis. The certify criteria quoted
here are E4B-protocol §5 amendment 6 (P-18) as coded in `tests/integration/backend/certify.py`
`CRITERIA`, not new targets. The slice-by-slice map of what the certify run already produces
is in `research/plan/evidence/e/E1B-prep-*.md`.

### 7.1 Rules for the window cells

1. **No overlap.** A window cell never runs while a certify cell, the soak, the journey or a
   drill runs. There is one engine, so any concurrent request is foreign traffic in both
   cells. The canary stays off until the last window cell ends (runbook §1 step 5).
2. **Own profile, own dataset.** Each cell runs under a committed profile base filled with
   the runbook §2 identity (WR-1 in the evidence file), with `keys-certify.json` (H6), or
   `keys-journey.json` for WC-9, and with its own `--dataset-version e1b-w1-<cell>` (§5 items
   9–10). A different rate, form set, tenant count or bound is a new base committed before
   the window (R133), never an edit of a filled copy. Per-level fields are stamped by
   committed code, the way `certify.cell_profile` does it (WR-2), never by hand.
3. **Frozen shape.** Every cell uses `--seed 20260922`, `--max-tokens 128,512,1024`,
   `--retries 0` and `--subset full`, unless its row says otherwise.
4. **Certify is untouched.** No window cell changes a certify criterion, cell or profile. A
   window cell that fails is recorded in E1B's record as FAIL, INVALID or NOT RUN, and has no
   effect on the certify report.
5. **Latency.** E1B's own §4 latency row stays **no criterion**. Gateway cells are reported
   descriptively, beside E4B §5's P-18 numbers, and are not judged against any new number.
6. **Phase timing** comes only from what the target publishes: `Server-Timing` (the gateway
   publishes `prepare` only), the gateway and worker `/metrics`, and the engine's `vllm:*`
   histograms. A phase that nothing publishes stays `declared_missing` (§5 item 7).
7. **CREDIT and USD.** CREDIT figures are ledger units (P-01 card). USD figures are
   infrastructure cost (P-19). They are never converted in either direction.

### 7.2 The cells

`M=models/marlin2b`. `BOX` is the certify bench prefix of runbook §3 (`--corpus $M/corpus/manifest.json --subset full --target gateway --model nemostation/marlin-2b --seed 20260922 --forms video_b64 --max-tokens 128,512,1024 --retries 0 --base-url http://127.0.0.1:8001/v1`). `DIRECT` is the same with `--target direct --model marlin2b --base-url http://127.0.0.1:8000/v1` (the engine's served name, `serve.sh --served-model-name`). Every bench line also takes `--profile <that cell's filled base> --key-inventory <H6 file> --out $O/<cell>.jsonl --raw $O/raw/<cell>.jsonl`. The CREDIT ceiling per request is 13.5168, the runbook §3 projection: 30,720 input tokens at 400 plus 1,024 output tokens at 1,200 CREDIT per 1M.

| Cell | When | Command sketch | Wall (`est.`) | CREDIT ceiling (`est.`) | Oracle |
|---|---|---|---|---|---|
| **WC-0** stage and resource sidecar | Start with runbook §4; stop after the last window cell | `while :; do t=$(date -u +%s); for p in 8000 8001 8002; do curl -s -m 5 127.0.0.1:$p/metrics \| grep -E '^(vllm:(request_(queue\|prefill\|decode\|inference)_time_seconds\|time_to_first_token_seconds\|e2e_request_latency_seconds\|num_requests_(running\|waiting)\|kv_cache_usage_perc\|(prefix\|mm)_cache_(queries\|hits))\|infrx_(phase_seconds\|queue_oldest_wait_seconds\|db_pool_(wait_seconds_total\|requests_total)\|process_resident_bytes\|gpu_(memory_bytes\|utilization_ratio)\|processing_cache_bytes))' \| sed "s/^/$t $p /"; done; sleep 30; done >> $O/scrape.log` and `vmstat -t 30 >> $O/vmstat.log` | 0 extra (runs alongside) | 0 | Each cell's interval (summary `ts` − `wall_s` … `ts`) holds ≥ 2 scrapes. The per-cell histogram delta gives a `sum/count` mean and bucket bounds per engine phase (queue, prefill, decode) and per gateway phase. A cell with fewer than 2 scrapes has no stage split. It is never interpolated. Bucket bounds are not percentiles, and are never quoted as p95 |
| **WC-1** direct concurrency ladder | After runbook §4's report is fetched | `for c in 1 2 4 8; do python $M/bench.py $DIRECT -c $c -n 64 --engine-state warm --dataset-version e1b-w1-L1-c$c …; done` (the ladder stops at the pinned `max_num_seqs` 8 in `E4C-box.base.json`. `measure/concurrency.sh` refuses any engine not at 32, so it is not used) | 6–10 min. Basis: 64 requests per level at the `meas.` 0.322–0.353 / 0.509–0.812 / 0.711–1.301 / 0.888–1.888 req/s for c = 1/2/4/8 (E1B-box-20260923T2155Z L1 and `serving-version.json` `concurrency_sweep`, same image, `--max-num-seqs 32`) | 0 (engine direct, unmetered) | One summary row per level. Failures are only the 4 over-cap clips (`expected_invalid`), with 0 in-cap failures. WC-0 shows peak running ≤ c and waiting 0 for c < 8. Each p95 rests on the 60 in-cap accepted samples, the §3 floor. No level sets a target |
| **WC-2** paired direct legs (L4) | After WC-1 | `for r in 0.5 2.0; do python $M/bench.py $DIRECT --rate $r --requests 135 --engine-state warm --dataset-version e1b-w1-pair-r$r …; done`. 135 is certify's `rung_requests`. The schedule is a pure function of n, clips, forms, seed, rate and output mix, so this replays certify's `envelope-r0.5` and `envelope-r2.0` clip, arrival and budget sequence exactly (checked locally: two dataset versions give identical sequences) | 7–8 min. Basis: last scheduled arrival 276.3 s and 69.1 s (`bench.build_schedule`, local, no GPU), plus a tail of ≤ 60 s each | 0 | Pair on the 126 in-cap items per rung. (a) Media semantics: `prompt_tokens` direct = gateway (certify `work/envelope-r*-raw.jsonl`) per clip, or they differ by one constant across all clips (a text-scaffold offset, which is then named). A clip-dependent difference makes the pair INVALID (§5 item 5). (b) Output: `completion_tokens` equal per item. This needs WR-3 (both EOS ids on the direct leg). Without it the output half is labelled NOT PAIRED. (c) Gateway overhead = gateway − direct TTFT and latency p50, with p95 only at ≥ 60 samples, plus WC-0's phase deltas. (d) The cache regime of each leg comes from WC-0's prefix and MM cache deltas. It is measured, not assumed |
| **WC-3** burst at the supported rate (L3) | After WC-2 | `python $M/bench.py $BOX --rate 0.5 --burst 8 --requests 135 --dataset-version e1b-w1-L3 …` | 8–9 min. Basis: last scheduled arrival 421.9 s, plus the tail | ≤ 1,824.768 | Every refusal is a 429 with `Retry-After` and an overload code (certify's `overload_problems` rule). In-cap failures are < 1 % (E4B §5, P-18). The WC-0 deltas of `infrx_requests_rejected_total` equal bench's `rejected`. Any change to intake, ingress or `LARGE_BODY_LIMIT` since bda1586 is named |
| **WC-4** cancellation under load (L5) | After WC-3 | `python $M/bench.py $BOX --rate 0.5 --requests 60 --cancel-fraction 0.2 --cancel-after 2 --dataset-version e1b-w1-L5 …` | 3–4 min. Basis: 9 scheduled cancels, last arrival 120.4 s | ≤ 811.008 | A cancelled row is neither accepted nor failed. Each cancelled item's debit is its model-reported consumed tokens, or nothing (R21), and it reconciles in `drift.py`. WC-0 shows `vllm:num_requests_running` back to 0 by the first scrape after the last arrival's tail |
| **WC-5** forms (upload and inline; URL when approved) | After WC-4 | `python $M/bench.py $BOX --forms upload,video_b64 --rate 0.5 --requests 135 --dataset-version e1b-w1-forms …`. `video_url` is added with `--media-base-url` only once runbook §5.0 step 4's `MEDIA_BASE_URL` is approved. Until then the URL form is BLOCKED, not skipped | 5–7 min. Basis: last scheduled arrival about 276 s (the r0.5 schedule), plus the upload round trips | ≤ 1,824.768 | `stages_s.upload` is present on each of the 68 upload rows. `prompt_tokens` per clip is equal across forms (same bytes, same frames). In-cap failures are < 1 %. Latency is split by form from the raw rows |
| **WC-8** SOP dataset, interrupt and resume (MARLIN-SOP) | After WC-5 | Items JSONL from `corpus-synth/manifest.json` sop00–sop08 (8/30/60 s, all in-cap), whole clip per item. Then `python $M/dataset.py run --manifest $O/sop-incap.jsonl --state $O/sop.sqlite --dataset-version e1b-w1-sop --base-url http://127.0.0.1:8001/v1 --model nemostation/marlin-2b --retain-output digest --form upload --concurrency 1 --profile … --key-inventory …`, SIGINT after 3 `done`, the same command again, then `dataset.py export` | 5–10 min. Basis: 9 clips of 8–60 s, with a passing 60 s clip bounded by E4B §5's 90 s-per-clip-minute e2e p95 | ≤ 121.6512 | All 9 items are `done`. Each item key has one accepted answer and at most one debit. Resumed items answer as replays (`Idempotency-Replayed`). `drift.py` shows 0. No accuracy statement (P-07) |
| **WC-6** caption-event parity on in-cap clips (L8; D-13 option A) | Last box-local GPU step before §5.0 | A copy of `research/plan/evidence/w/box/box-lane/l8ref.sh` with `CLIPS` = sop00–sop08 plus the two 10 s samples (11 clips). Then `l8served.sh` over the same list at both budgets, then `l8compare.py`. The original script stays in its append-only directory | 8–12 min. Basis: reference 11 × 8.9–23.3 s = 98–256 s (`meas.` per-clip range, E1B-box-20260923T2155Z L8), plus the restore's `meas.` `ready_s=169`, plus the served half at c = 1 | 0 | Events and their order are equal per clip at the processor default (the parity pair, §4). Served v1 events are recorded for context. Frame count and prompt tokens are logged per path where the tool reports them, and named as missing where it does not. The restore prints `restored=yes` with the same args and image, or the window stops |
| **WC-7** cold/warm split | Immediately after WC-6's restore, with no request in between | `python $M/bench.py $BOX --rate 0.5 --requests 128 --engine-state restarted --dataset-version e1b-w1-cold …`. Items 1–64 are the 64 distinct clips (60 in-cap), which are cold. Items 65–128 are warm (schedule, checked locally) | 5–6 min. Basis: last scheduled arrival 257.8 s, plus the tail | ≤ 1,730.1504 | The first WC-0 scrape after the restore reads 0 prefix and 0 MM cache queries. Otherwise the cell is reported as warm. Report `cold_ttft_s` and `warm_ttft_s`: p50 is supported, and p95 sits exactly at the 60-sample floor. The gateway's processing cache is **not** cleared, so this is an **engine**-cold split. A preparation-cold split needs an emptied `PROCESSING_CACHE_DIR`, a box state change that is the coordinator's decision and is not planned |
| **WC-9** mixed tenants | After runbook §5.1, from the coordinator host, through the edge | `python $M/bench.py --corpus $M/corpus/manifest.json --subset full --base-url https://marlin2b.callbill.ai/v1 --target gateway --model nemostation/marlin-2b --rate 0.5 --requests 135 --seed 20260922 --forms video_b64 --max-tokens 128,512,1024 --retries 0 --tenant-keys INFRX_API_KEY,INFRX_API_KEY_B --dataset-version e1b-w1-2t --profile <two-tenant perf base, WR-1> --key-inventory ~/e4c/keys-journey.json …` | 5–7 min | ≤ 1,824.768 in total. Tenant B has 67 items, ≤ 905.6256 of its one 10,000 grant | Per-tenant accepted counts, failure rate (< 1 % each) and latency. Per-tenant ledger debits sum to that tenant's rows. The client is outside the box, so the edge and the WAN are included, and the cell is not comparable with the box cells |

**Window budget (`est.`).** 52–73 min of cells plus 10–20 min of transitions, about
**1.0–1.6 h** added to the E4C window. At the P-19 list price this is 2.24208 × 1.0–1.6 =
**2.24–3.59 USD** of instance time (`est.`, a list price, not a bill). The gateway cells'
CREDIT ceilings sum to **8,137.1136**. The certify tenant's share, WC-3/4/5/7/8 plus about
half of WC-9 (68 × 13.5168), is **7,231.488**. Read the tenant's balance before WC-3 and stop
if it is below that figure. The runbook's own estimate of about 9,200 CREDIT per full certify
run (§6) leaves room in the P-24 allocation, but only the ledger decides.

**Order.** §4 → WC-1 → WC-2 → WC-3 → WC-4 → WC-5 → WC-8 → WC-6 → WC-7 → §5.0/§5.1 →
WC-9 → §6 drills. Each drill's restart would reset WC-7's cold state, so no drill runs
before WC-7.

### 7.3 Cost per successful video and per video-second (formula; `est.` only)

Inputs: a cell's bench summary (the certify `work/<cell>.jsonl` or a window cell's `--out`)
and the P-19 row, [`cloud-pricing.md`](../../../research/cross-cutting/cloud-pricing.md)
§3.1 line 176: `g6e.2xlarge` 1× L40S, **$2.24208/h on-demand**, AWS price sheet us-east-1
Linux, published 2026-09-18, fetched 2026-09-20. The box profiles carry the same figure in
`measurement.price`.

- `USD_cell = 2.24208 × instances (1) × wall_s / 3600`. This is bench's
  `measurement.infrastructure_cost.infrastructure_usd`. Per P-19, the whole box hour is
  attributed to the measured workload.
- **USD per successful video** = `USD_cell / N`, where `N` = `measurement.counts.fresh_accepted`
  restricted to media items within the cap. In a video-only cell that is the fresh accepted
  count minus text rows (none). Replays, cancels, rejections and failures are excluded
  (RV-08).
- **USD per successful video-second** = `USD_cell / measurement.successful_clip_seconds`,
  over unique in-contract item keys. × 3600 gives bench's `usd_per_successful_video_hour`.
- **CREDIT per successful video** = the ledger's CREDIT debits for the cell's tenant over the
  cell (the usage resource, reconciled by `drift.py`) / `N`. Bench reports `charges.credit`
  as null by design. It is never derived from token counts × the card on the client side,
  and never converted to or from USD.

Worked sketch, `est.`. It uses the schedule's last arrival as a lower bound on `wall_s` and
**assumes that every in-cap item succeeds**, so it is a lower bound on cost per unit. The
measured counters replace every term.

| Cell | `wall_s` ≥ | in-cap items | clip-seconds | USD_cell | USD / video | USD / video-second | USD / video-hour |
|---|---|---|---|---|---|---|---|
| `envelope-r0.5` | 276.3 | 126 | 2,604 | 0.172080 | 0.001366 | 6.608e-05 | 0.2379 |
| `soak` (0.25 × 14,400 s) | 14,218.1 | 3,374 | 71,180 | 8.855033 | 0.002624 | 1.244e-04 | 0.4479 |

These agree with P-01's cost basis (0.239 and 0.449 USD per video-hour from run3's counters,
`15-pending-inputs.md`). **Full service cost** adds the control plane (hosted Postgres/Auth,
the edge). `cloud-pricing.md` has no row for it: ⚠️ TO BE VERIFIED, and no figure is written
here. The ⚠️ in §4 and §5 item 8 about the ≈ $2.24/h operational figure is historical for
cells run before 2026-09-25. From P-19's decision onward, the sourced row above is the input,
and every derived figure stays `est.` until a bill replaces it.

### 7.4 D-13 disposition (proposal; the coordinator decides)

D-13 is the 2026-09-23 L8 result: caption-event parity 0/3 on the 120 s `sop-synth-v1`
clips, undiagnosed. Those clips are now over the 82 s cap (P-20), so the product never sends
them to the engine: the gateway answers 400 `unsupported_media`.

- **Proposed: option A.** Run WC-6 in the window on the in-cap clips (sop00–sop08 at 8, 30
  and 60 s, plus the two 10 s samples that matched before). If the 30 s and 60 s clips match,
  retire D-13 as **over-cap only, unreachable through the product**, with WC-6 as the
  evidence. If they do not match, D-13 becomes a **live in-cap parity finding** for the
  serving stack, and the §4 parity criterion fails within the cap. Its diagnosis (frame
  indices and prompt tokens per path) then goes to W/E. Cost: about 8–12 min of window and no
  CREDIT (`est.`).
- **Option B**, if the window cannot spare it: retire D-13 as stated, because no 120 s clip
  reaches the engine at 82 s. The underlying cause is not diagnosed, and it may scale with
  duration, so the retirement must say that **in-cap caption parity beyond the two 10 s
  samples is unmeasured**. It may not be read as parity.

## Verification log

- 2026-09-22 (E1B.a–c): Protocol predeclared before any measurement. The workload,
  seed, dataset identity, run matrix and criteria above were fixed with **no GPU run,
  no cloud operation and no paid call**; the only numbers quoted are the four
  committed L40S rows of 2026-09-19 (two clips, p50-grade) and the coordinator's
  read-only inventory of the current box, both labelled as such. Nothing here has
  been measured on the target.
- 2026-09-24: §5 items 9–11 and the §3 profile amendment appended at the E1C merge (evidence `research/plan/evidence/e/E1C-2531dc4.md`).
- 2026-09-26 (E1B-PREP): §7 appended: window cells WC-0…WC-9 for the E4C window, the cost formula over bench/report counters × the P-19 row (`cloud-pricing.md` §3.1), and the D-13 proposal. Nothing run: no GPU, no box, no cloud, no paid call; every number in §7 is `est.` with its basis, or a quoted criterion. §1–§6 unchanged (evidence `research/plan/evidence/e/E1B-prep-*.md`).
