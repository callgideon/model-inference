# E1B-PREP: window-ready E1B cells for the E4C window (preparation; nothing run)

| Field | Value |
|---|---|
| Task | E1B, preparation lane E1B-PREP. The box cells run in the E4C window |
| Branch / worktree | `codex/e1b-prep` / `.claude/worktrees/codex-e1b-prep` |
| Base / head | `b967a033` / `fc3d66a8` (the protocol and README commit; this file and the update land in the next commit) |
| Changed paths | `models/marlin2b/results/E1B-protocol.md` (new §7 plus one log line; §1–§6 unchanged), `research/models/marlin2b/README.md` (one Planned line), this file, `research/plan/evidence/coordinator/updates/E1B-20260926T*.json` |
| Operations | None. No GPU, no box, no SSM/AWS, no hosted Supabase, no docker, no paid call. The only computation was local `bench.build_schedule`/`load_corpus` over the committed manifest (no media read) to derive schedule sizes, spans and in-cap counts |

Every number below is `est.` with its basis, a `meas.` figure quoted from committed evidence
with its source, or a quoted criterion (E4B-protocol §5 amendment 6, P-18, as coded in
`tests/integration/backend/certify.py` `CRITERIA`/`MATRIX`).

## 1. What the E4C certify run produces, what is missing, and the closing cell

Cells WC-0…WC-9 are defined in `models/marlin2b/results/E1B-protocol.md` §7.2, with their
command sketches, wall times, CREDIT ceilings and oracles.

