# E4B certification protocol — predeclared before any E4B number

Task E4B (`research/plan/18-marlin-backend-first.md` §E4B, slices E4B.a/E4B.b). This file is the
**pre-registration** of the release-candidate certification: the checks, the matrix cells,
their shapes at each scale and every criterion that decides a check are fixed **here**, and
committed before the runner that executes them (`tests/integration/backend/certify.py`). Git
order is the proof. A criterion is never edited after an E4B result exists; a change is an
amendment appended to the log at the end, with the reason.

The one source of the numbers is `certify.py`: §5's table quotes `certify.CRITERIA` and
`certify.MATRIX`, and `tests/integration/backend/test_certify.py` holds the two equal. Most
criteria are imported, not restated: W4's `measure/decide.py` constants, E1B's sample rule and
`research/production-api/01-requirements-and-traffic-model.md` §2.3. **Nothing here is a
target or an SLO**: every latency, rate and soak row was **provisional (P-18)** until a
workload owner supplied one. Amendment 6 (2026-09-25): P-18 is decided; §5's rows are E4C's
single-GPU pilot limits for the pinned engine, still not a public SLA.

## 1. Identity

| Axis | Value |
|---|---|
| Release under test | the git SHA the runner runs at, dirty flag at start and end, plus the hashes `certify.py` computes into its report (`hashes`): serving version, `serve.sh`, the engine-options digest recomputed from the pinned flags, runtime image and model digests, contract limits, migrations, the `deploy/` tree, alert rules, the lockfile, and the published Marlin release record |
| Target `local` | the E2 compose stack (`INFRX_E2_NAMESPACE`, default `e2`) and the runner's own fake vLLM (`tests/integration/fake_vllm.py`). **Every number from it is labelled `fake-engine, not a measurement`** |
| Target `box` | the pilot box (P-04 target) inside the coordinator's maintenance window: `--target <gateway /v1 URL> --engine-url <engine URL> --metrics-url <gateway /metrics> --inventory <inventory.sh output> --box`. Numbers are `meas.` |
| Corpora | licensed `models/marlin2b/corpus/manifest.json`, `--subset full` at the `box` scale and `--subset fast` at `tiny` (envelope, soak, overload, dataset resume; amendment 1); W4's parity set (`measure/parity.py` `PARITY_SET`, which includes `sop-synth-v1`'s three 120 s clips) |
| Seed | `20260922` (E1B's frozen seed); the dataset version is `e4b-<short sha>-<UTC>`, so a second run never replays the first run's items |

## 2. Preconditions

Checked by the runner (`e4b.b.preconditions`); a failed precondition is a FAIL of that check,
and the other checks still run and are recorded.

1. **App and Lab stopped.** No Next.js server of this repository (a `next-server` or `next
   start|dev` process whose working directory is inside the repository's main checkout or one
   of its worktrees) runs on the runner host. The backend is certified with both applications
   stopped (18 §Deliverable).
2. **Box only:** `E4B_WINDOW_OK=1`, the coordinator's statement that a logged maintenance
   window is open (W4's `W4_ENGINE_RESTART_OK` rule: the runner cannot stop new traffic).
3. **Box only:** the engine is idle: `vllm:num_requests_running` and
   `vllm:num_requests_waiting` are both 0 on `<engine-url>/metrics` (W4 `candidate.sh`).
4. **Box only:** every parity clip is in the corpus cache (`parity.py --check`, W4
   precondition 3).

The measurement checkout at the release SHA (W4 precondition 2) and the inventory file
(`measure/inventory.sh`, W3) are the coordinator's inputs; the runner records their hashes.

## 3. E4B.a checks

| Check | What runs | Passes when |
|---|---|---|
| `e4b.a.protocol` | the phase-2 gate's stages on the E2 stack (preflight, services, migrate, rls, backend), exactly as `run.py --layer 3` runs them; this check is the backend suite **outside** `recovery/` | every stage passes and every backend case outside `recovery/` passes; a pending case pends with its typed ids |
| `e4b.a.sop-parity` | `measure/parity.py` at c = 1 on the parity set against the engine, paired by `decide.parity_verdict` with a baseline: local = a second run on the same fake engine; box = `--parity-baseline` (W4's E0 `parity.jsonl`, or the previous certified release's) | `decide.parity_verdict` is `pass` |
| `e4b.a.dataset-resume` | `bench.py` over licensed-corpus items with `Idempotency-Key: sop1.<item_key>`, **SIGINT after `interrupt_after` accepted rows**, then `bench.py --resume <raw>` | client: the first run was really interrupted, every item terminal after the resume, no terminal item re-sent, each item one key; server (metered target only): one usage record and no remaining hold per accepted item, one Inference-Id per item across both runs, every record in CREDIT, Σ charged = ledger delta, reserved back to its value before the run |

## 4. E4B.b matrix

| Cell | Driver | Passes when |
|---|---|---|
| `e4b.b.envelope` | `bench.py` open loop, one run per rate of the ladder, `--retries 0`, `--max-tokens 128,512,1024`, `--forms video_b64` | per rung: platform-caused failure rate below `max_failure_rate`; no rejection other than the duration cap's; TTFT p95 of short clips ≤ `ttft_p95_short_s`, request latency p95 ≤ `latency_p95_s` (amendment 6) and end-to-end p95 per clip-minute ≤ `e2e_p95_s_per_clip_minute`, each with ≥ `p95_min_accepted` samples; the envelope is the highest rung that passes (box: the declared rung, `declared_rate_per_s`, amendment 6 fix round). **Duration cap (P-20):** every attempt on a clip longer than `engine_ceiling_s` is refused at admission (4xx), never accepted and failed by the engine; no attempt on a clip of at most `applied_cap_s` is refused. *Superseded by 5(c): the deployed cap (`MAX_VIDEO_SECONDS`) is the one bound, and a clip over it gets the typed refusal* |
| `e4b.b.soak` | `bench.py` open loop at `soak.rate` for `soak.seconds`, the target's `/metrics` scraped every `soak.sample_s` | failure rate below `max_failure_rate`; growth (second half's maximum over the first half's, `decide.growth`) of `infrx_process_resident_bytes` ≤ `max_host_growth_mib` and of used GPU memory ≤ `max_gpu_growth_mib`; `infrx_reconciliation_drift` and `infrx_unsettleable_jobs` 0 at the end; latency p50 of the last third ≤ `soak_latency_drift` × the first third's |
| `e4b.b.overload` | `bench.py --burst <burst>`: `burst` requests from one key at one instant, a P4 cell (box: through the public edge, §5) | at least one accepted; at least one refused; **every** refusal is a 429 carrying a numeric `Retry-After` and one of `overload_codes`; no 5xx and no platform-caused failure |
| `e4b.b.recovery` | I3B's `rc*`/`bk*` drills: local = the backend suite's `recovery/` cases on the E2 stack; box = I3B's runbook drills, executed by the coordinator from the E4B box protocol | every drill passes, or pends on a typed owner |
| `e4b.b.config-pin` | the tree against the settings W3/W4/M4 declared (`certify.DECLARED`); the published Marlin release record against the serving version; box: the deployed engine (`--inventory`) against the pin | every value equal. A difference is a FAIL naming the evidence it would invalidate: re-measure and re-declare, never ship silently ("reject any optimization that invalidates earlier evidence") |

## 5. Criteria and cell shapes (`certify.CRITERIA`, `certify.MATRIX`)

| Id | Value | Source |
|---|---|---|
| `max_failure_rate` | 0.01 | `decide.MAX_FAILURE_RATE`; E1B §4 error rate per cell, decided (P-18, 2026-09-25; `research/plan/15-pending-inputs.md`); meas. 0/126 at 0.5 req/s on run3 |
| `p95_min_accepted` | 60 | `decide.P95_MIN_ACCEPTED`; E1B sample sufficiency (method) |
| `ttft_p95_short_s` | 6.0 | 01 §2.3 interactive TTFT p95 for clips ≤ 30 s at ≤ 720p; decided (P-18, 2026-09-25; `research/plan/15-pending-inputs.md`); meas. 2.4461 s at 0.5 req/s on run3 |
| `short_clip_max_s` | 30.0 | 01 §2.3 (the clip class the TTFT row names) |
| `short_clip_max_edge_px` | 1280 | 01 §2.3 "≤ 720p" read as the long edge of 1280×720 |
| `e2e_p95_s_per_clip_minute` | 90.0 | latency p95 per clip-minute at 0.5 req/s; replaces 01 §2.3's provisional 45 (run3 failed it); decided (P-18, 2026-09-25; `research/plan/15-pending-inputs.md`); meas. 77.3142 s over 126 samples on run3 `bda1586` |
| `latency_p95_s` | 9.0 | whole-request latency p95 at 0.5 req/s, judged beside the TTFT row; decided (P-18, 2026-09-25; `research/plan/15-pending-inputs.md`); meas. 7.6688 s on run3 (`work/envelope-r0.5.jsonl`) |
| `declared_rate_per_s` | 0.5 | box: the only rate the certificate supports; the envelope's P-18 rows are judged at this rung. Rungs 1.0 and 2.0 run and are reported as `measured_passing_rate_per_s`, never as supported; if this rung fails nothing is supported and the soak does not run; decided (P-18, 2026-09-25; `research/plan/15-pending-inputs.md`) |
| `max_host_growth_mib` | 512 | `decide.MAX_HOST_GROWTH_MIB` (W4 memory criterion); decided (P-18, 2026-09-25; `research/plan/15-pending-inputs.md`) |
| `max_gpu_growth_mib` | 256 | `decide.MAX_GPU_GROWTH_MIB`; decided (P-18, 2026-09-25; `research/plan/15-pending-inputs.md`); must be measured in E4C (unknown on run3) |
| `soak_latency_drift` | 1.5 | E4B engineering criterion, decided (P-18, 2026-09-25; `research/plan/15-pending-inputs.md`) |
| `applied_cap_s` | 72 | P-20 interim: the cutover's `MAX_VIDEO_SECONDS=72`. *Superseded by 5(c): the deployed cap* |
| `engine_ceiling_s` | 82 | `decide.ceiling_s(<encoder budget of the pinned flags>)`: 16,384 tokens today (W4 P-20 record) |
| `overload_codes` | capacity_exhausted, journal_capacity_exhausted, rate_limited | `errors.RETRY_AFTER_CODES` minus `dependency_unavailable` (a dependency, not overload) |

| Scale | Envelope rates (req/s) × requests | Soak rate × seconds, sample every | Overload burst | Dataset items / interrupt after / rate |
|---|---|---|---|---|
| `tiny` (local) | 4.0 × 12 | 2.0 × 10, 1 s | 32 | 12 / 4 / 4.0 |
| `box` | 0.5, 1.0, 2.0 × 120 each | 0.25 × 14400 (decided, P-18: fixed; runs only once the declared rung is supported), 30 s | 32 (4 × `max_active_jobs_per_key`) | 24 / 8 / 1.0 |

The `tiny` scale proves the runner end to end and cannot support a p95 (12 samples): its
latency rows are `unknown` by construction, so a local run never passes the envelope.

**Decided limits the runner does not read from `CRITERIA`** (P-18, 2026-09-25, `research/plan/15-pending-inputs.md`;
single-GPU pilot limits for the pinned engine, not an SLA; no availability claim and no SOP
accuracy claim):

| Limit | Value | Source |
|---|---|---|
| Refusals within the cap at the declared 0.5 req/s | 0 (the rung's `rejections` row) | run3 `report.json`:280-304 |
| Engine restart | gateway `/readyz` 200 ≤ 300 s | meas. 172 s (`research/plan/evidence/coordinator/2026-09-22-session-02.md`:940) |
| Worker SIGKILL | ≤ 30 s | meas. 8 s (session-02.md:941) |
| Valkey index loss | ≤ 30 s; no gateway 5xx other than 503 `dependency_unavailable` | meas. ~6 s (session-02.md:942) |
| DB or object-store stall | a retryable 503/504 within 45 s | `research/plan/evidence/e/E3C-8406c79.md`:249 |
| Recovery correctness (every drill) | every accepted job reaches exactly one terminal state and settles once; inference and settlement succeed after recovery (P7) | `research/plan/evidence/coordinator/2026-09-24-S3-reconciliation.md`:170 |

The recovery bounds are judged by the I3B drills and the coordinator's box drills (§4
`e4b.b.recovery`), not by a number in `CRITERIA`; the E4C drill record prints each measured
recovery time next to its bound above with PASS or FAIL (a record rule, no runner code).
The runner's failure rate is stricter than P-18's "platform-caused ≤ 1 %": it is
`< max_failure_rate` over every attempt that got no answer, platform-caused and transport
alike (amendment 3(c)), with the platform-caused share in the row's detail.

**The box overload burst enters through the public edge** (E1B-protocol rule 11; S3 F5;
CW-V3): certify stamps the overload cell P4, which bench refuses on any profile but a
`public-edge` one, runs it under `--overload-profile` at `https://<its first allowlist
host>/v1`, and without that profile the box overload cell is BLOCKED (PENDING on `PROFILE`).

## 6. What invalidates a result

E1B §5 in full, plus:

1. any number from the `local` target quoted without `fake-engine, not a measurement`;
2. a config-pin difference (§4) left unresolved: the measured envelope then belongs to another
   serving configuration;
3. a report whose `git_head` and `git_head_end` differ, or either is dirty;
4. a box run without the §2 preconditions.

## 7. What this protocol does not decide

**BACKEND-READY.** It needs the box half (P-04), P-20 resolved, the pending owners of the local
run closed, and the coordinator's decision recorded in
`research/plan/evidence/e/E4B-release-decision.md`. Cost per video-hour (P-19), accuracy
(P-07) and an availability target (P-18) are outside it.

## Verification log

- 2026-09-23 (E4B): Protocol predeclared before `certify.py` existed and before any E4B run.
  No GPU, box, AWS or hosted service was used; the only numbers quoted are constants of
  `decide.py`, E1B and 01 §2.3, each with its source.
- 2026-09-23 (E4B), **amendment 1**, before any E4B run: the dataset resume uses the licensed
  corpus, not `sop-synth-v1`. Reason: `bench.load_corpus` selects clips by their `subset`
  field, which `corpus-synth/manifest.json` does not carry, so bench.py cannot schedule
  `sop-synth-v1` items. `sop-synth-v1` stays in the certification through W4's parity set.
  The `tiny` scale reads `--subset fast`, the `box` scale `--subset full`.
- 2026-09-23 (E4B), **amendment 2**, before any certification run was recorded (only the
  runner's own unit cases and two unrecorded smoke calls against the fake engine had run):
  (a) a client run (bench.py) that does not exit 0 fails its cell, and on the envelope it
  ends the climb like a failed rung - a crashed client is never an unjudged cell;
  (b) the dataset ledger is re-read for up to 300 s after the resumed run, because a debit
  may land after the answer; (c) a check the local target (an engine) can never judge pends
  on `BOX`, not on a task id. None of the §5 numbers moved.
- 2026-09-23 (E4B), **amendment 3**, the review fix round (`E4B-review-37a4652.json`), after
  the local runs of `9aa7ffe`/`37da3b3` (so appended, not edited in place; §5's numbers are
  unchanged and the test still holds them):
  (a) §6.3 is enforced by the runner: a `release-identity` entry FAILs unless both git
  samples carry the same SHA and a clean tree; git that cannot answer (no git in the runtime
  image, not a tree) is an **unknown** tree, never a clean one. A box run names its release
  (`--release-sha`) and the checkout's SHA must be it (F1/F2).
  (b) A box run also reads the served build (`--metrics-url`): `e4b.b.served-build` FAILs
  unless the gateway's `infrx_build_info` revision is the report's tree and the gateway runs
  the image built for the release (`INFRX_CERTIFY_GATEWAY_IMAGE` = `INFRX_CERTIFY_RELEASE_IMAGE`,
  both read with `docker image inspect` in the box step) (F3).
  (c) §4's failure rate counts **every** attempt that got no answer - transport timeouts and
  resets as well as 5xx and broken streams (the platform-caused share stays in the detail) -
  and a rung or soak that accepted nothing fails; a reset under overload is a failure (F4).
  (d) `meas.` only from a `--box` run whose preconditions passed; the local target stays
  `fake-engine, not a measurement` and any other target is `unverified target, not a
  measurement` (F5).
  (e) The config pin's W3/W4 values are read from `serving-version.json` (never typed), and
  `serve.sh` is the second source held against it (F6).
  (f) §2.1: an App or Lab is a Next.js process in an `apps/app` or `apps/lab` package of any
  checkout, found by its working directory or its command line; one whose working directory
  cannot be read counts as running - unknown is not stopped (F7).
  (g) The stack halves are judged with the backend run's own pytest exit code (N1).
- 2026-09-23 (E4B), **amendment 4**, the verifier's fold-ins (`E4B-verify-7b5dbd7.json`,
  PASS with V1-V6): (a) amendment 3(d) now also requires `e4b.b.served-build` PASS for
  `meas.` (V4); (b) `serve.sh`'s encoder budget is held against the record, and a value that
  is not a literal integer reads as unknown, which fails the pin (V2); (c) `--release-sha` is
  the **full** 40-character commit id - a prefix is not the release - and a served revision
  shorter than 7 characters identifies nothing (V5). Stated, not coded (V3): the runner reads
  only `E4B_WINDOW_OK=1` from the window record; the record's time and the edge's maintenance
  site are the operator's checks in the box protocol's step 5, not the runner's.
- 2026-09-24 (CERTIFY-TREE), **amendment 5**, after the first box run (in
  `infrx-runtime:<release>`, which has no git; out `20260924T165408Z`) failed
  `e4b.b.served-build` with the report's tree `None`. None of the §5 numbers moved.
  (a) Where git cannot exist - no git binary, or a checkout with no `.git` - the tree under
  test is `--release-sha`: both `git_head` samples read
  `{"sha": <release>, "dirty": null, "source": "--release-sha (no git)"}` (or `(no .git)`),
  and the served-build cell names that source. The state stays unknown, so `release-identity`
  still FAILs (§6.3, R97): only a run with git proves one clean SHA. Git that runs is never
  overridden - a SHA other than `--release-sha` stays a FAIL of both entries.
  (b) The served build is read from both processes: `--box` also needs `--worker-metrics-url`
  (box: `http://127.0.0.1:8002/metrics`, the worker's `infrx_build_info{process="worker"}`
  since I2B-R4). `e4b.b.served-build` FAILs unless the gateway's and the worker's revisions
  are each the report's tree, each read from a page whose `process` label is that process's.
  (c) The deployed duration cap, from the same box run: its sop-parity cell failed with
  "refused by the candidate". W4's E0 baseline and the release engine both refuse the parity
  set's 112 s and 120 s clips (16,384-token encoder budget), and the pilot's admission
  refuses every clip over `MAX_VIDEO_SECONDS=82` (P-20 decision B).
  - The runner reads the cap once, from the environment the box step passes (`--env-file`),
    with the gateway's own parser; unset, the tree's default (120). It records the cap in the
    report's `target.max_video_seconds` and the config pin's `deployed_max_video_seconds`.
    The cap replaces §5's interim `applied_cap_s` (72) everywhere; §5's other numbers do not
    move.
  - The typed over-cap refusal is the M layer's (`MediaProfile.check`): HTTP 400,
    `unsupported_media`, param `messages`. On bench.py's rows it is the status and the code,
    because bench's allowlist does not keep the param.
  - `e4b.a.sop-parity` pairs only the parity clips within the cap. It sends each clip over
    the cap to the gateway, as bench.py would, and that clip must get the typed refusal. A
    within-cap refusal or an over-cap acceptance FAILs. An engine target has no admission,
    so its over-cap clips pend on `BOX`.
  - The envelope's `duration_cap` counts a clip over the cap as passing only when it gets the
    typed refusal, and it FAILs a within-cap clip refused as over the cap. A clip at the cap
    is within it. Failures, soak and overload are judged over the clips within the cap.
  - `e4b.a.dataset-resume` schedules only clips within the cap, from a copy of the licensed
    manifest in the workdir that the first run and the resume both read. An item refused as
    over the cap anyway FAILs the drill: the gateway's cap is then not the runner's.
  (d) R106, a cancelled job's replay is terminal for that key. This comes from the box rerun
  (out `20260924T172244Z`): its dataset-resume drill FAILed "items not terminal after the
  resume" for exactly the two items the SIGINT interrupted.
  - Why: a torn stream is a client that left, so each job is a committed cancel (R21). The
    resume's replay of the same key answered that committed result, `state_conflict` (R91),
    and bench.py re-sent it as a failure every time.
  - bench.py now records a replay whose stream answered `state_conflict` as
    `cancelled_by_interruption`, which is terminal. It reads only the allowlisted code, and
    only a stream error event (`stream_error_event`) counts: the sync replay is a 409, which
    is already terminal by its status.
  - The drill FAILs an item accepted twice, and a cancelled item that was not replayed
    exactly once. A cancelled replay counts as cancelled by the interruption only when its
    item's first attempt was the client's own tear (a transport error), or when the item has
    no first-run row at all (in flight at the SIGINT, cut before bench wrote one). Any other - a
    platform-side failure the relay cancelled - is listed as cancelled by the platform and
    FAILs the drill (R106's corrected text). A passing drill states the property it proved:
    no second accepted item, nothing re-sent after it was terminal, each item the
    interruption cancelled terminal after exactly one replay, and none cancelled by the
    platform.
  - On the ledger a cancel carries no usage. A cancelled job's hold that is still held
    (`held_unknown`, R21) is accounted in the reserved total, not failed.
  - The envelope's over-cap refusals appear in every rung (the rerun: 8 × `unsupported_media`
    at r = 0.5). They are the cap's by design and never lower the supported rate.
  (e) The post-merge polish round, from the CERTIFY-TREE verifier's findings and box run2 at
  `4226315` (out `20260924T172244Z`). None of the §5 numbers moved.
  - Rewording in place: 5(d) was reworded on the same day, before main, to match R106's
    corrected text (`9b3b851`). In (c), §4's envelope row and §5's `applied_cap_s` row are
    marked superseded in place.
  - Each bench run's timeout is its own schedule, requests over rate, plus 900 s. The soak
    gets its seconds plus 900; a flat hour cut run2's 4 h soak at exit 124.
  - A box rung sends its declared 120 requests, or the fewest more for which bench's
    schedule holds 60 short clips (135 on today's corpus). The TTFT p95 was unknown at every
    run2 rung, with 54, 52 and 40 samples.
  - Capacity is checked before duration. An over-cap attempt refused 429 with an overload
    code is not judged by the duration cap. Parity asks such a 429 once more after its
    Retry-After.
  - On a gateway, the soak reports a breach of the cap.
  - Each latency row prints its p50 and accepted count beside the p95. run2's e2e p95 of
    about 91 s per clip-minute against the provisional 45 s is a measurement, and the
    criterion stands.
- 2026-09-25 (E4C-PREP), **amendment 6**, before any E4C run: P-18 is decided
  (`research/plan/15-pending-inputs.md`, "Decisions 2026-09-25"), and this amendment is
  committed before the first qualifying run's start (consumer-v1/05 §3; P-17 check 4).
  `e2e_p95_s_per_clip_minute` 45.0 → 90.0 (run3 measured 77.3142; the provisional 45 came
  from 01 §2.3 and was never an agreed target). New `latency_p95_s` 9.0, judged in each
  envelope rung beside the TTFT row: `unknown` below `p95_min_accepted` samples, like the
  other rows. Every other §5 number is unchanged and now decided. The recovery bounds, the
  declared supported rate, the soak rate and the public-edge rule for the box burst are
  recorded in §5 as protocol text. The runner's soak derivation is unchanged (see §5).
- 2026-09-25 (E4C-PREP), **amendment 6, fix round** (recheck E4P-V1/V2/V5/V6), before any
  E4C run. (a) New `declared_rate_per_s` 0.5: on the box the envelope's supported rate is
  the declared rung when it and every rung below pass. Its P-18 rows are the ones judged;
  1.0 and 2.0 are measured only (`measured_passing_rate_per_s`); a failed declared rung
  supports nothing. (b) The box soak is P-18's fixed 0.25 req/s × 14,400 s (3,600 requests,
  the P-24 bound), run only once the declared rung is supported; this replaces the
  derivation from the highest passing rung that amendment 6 kept. The `tiny` scale is
  unchanged. (c) certify stamps the overload cell P4 and runs the box burst only under
  `--overload-profile` (a `public-edge` profile), else BLOCKED. (d) §5 states that the
  runner's failure rate counts transport failures too, and that the drill record prints each
  recovery time against its bound.