| E1B slice / test ID | Already produced by the E4C run (report check, bench field, runbook section) | Missing | Closing cell |
|---|---|---|---|
| **E1B.a** corpus and client coverage | Certify cells use the E1 corpus `--subset full`: 64 clips, 16 geometries, 2–112 s, output mix 128/512/1024, seed 20260922 (`certify.bench_argv`). Each `work/<cell>.jsonl` carries `profile` (frame, duration and geometry spread from the schedule) and `output_lengths`. The 4 over-cap clips are `expected_invalid`. Runbook §3 fixes the profiles | (1) **Forms.** Certify is `video_b64` only (`BENCH_FORMS`, certify.py:647). Upload and URL appear only in the §5.1 journey at c = 2, which is functional, not performance, and BLOCKED on `MEDIA_BASE_URL`. (2) **Cold/warm.** `cache_regime: "unknown"` in `E4C-box.base.json`, and no restarted-engine cell. (3) **Mixed tenants.** Certify has 1 tenant (`test_key_ids` = `142c7d81`). (4) **Codec.** 64/64 clips are h264 (manifest), so the codec axis is not exercised. (5) The SOP-oriented synthetic clips are not in any certify cell | WC-5 (upload + inline; URL once approved), WC-7 (engine-cold split), WC-9 (two tenants), WC-8 (sop-synth in-cap). **Codec: no window cell.** It needs an E1 corpus extension and product support for a second codec, and stays a named limit |
| **E1B.a** predeclared acceptance | P-18 is committed before the run (runbook 0.1). Gateway criteria: `ttft_p95_short_s` 6.0, `latency_p95_s` 9.0, `e2e_p95_s_per_clip_minute` 90.0, `max_failure_rate` 0.01, host +512 MiB and GPU +256 MiB, soak drift 1.5×, declared rate 0.5 | Direct-engine and pair cells have no criterion, and E1B §4 keeps latency "no criterion". A cost acceptance was never stated | E1B-protocol §7.1 rule 5: the window cells are descriptive, with validity oracles only. §7.3 gives the cost formula, not a target |
| **E1B.b** direct engine vs gateway on identical semantics | The gateway side only: `work/envelope-r{0.5,1.0,2.0}.jsonl`, each 135 requests (`rung_requests`), open-loop | (1) **No direct leg.** The E1B L1/L0 rows (`meas.` 2026-09-23) ran on the pre-pin `--max-num-seqs 32` engine, and the pinned engine is 8 (`E4C-box.base.json` identity). (2) **Semantics differ today.** The worker sends `stop_token_ids` [248044, 248046] and `cache_salt` (engine.py:655-668). `bench --target direct` sends neither (bench.py:993-1004). The worker hands the engine a local file; bench sends a `data:` URL. The kwargs match: bench `training_budget_kwargs` = worker `budget_kwargs` = profile v1 | WC-2 replays certify's r0.5 and r2.0 schedules against the engine. They are identical (a pure function of n, clips, forms, seed, rate and mix; checked locally). The pair oracle is `prompt_tokens` equality per clip (media semantics). The `completion_tokens` equality needs **WR-3**. The file vs `data:` transport difference is part of the gateway's overhead, by design. `cache_salt` affects cache hits, not outputs, and WC-0 measures the cache regime per leg |
| **E1B.b** stage timings | `Server-Timing` through the gateway carries **`prepare` only** (relay.py:185, 210; the docstring at relay.py:34 names the worker timings pending, W3 request 9). bench `phases`/`stages_s` (upload, admission, first output, terminal) come from one client clock. The certify soak scrapes `rss_mib`, `gpu_used_mib`, drift, unsettleable, and running/waiting (certify.py `scrape`) | (1) **Engine queue/prefill/decode.** Not in any report. They are published by the engine as `vllm:*` histograms, but nobody reads them. (2) **Journal, persist and settle.** Timed in `AttemptResult.timings` (attempt.py:120, 539) but **never published**: no `observe_phases` in the worker. (3) **Retrieval and decode.** Not published by any process. (4) **DB and I/O.** The `infrx_db_pool_*` counters exist and are not read. Disk I/O is not sampled | WC-0 sidecar: per-cell histogram deltas for engine phases, `infrx_phase_seconds`, the DB pool counters and `vmstat` I/O. **Journal, persist and settle need WR-4.** Until then they stay `declared_missing`. Retrieval and decode stay `declared_missing` (no publisher; M-track) |
| **E1B.c** open-loop sustained, burst and rate sweep | `e4b.b.envelope`: `supported_rate_per_s` (declared 0.5), `measured_passing_rate_per_s`, per-rung verdicts (failure_rate, rejections, ttft_p95_short, latency_p95, e2e_p95_per_clip_minute, duration_cap, bench_validity). `e4b.b.soak`: 0.25 req/s × 14,400 s = 3,600 requests, verdicts (host and GPU growth, reconciled_at_end, latency_drift) and `samples`. `e4b.b.overload`: a P4 32-burst through the edge, `measured` {attempts, accepted, refused} | Burst **at the supported rate** (L3 `--burst 8`): last measured at bda1586 (0 × 429), before the drain and intake changes. Concurrency sweep on the pinned engine | WC-3 (burst-8 at 0.5), WC-1 (direct ladder 1/2/4/8) |
| **E1B.c** cancellation, long duration, denominators | Cancellation appears only in the journey (4 cancels, c = 2) and in the dataset drill's interruption (R106). Soak 4 h (P-18 fixed). bench `measurement.counts` (offered, attempts, rejected, failed, timed_out, cancelled, replayed) and `error_rate` over valid offers | Cancellation **under load** at the supported rate. The induced restart inside the soak (§3 L7) is replaced by the separate §6 engine-restart drill | WC-4 (cancel 0.2 at 0.5). The restart stays with the §6 drill. This is a named deviation from §3 L7's "plus one induced restart", because certify's soak has no fault schedule |
| **E1B.c** recommended capacity and bottlenecks | `supported_rate_per_s`, `measured_passing_rate_per_s`, bench `video_s_per_s`, `req_per_s`, `peak_in_flight`, `resources` | Where the time goes (the bottleneck) | WC-0 + WC-2 give per-phase gateway − engine deltas. WC-1 gives the engine's own batching curve at the pinned limit |
| **PERF-ENVELOPE** (04-verification.md:138) | Envelope, soak and overload, with raw denominators and sample-supported percentiles (bench rule p95 ≥ 60) | Paired direct/gateway, cold/warm paths and full cost | WC-2, WC-7, §7.3. Full-service cost is ⚠️ TO BE VERIFIED: `cloud-pricing.md` has no control-plane row |
| **MARLIN-SOP** (04-verification.md:95) | `e4b.a.dataset-resume`: 24 items, interrupt after 8, resume, `sop` property and the ledger half exact. `e4b.b.config-pin`: profile v1, `max_video_seconds` 82. Journey: over-cap typed 400 (unsupported mode) | The SOP client (`dataset.py`) is never run on the live gateway. The certify drill is bench on E1 clips. There are no SOP-oriented finite clips. "Snippets match real capabilities" is App/A3, not a box cell | WC-8 (`dataset.py` on sop-synth sop00–sop08, interrupt and resume). The snippet half is out of scope for this lane |
| **MEDIA-PARITY** (04-verification.md:23) | `e4b.a.sop-parity`: `parity.py` at c = 1 vs the E0 baseline, **over only 2 in-cap clips** (c039 at 2 s, c024 at 72 s). 7 of the 9 `PARITY_SET` clips exceed 82 s (4 × 112 s, 3 × 120 s; `measure/parity.py:36`) and are judged as the typed over-cap 400 | Parity on orientation and aspect extremes within the cap. Gateway vs engine token-budget parity. Caption-event parity vs the transformers reference within the cap (D-13) | WC-2(a) (`prompt_tokens` equality per clip across all 60 in-cap clips, both paths). WC-5 (the same across forms). WC-6 (L8 on in-cap clips) |

## 2. Findings from the code read (no run)

1. **Worker phase timings are dropped.** `AttemptResult.timings` (prefill, generate, journal,
   persist, settle; attempt.py:64-66, 120, 539) reaches neither `Server-Timing` nor the
   worker's `/metrics`. `observe_phases` is called only in relay.py:228. E1B.b's
   journal/persist/settle split is therefore not measurable in the window without WR-4.
2. **`bench --target direct` omits both EOS ids** (bench.py:993-1004). The worker always
   sends them (engine.py:663, S2M §1.2: the remap drops one). The 2026-09-23 direct rows
   (L0/L1) were taken without them. A direct/gateway output pair is not a pair until WR-3.
3. **`measure/concurrency.sh` refuses the pinned engine** (it requires `--max-num-seqs 32`;
   concurrency.sh:36-39). WC-1 calls bench directly.
4. **Certify's MEDIA-PARITY is 2 clips.** The cap moved to 82 s after `PARITY_SET` was
   frozen. `e4b.a.sop-parity` PASS means parity on c039 and c024 only, and must be quoted as
   such (S3 reconciliation already says "2 clips").
5. **E1B-protocol §4's cost ⚠️ is superseded going forward.** P-19 added the sourced
   `g6e.2xlarge` row at $2.24208/h (`cloud-pricing.md:176`, audit log `:1777`). The
   historical ⚠️ text is kept (it is test-pinned, and it is correct for cells before
   2026-09-25). §7.3 states the new input.
6. The box profiles' `measurement.price.source` cites `production-api/08`, not
   `cloud-pricing.md`, so bench labels the cost `estimated`, not `sourced`
   (bench.py:1529). The figure is the same. Relabelling needs a profile change (WR-1c,
   optional).

## 3. Cost (§7.3 of the protocol)

The formula is over bench's counters × the P-19 row. The worked lower bounds are `est.`
(schedule-derived `wall_s`, every in-cap item assumed successful): envelope-r0.5 at 0.2379
USD per video-hour and soak at 0.4479 USD per video-hour. They agree with P-01's run3-based
0.239 and 0.449 (`15-pending-inputs.md`, P-01 row). CREDIT per video comes from the ledger
only. The window's added instance time is `est.` 1.0–1.6 h, which is 2.24–3.59 USD at the list
price.

## 4. D-13 (proposal; the coordinator decides)

**Option A (proposed):** WC-6 re-runs L8 on in-cap clips (sop00–sop08 plus the two 10 s
samples) in the window, about 8–12 min (`est.`) and 0 CREDIT. If they match, retire D-13 as
over-cap only. If they do not, it becomes a live in-cap parity finding. **Option B:** retire
now as unreachable through the product at 82 s, stating that in-cap parity beyond the two
10 s samples is unmeasured. The full text is in protocol §7.4.

## 5. Wiring requests (not applied; outside this lane's owned paths)

- **WR-1 (profiles owner; `models/marlin2b/profiles/`).** Commit the window bases before the
  window.
  - (a) `E1B-direct.base.json`: `target.path` `direct-engine`, allowlist `127.0.0.1:8000`,
    arrival and concurrency for WC-1 and the rates for WC-2. `bench.py:2062` sets
    `expect_model` from `identity.model_revision`, and the direct stream reports the served
    name `marlin2b`, so the owner decides how the direct path satisfies it.
  - (b) A box window base for WC-3/4/5/7, with forms `[upload, video_b64]` for WC-5.
  - (c) Optionally, relabel `measurement.price.source` to cite `cloud-pricing.md` §3.1.
  - (d) An SOP base for WC-8 (the manifest sha of the generated in-cap items JSONL).
  - (e) A two-tenant perf base for WC-9: public-edge, `tenants` 2, open-loop 0.5,
    `max_requests` ≥ 135, `max_spend` ≥ 1,824.768 CREDIT.
  - Test: extend `models/marlin2b/tests/test_profile.py`'s runbook check, so every §7 bench
    line validates `--validate-only` as written against its base.
- **WR-2 (E4C runbook / rollout owner).** A committed launcher (for example
  `infra/rollout/e1b-window.sh`, or a certify mode) that stamps per-cell `run_id`,
  `dataset_version`, rate and concurrency into copies of the filled bases, as
  `certify.cell_profile` does, and runs WC-1…WC-8 in §7.2's order with the no-overlap
  preconditions. Pin its flags in `apps/infrx-api/tests/i/test_rollout.py`.
- **WR-3 (E1B implementer; `models/marlin2b/bench.py` `_send`).** For `--target direct`, add
  `payload["stop_token_ids"] = [248044, 248046]` (the worker's `MODEL_EOS_TOKEN_IDS`;
  `serving-version.json` `eos_token_ids`). Test in `test_bench.py`: a direct payload carries
  both ids, and a gateway payload carries neither (closed parameter set). Failure oracle:
  without it, WC-2's output half is NOT PAIRED.
- **WR-4 (W track; the worker).** After `result = await self.runner.run(...)`
  (worker/loop.py:82), call `metrics.observe_phases({k: v / 1000 for k, v in
  result.timings.items()})` on the worker's `Registry` (`__main__.py:134`). Timings are ms,
  and `observe_phases` takes seconds (metrics.py:298-301). Test: one fake-engine attempt
  increments `infrx_phase_seconds_count{phase=...}` for each timed phase. Without it,
  journal, persist and settle stay `declared_missing`.

## 6. Commands

| Command | Exit | Result |
|---|---|---|
| `python3 research/plan/scripts/validate_plan.py` | 0 | all PASS (943 local links across 271 documents) |
| `make api-env` | 0 | pinned env synced |
| `cd apps/infrx-api && uv run --frozen --no-sync pytest -q ../../models/marlin2b/tests` | 0 | 116 passed (includes `test_the_predeclared_protocol_matches_the_client_that_implements_it`) |
| local `bench.build_schedule` / `load_corpus` (manifest only) | 0 | rung 135; in-cap 126 / 2,604 clip-s per rung; soak 3,374 / 71,180 clip-s, last arrival 14,218.1 s; cold cell 64 cold (60 in-cap), last arrival 257.8 s; burst-8 last arrival 421.9 s; cancel 9 of 60, last arrival 120.4 s; tenant B 67 of 135; schedule identical across dataset versions |
| `git diff --stat b967a033..HEAD` | 0 | owned paths only |

No regression test was written. This lane changes no executable code, and the existing
protocol test still pins §1–§6.

## 7. Open issues

- WR-1…WR-4 must land before the window, or the affected halves are recorded as NOT PAIRED
  (WR-3) or `declared_missing` (WR-4), or the cells are refused by bench (WR-1/2).
- The URL form (WC-5) and the whole journey stay BLOCKED on `MEDIA_BASE_URL`, which is
  operator-held.
- A preparation-cold split needs an emptied `PROCESSING_CACHE_DIR`: a coordinator decision,
  and not planned.
- Codec coverage: h264 only.
- Full-service cost: there is no control-plane row in `cloud-pricing.md` (⚠️ TO BE VERIFIED).
- The certify tenant's balance after certify must cover 7,231.488 CREDIT of window ceilings
  (`est.`). Read it before WC-3.

## 8. Remaining effort (E1B through the window)

Optimistic 5 h, likely 8 h, pessimistic 12 h. Confidence: medium. Basis:
- WR-1 about 2 h, WR-2 1–2 h, WR-3 0.5–1 h, WR-4 1–2 h.
- Window cells about 1.0–1.6 h (`est.`, §7.2).
- Post-window analysis and the E1B record 2–4 h.
- The pessimistic case adds one window re-run of the pair after a semantics mismatch.
