# E4B — Certify the Marlin endpoint release candidate (software half)

## Task and status

- **Task:** E4B (track E), [18 §E4B](../../18-marlin-backend-first.md) slices E4B.a/b/c, the
  **software half**; the box-measured half runs later in the coordinator's maintenance window
  on the pilot box.
- **Status: implemented (software half). BACKEND-READY is not claimed**: it requires
  allocated GPU/staging proof, and software-only tests cannot close the gate (18 §E4B
  acceptance). The release decision is a template with the software half filled
  ([E4B-release-decision.md](E4B-release-decision.md)); its decision reads **PENDING**.
- **Oracles:** BACKEND-JOURNEY, BACKEND-DEPLOY, BACKEND-OBSERVE, PERF-ENVELOPE, ENGINE-OPT,
  MEDIA-OPT, OPS-RECOVER, MARLIN-SOP - each covered by a check of the runner (below), every
  one either judged locally with a `fake-engine, not a measurement` label or PENDING on a
  typed owner.
- Nothing was deployed. No hosted project, AWS call, pilot box, GPU or secret was used.
- **Owner/session:** Claude Opus 5.5 implementation session, worktree
  `.claude/worktrees/codex-e4b`, branch `codex/e4b-certify`.

## Source

| Field | Value |
|---|---|
| Base SHA | `7c52627` (the integration head: 27/30 packages merged). **D5 not merged**: this lane did not need `terminalize` - the ledger half of the dataset drill reads G6B's `Operations` and pends on D5 by construction |
| Implementation SHA | **`9aa7ffe`** (the head every run below ran at, except where a row says otherwise); this report is committed on top |
| Branch / worktree | `codex/e4b-certify` in `.claude/worktrees/codex-e4b` |
| Integrated SHA | none (coordinator) |

Commits, oldest first (no rebase, reset, amend, stash or push):

| Commit | What |
|---|---|
| `aaeb5ce` | the predeclared protocol `models/marlin2b/results/E4B-protocol.md`, **before any runner code** (git order is the proof) |
| `1203897` | item 1, E4B.a: `certify.py` (report, hashes, stack suite, parity, dataset drill), `test_certify.py`, `e4b_mutants.py` + `test_e4b_mutants.py` through the shared R83 runner, the Makefile `api-mutants` line, protocol amendment 1 |
| `b51a548` | item 2, E4B.b: preconditions, config pin, envelope / soak / overload cells |
| `26ffc27` | item 3, E4B.c: `endpoint_doc.py` + the generated `E4B-endpoint.md`, `test_endpoint_doc.py`, `E4B-release-decision.md` |
| `6c437a6` | owners: a check the local engine target can never judge pends on `BOX`, not on `G2-R1` (a held-cutover id leaves the vocabulary the day it lands) |
| `16904a0` | robustness: a runner error is a recorded FAIL with the report still written; a client that does not exit 0 fails its cell; the ledger is re-read until settlement (300 s) |
| `9aa7ffe` | protocol amendment 2 (the two robustness rules and the `BOX` owner, logged; no §5 number moved) |

## Items

Every mutant below is a single edit killed by an assertion in the named case, through the
shared runner (`apps/infrx-api/tests/contracts/mutants.py`, R83: anchor exactly once, a
pristine baseline per list, named cases only, assertion-shaped deaths). The list is
`tests/integration/backend/e4b_mutants.py`: **77 mutants over 25 named cases**.

| # | Slice | What was built | Killing case ← mutants |
|---|---|---|---|
| 1 | E4B.a | **`tests/integration/backend/certify.py`** - one command, one report JSON: `git_head` at start and end, `target`, `hashes` (serving record, `serve.sh`, the engine-options digest recomputed from the pinned flags, image and model digests, contract limits, migrations, `deploy/` and `infra/rollout/` trees, alert rules, `uv.lock`, the `infrx` package, the published release record), and one named entry per check. PASS / FAIL / PENDING / SKIP, where a PENDING or SKIP must name owners from a closed vocabulary (the backend suite's `stack.PENDING` + `recoverykit.OWNERS`, plus `BOX` and `STACK`) or it is recorded as FAIL; exit 0 only when every entry passes. **`e4b.a.protocol`** re-runs the phase-2 gate's stages with run.py's own functions (preflight, services, migrate, rls, backend) and judges the backend suite outside `recovery/` with the gate's own `run.backend_verdict`; **`e4b.a.sop-parity`** runs W4's `parity.py` over its parity set and pairs it with `decide.parity_verdict` (local: a second run on the same fake engine; box: W4's E0 file); **`e4b.a.dataset-resume`** runs E1B's `bench.py`, SIGINTs it after `interrupt_after` accepted rows, resumes with `--resume`, checks the client half (a real interruption, one key per item, nothing terminal re-sent, every item terminal) and, on a metered target, reconciles the tenant's own ledger through G6B's `Operations.tenant(secret)` (one job per item across both runs, one CREDIT usage record and one released hold per job, Σ charged = ledger fall, reserved restored) | `…the_report_carries_both_heads…` ← `skip_exits_zero`, `owners_not_recorded`; `…skip_or_pending_without_a_known_owner…` ← `untyped_skip_accepted`, `unknown_owner_accepted`; `…release_hashes_recompute…` ← `flags_not_substituted`; `…recomputed_digest_comes_from_the_flags…` ← `digest_copied_not_recomputed`; `…splits_into_protocol_and_recovery…` ← `recovery_counted_as_protocol`, `stack_down_ignored`, `pending_suite_is_pass`; `…sop_parity_pairs…` ← `parity_unknown_is_pass`, `parity_without_baseline_runs`; `…resume_drill_counts…` ← `interruption_unchecked`, `key_split_unchecked`, `resent_unchecked`, `item_count_unchecked`, `open_items_unchecked`; `…ledger_reconciles_item_by_item…` ← `duplicate_job_unchecked`, `missing_usage_unchecked`, `usd_usage_accepted`, `held_hold_accepted`, `hold_count_unchecked`, `sum_unchecked`, `reserved_unchecked`; `…dataset_drill_pends_on_the_owner…` ← `client_problems_ignored`, `resume_exit_ignored`, `engine_target_skips_to_ledger`, `missing_ledger_passes`, `settlement_not_awaited`; `…protocol_file_states_the_numbers…` ← `criterion_drifts_from_protocol`, `matrix_drifts_from_protocol`; `…runner_error_is_a_recorded_failure…` ← `runner_error_escapes` |
| 2 | E4B.b | **The matrix as a declared protocol** (`E4B-protocol.md` §3-§5, held to `certify.CRITERIA`/`MATRIX` by a case): **`e4b.b.preconditions`** - no Next.js server of this repository (main checkout or a worktree) on the runner host; box: `E4B_WINDOW_OK=1`, the engine idle (`vllm:num_requests_running`+`waiting` = 0), every parity clip present (W4's `candidate.sh` preconditions). **`e4b.b.config-pin`** - the tree against the settings W3/W4/M4 declared (`certify.DECLARED`: engine-options digest, image, `ENGINE_MAX_NUM_SEQS`, encoder budget, profile, `PREPARATION_CONCURRENCY`, `MAX_PREPARING_JOBS`, `MAX_VIDEO_SECONDS`), the published release record against the pin, and on the box the deployed engine from `inventory.sh`'s `image_equals_pin` and `args` - "reject any optimization that invalidates earlier evidence". **`e4b.b.envelope`** - one open-loop `bench.py` run per rung (box 0.5/1/2 req/s × 120; local 4 × 12): platform failures < 1 %, no refusal within the applied cap, TTFT p95 of clips ≤ 30 s at ≤ 720p ≤ 6 s and end-to-end p95 per clip-minute ≤ 45 s (each ≥ 60 samples), and the **P-20 duration cap**: every clip past `engine_ceiling_s` (82 s = `decide.ceiling_s(16384)` from the pinned flags) refused at admission, no clip within 72 s refused; the supported rate is the highest rung climbing from the lowest. **`e4b.b.soak`** - half the supported rate for 4 h (local 2 req/s × 10 s), `/metrics` sampled: RSS and GPU growth within W4's 512/256 MiB, drift and unsettleable 0 at the end, last-third p50 ≤ 1.5× the first. **`e4b.b.overload`** - a 32-request burst from one key: something accepted, something refused, every refusal a 429 with a numeric Retry-After and an overload code, no 5xx. **`e4b.b.recovery`** - I3B's `rc*`/`bk*` drills, the `recovery/` half of the same backend run; box: `e4b.b.recovery-box`, the runbook drills | `…config_pin_names_every_setting…` ← `moved_setting_accepted`, `published_digest_read_from_the_record`, `unread_box_passes`; `…deployed_engine_is_judged_from_the_box_inventory` ← `image_pin_unchecked`, `missing_flags_accepted`, `extra_flags_accepted`; `…app_and_lab_servers…` ← `any_next_server_counts`, `a_shell_naming_next_counts`, `window_consent_unchecked`, `busy_engine_accepted`; `…envelope_rung_judges_the_duration_cap…` ← `within_cap_refusal_accepted`, `admitted_long_clip_accepted`, `over_ceiling_judged_as_failures`, `failure_rate_loosened`, `refusals_below_the_rate_accepted`, `tail_limit_ignored`, `short_class_unfiltered`, `short_class_any_resolution`; `…supported_rate_is_the_highest_rung…` ← `climb_skips_a_failed_rung`, `other_rungs_cap_ignored`, `unknown_is_pass`, `crashed_client_accepted`; `…soak_judges_memory…` ← `memory_growth_limit_ignored`, `unreconciled_end_accepted`, `latency_drift_ignored`, `thin_thirds_judged`; `…overload_refusals_are_429…` ← `refusal_without_retry_after_accepted`, `refusal_code_unchecked`, `overload_5xx_accepted`, `unreached_limit_accepted`; `…scrape_reads_the_series…` ← `gpu_total_read_as_used`; `…load_cells_run_the_declared_shapes…` ← `engine_target_overload_run`, `soak_at_the_full_rate` |
| 3 | E4B.c | **`research/plan/evidence/e/E4B-release-decision.md`** - the template, filled software-side: decision PENDING; the release hashes; the checklist per check and oracle; the coverage of measured limits/SLOs/cost/quality, each ⚠️ TO BE MEASURED with the cell that measures it; the remaining inputs and blocking findings B1/B2 with owners; failure and rollback triggers linked to I2B's rollout (R1-R4) and I3B's runbook sections; the operator runbook index. **`E4B-endpoint.md`**, generated by `endpoint_doc.py` from the code: the route table (the `@app.<method>` decorators of the modules the cutover mounts, constants resolved), models, R94's mode rule, parameters, limits (`DEFAULTS` and the validator's bounds), the error catalogue (`HTTP_ERRORS`, messages, `RETRY_AFTER_CODES`, stream codes), terminal causes / cancel causes / billing, usage certainty and settlement, headers, wire shapes, and headless curl examples (sync, SSE, `POST /v1/jobs` + status/result/events/DELETE, `Prefer: respond-async`, uploads, R94's 409) | `…endpoint_doc_is_what_the_code_generates` ← `committed_doc_edited_by_hand`; `…regeneration_keeps_the_verification_log` ← `regeneration_drops_the_log`; `…every_mounted_route_has_one_description…` ← `delete_routes_unread`, `route_constant_unresolved`, `uploads_module_undocumented`; `…error_catalogue_is_complete…` ← `retry_after_column_blank`, `an_error_code_dropped`, `stream_codes_dropped`; `…examples_call_only_mounted_routes…` ← `async_example_without_a_key`, `example_on_an_unmounted_path`, `key_on_the_command_line`, `r94_example_dropped`; `…release_decision_links_resolve…` ← `decision_links_a_missing_section` |
| 4 | - | this report | - |

## Requirement coverage

| Oracle | Check (report entry) | Local (this lane) | Needs |
|---|---|---|---|
| BACKEND-JOURNEY | `e4b.a.protocol` (the gate's journeys, drills, schema backstops, provisioning), `e4b.a.dataset-resume` | the suite ran at layer 0 only (no stack, below); dataset client half PASSES the drill, ledger half PENDING[BOX] | the E2 stack (blocked, below); the metered endpoint (G2-R1) and D5's adapters on the box |
| BACKEND-DEPLOY | `e4b.b.preconditions`, `e4b.b.config-pin` | both FAIL on measured findings (App/Lab running on the dev host; B1) | the box inventory (B2) |
| BACKEND-OBSERVE | `e4b.b.soak` (`/metrics` growth, reconciler drift) | PENDING[BOX]: the fake engine publishes no `/metrics` | the gateway's `/metrics` on the box |
| PERF-ENVELOPE | `e4b.b.envelope`, `e4b.b.soak`, `e4b.b.overload` | runner proved end to end on the fake engine; every latency row unknown by design (12 samples) | the box |
| ENGINE-OPT | `e4b.b.config-pin` (W4 pin), the envelope's P-20 cap | tree matches W3/W4 except the published release (B1) | W4 phase B, the box inventory |
| MEDIA-OPT | `e4b.b.config-pin` (M4's `PREPARATION_CONCURRENCY` 2, profile v1) | matches | - |
| OPS-RECOVER | `e4b.b.recovery` (I3B `rc*`/`bk*`), `e4b.b.recovery-box` | not run (the stack run is blocked, below) | the E2 stack; the box drills |
| MARLIN-SOP | `e4b.a.sop-parity`, `e4b.a.dataset-resume` | parity PASS on the fake engine (9 clips, labelled); resume drill's client half holds | W4's E0 baseline on the box; the metered ledger |

## Environment

| Item | Value |
|---|---|
| Host | `Linux 7.0.0-1010-aws x86_64`, the shared dev host (other lanes and the coordinator ran concurrently); classification **local** |
| Python / deps | 3.12.3, `UV_OFFLINE=1 make api-env` (`uv sync --frozen --all-extras`, exit 0) |
| Docker | 29.6.2; this lane started **no container** (see the stack run below) |
| Fake engine | `tests/integration/fake_vllm.py` on `127.0.0.1:55580` (the e2 block's port), started and stopped by the runner |
| Corpus cache | `<main checkout>/.claude/corpus-cache` (bench's default), W4's 9 parity clips present (`parity.py --check`: `present=9 missing=[]`) |
| Credentials | none: the local target reads a fixed placeholder key; no secret was used or printed |

## Commands and results

UTC 2026-09-23. `$SC` is the session scratch area (not committed). Tails quoted.

| # | Command | At | Exit | Tail |
|---|---|---|---|---|
| 1 | `git log --oneline -3`, `git status`, `UV_OFFLINE=1 make api-env` | `7c52627` | 0 | clean tree; venv synced |
| 2 | `INFRX_MUTANTS=all apps/infrx-api/.venv/bin/python -m pytest -q -p no:cacheprovider tests/integration/backend/test_e4b_mutants.py` (the `api-mutants` line's new command) | `16904a0` content | **0** | **`79 passed in 182.84s (0:03:02)`** = 77 mutants killed + the 2 list checks (`mutants-final.log`) |
| 3 | layer 0 from the repo root: `apps/infrx-api/.venv/bin/python -m pytest -q -p no:cacheprovider -rfEs tests/integration` (namespace unset = e2) | `9aa7ffe` | **0** | **`184 passed, 99 skipped, 2 warnings in 60.63s`**; the skips are the stack-backed cases without a stack (`no infrx-e2 stack…`) and the pending journeys/dr11 (`PENDING[D5,G2-R1]`, `PENDING[G2-R1]`) (`layer0.log`) |
| 4 | `cd apps/infrx-api && .venv/bin/python -m pytest -q -p no:cacheprovider -rfEs tests/i` | `6c437a6` (no `apps/` change since) | **0** | **`143 passed in 154.79s`** (`tests-i.log`) |
| 5 | `cd apps/infrx-api && .venv/bin/python -m pytest -q -p no:cacheprovider -rfEs tests/contracts --ignore=tests/contracts/v2/test_v1_projection_pg.py` | `6c437a6` | **0** | **`1053 passed in 89.98s`**. The one ignored file runs D's pgharness: unset it takes the shared D1 port 55432 (forbidden), and `INFRX_D_TASK=d2` meets `infrx-d2-postgres` on 55433, **up 15 h and labelled with the `infrx-impl` checkout** - not this lane's, so the harness would refuse it as foreign (`tests-contracts.log`) |
| 6 | `make bench-test` | `6c437a6` (`models/marlin2b` changed since only by the protocol's log) | **0** | **`67 passed in 7.63s`** |
| 7 | the runner without the stack, detached: `INFRX_E2_NAMESPACE=e2 apps/infrx-api/.venv/bin/python tests/integration/backend/certify.py --no-stack --workdir $SC/nostack/work --report $SC/nostack/report.json` (20:40:51Z, 20.9 s) | `9aa7ffe` | **1** | the table below; **report sha256 `5c840cd3e222966b740be39e7517afe097d47dc5e9505319a523eaf6fc013a32`**, `git_head` = `git_head_end` = `9aa7ffe…`, both `dirty: false` |
| 8 | **the runner with the stack** (`certify.py --report …`, namespace e2) | - | **not run** | **stopped by the rule**: `docker ps -a \| grep infrx-e2-` showed **`infrx-e2-s3`**, created 20:04:16Z by another lane (label `ai.infrx.e2.checkout` = `.claude/worktrees/codex-objstore/tests/integration`), still up at 20:45Z. The lane's rule is "check it is empty first and STOP if not"; nothing of it was touched. The stack half (`e4b.a.protocol`, `e4b.b.recovery`) is the coordinator's to run when e2 is free: integration request 1 |

**The local report (command 7), quoted from `report.json`:**

| Entry | Status | Owners | Label | Detail (quoted) |
|---|---|---|---|---|
| `e4b.b.preconditions` | **FAIL** | - | - | `App/Lab running: pid 3907004 in /home/rey/workspace/rey/code/model-inference/apps/app (next-server (v16.3.5))`, and pid `3969240`, same directory: two `next-server` processes of this repository's App, running since 2026-09-20 (`ps`: elapsed 3-16:45). Not this lane's; not touched (integration request 2) |
| `e4b.b.config-pin` | **FAIL** | - | - | `published_engine_options_digest: 'sha256:4444…4444' is not the declared 'sha256:3c4bb…c036147' (R76/R78: …)`; `published_runtime_image: 'vllm/vllm-openai:nightly' is not the declared 'vllm/vllm-openai@sha256:4cbfd34a…20b42'` - finding **B1**. Every other declared setting matches the tree |
| `e4b.a.protocol`, `e4b.b.recovery` | SKIP | `STACK` | - | `--no-stack` (command 8) |
| `e4b.a.sop-parity` | PASS | - | fake-engine, not a measurement | `verdict pass, 9 clips` (parity.py twice on the same fake engine; candidate sha256 in the report) |
| `e4b.a.dataset-resume` | PENDING | `BOX` | fake-engine, not a measurement | client half holds: 12 items, the first run **SIGINTed** after 4 accepted (`exit 130`, 5 attempts: the fifth was in flight), the resume sent the other 8 (`exit 0`), no terminal item re-sent, every item terminal; the ledger half needs a metered endpoint |
| `e4b.b.envelope` | PENDING | `BOX` | fake-engine, not a measurement | rung 4.0 req/s: failures `0/11` (one attempt was a clip past the 82 s ceiling, the cap's), refusals `0`, TTFT p95 `None over 5 samples (needs 60)`, e2e p95 `None over 11`, duration cap `unknown` (an engine has no admission), client exit 0 |
| `e4b.b.soak` | PENDING | `BOX` | fake-engine, not a measurement | 2 req/s × 10 s: failures `0/19`, latency drift pass (`p50 0.0406 -> 0.0083`), memory and reconciliation `unknown` (no `/metrics` on the fake engine) |
| `e4b.b.overload` | PENDING | `BOX` | fake-engine, not a measurement | not run: an engine has no admission to refuse with |

## Findings

- **B1 (blocking cutover).** `infrx.operations.service.marlin_release` - what G6B publishes
  and every CREDIT admission pins (R76/R78) - carries `ENGINE_OPTIONS_DIGEST = "sha256:" +
  "44" * 32` and `RUNTIME_IMAGE_REF = "vllm/vllm-openai:nightly"` from
  `infrx/contracts/v2/fixtures.py`, although W3 has recorded both for real
  (`serving-version.json`: `sha256:3c4bb…` and the digest-pinned image). Accepted jobs would
  pin a serving revision whose digest is not the measured one. Measured by the config pin
  (command 7); integration request 3.
- **B2 (blocking BACKEND-READY).** The committed W3 inventory of the box
  (`research/plan/evidence/w/box/inventory-20260923T0319Z.txt`) runs `--max-num-seqs 32` and
  no `--allowed-local-media-path`; the pin is 8 and the media root. The case
  `…deployed_engine_is_judged_from_the_box_inventory` reads exactly that from the committed
  file. I2B's cutover keeps 32 on purpose (FC-1) until W4 phase B, so either the pin or the
  deploy is re-declared then.
- **The dev host runs this repository's App.** Two `next-server` processes from
  `<main checkout>/apps/app`, 3 days old, fail the App/Lab precondition on this host.
- **Two lanes, one namespace.** `codex-objstore` holds `infrx-e2-s3` in the e2 namespace this
  lane was assigned; the stack run could not happen (command 8).

## What needs the box

Every row of the release decision's §4, and in the report: `e4b.a.sop-parity` against W4's E0
file, `e4b.a.dataset-resume`'s ledger half, `e4b.b.envelope`/`soak`/`overload` on the
metered gateway, `e4b.b.config-pin`'s deployed half (`--inventory`), and the runbook drills
behind `e4b.b.recovery-box`. The protocol below is what the coordinator runs.

## The coordinator's box protocol (an executable checklist)

**Superseded by Round 2's box protocol below** (review F2/F3/N2): kept as written.

Run from the coordinator host, never from an implementation session. It reuses W3's SSM
helpers `wrap`, `ssm`, `out` verbatim ([W3-d8a7878.md](../w/W3-d8a7878.md), "The measurement
half") and W4's `candidate.sh` preconditions. **No secret in any command text**: the API key
and the database settings reach the runner through `--env-file` and the environment on the
box, never through `send-command`'s `commands` (it is kept in SSM history and CloudTrail).

**Gate before the window (all local, no AWS):**

- [ ] 0.1 The release SHA is an integration head that contains E4B, the released cutover
      (G2-R1: `gateway.app.ROUTERS` mounts the ingress, jobs and uploads), D5 and a wired
      `infrx.operations.cli.build_operations`. `git status` clean.
- [ ] 0.2 Dev-host report at that SHA, on this checkout's own namespace:
      `INFRX_E2_NAMESPACE=e2 apps/infrx-api/.venv/bin/python tests/integration/backend/certify.py --report dev-<sha>.json`
      (detached; ~10 min). Required: `git_head == git_head_end`, both `dirty: false`;
      `e4b.a.protocol`, `e4b.b.recovery` and `e4b.b.config-pin` PASS or PENDING only on
      `BOX`; no FAIL anywhere. B1 (the published serving revision) must be closed first.
- [ ] 0.3 `make check` green at that SHA (it now runs E4B's list: `make api-mutants`).
- [ ] 0.4 W4 phase B decided: `decide.py <E1> --baseline <E0>` adopted or rejected, the
      serving version re-declared if a flag changed (B2), `certify.DECLARED` equal to it, and
      P-20's `MAX_VIDEO_SECONDS` in the cutover configuration. Keep W4's E0 `parity.jsonl`:
      it is this run's parity baseline.
- [ ] 0.5 Log the window in the session record (runbooks README rule 1): purpose, expected
      duration (est. 5–6 h: the 4 h soak dominates), rollback (§6 of the release decision).

**In the window (box, one step at a time):**

- [ ] 1. The edge in maintenance, or no external traffic (`30-pause.sh`, or the pilot not yet
      public). The runner cannot stop new requests; `E4B_WINDOW_OK=1` is the operator's
      statement that none arrive.
- [ ] 2. The measurement checkout at the release SHA (W4 precondition 2):
      `/opt/dlami/nvme/w3-checkout`, updated by the project-bucket git bundle as W3's step 1.
- [ ] 3. Inventory of the deployed engine (W3):
      `ssm "$(wrap models/marlin2b/measure/inventory.sh 'CONTAINER=marlin2b-8000 ENGINE=http://127.0.0.1:8000 WEIGHTS=/opt/dlami/nvme/marlin2b')"`,
      its output saved on the box as `/opt/dlami/nvme/e4b/inventory.txt` (and fetched).
      With HF_TOKEN available, `wrap_hf` instead closes the registry-oid ⚠️.
- [ ] 4. A scoped consumer key for the certification tenant, issued with G6B's operator CLI
      (`issue-key --secret-file`, reveal-once, 0600) and placed on the box in a 0600 env file
      `/opt/dlami/nvme/e4b/key.env` (`INFRX_API_KEY=…`), written from the 0600 secret file
      through the rollout's secret route (never through `send-command` text); the tenant holds
      the individual 10,000 CREDIT grant. Copy W4's E0 `parity.jsonl`
      (`/opt/dlami/nvme/w4-e0-<utc>/parity.jsonl`) to `/opt/dlami/nvme/e4b/parity-e0.jsonl`.
- [ ] 5. The certification run (detached; ⚠️ unverified on the box: the runtime image carries
      the `infrx` dependencies and `httpx` - check `python -c 'import infrx, httpx'` in it
      first; `--user 0 --pid host` let the App/Lab scan read the host's processes; if the
      image has no `git`, the report's `git_head` reads null and step 7 records the SHA):
      ```bash
      # step: e4b-certify.sh (SSM), RELEASE=<sha>
      set -euo pipefail
      out=/opt/dlami/nvme/e4b/$(date -u +%Y%m%dT%H%M%SZ); mkdir -p "$out"
      nohup docker run --rm --network host --pid host --user 0 \
        -v /opt/dlami/nvme/w3-checkout:/repo:ro -v /opt/dlami/nvme/w3-corpus:/corpus:ro \
        -v /opt/dlami/nvme/e4b:/e4b -v "$out":/out -w /repo \
        --env-file /etc/marlin2b-gateway.env --env-file /opt/dlami/nvme/e4b/key.env \
        -e CORPUS_CACHE=/corpus -e E4B_WINDOW_OK=1 -e INFRX_CERTIFY_GATEWAY_IMAGE="$(docker inspect --format '{{.Image}}' infrx-gateway)" \
        "infrx-runtime:$RELEASE" python tests/integration/backend/certify.py --no-stack --box \
          --target http://127.0.0.1:8001/v1 --engine-url http://127.0.0.1:8000 \
          --metrics-url http://127.0.0.1:8001/metrics --inventory /e4b/inventory.txt \
          --parity-baseline /e4b/parity-e0.jsonl --workdir /out/work --report /out/report.json \
        > "$out/certify.log" 2>&1 &
      echo "out=$out"
      ```
      Poll `ssm "tail -c 4000 <out>/certify.log"` until the `exit N` line. The soak is 4 h.
- [ ] 6. The recovery drills the runbooks name, each logged with its UTC and outcome, in the
      order that leaves the box healthy: [restart.md — Engine](../../../../infra/runbooks/restart.md#engine)
      (this drill log is what closes the report's `e4b.b.recovery-box`), [Worker](../../../../infra/runbooks/restart.md#worker),
      [index-loss.md — Index loss](../../../../infra/runbooks/index-loss.md#index-loss),
      [restore.md — Box snapshot](../../../../infra/runbooks/restore.md#box-snapshot) (its
      steps B1-B2: the snapshot exists and is listed - the root-volume swap itself is R4's),
      [rollback.md — Rollout rollback](../../../../infra/runbooks/rollback.md#rollout-rollback)
      against the previous accepted release, then forward again. After each:
      [reconcile.md — Drift](../../../../infra/runbooks/reconcile.md#drift) must read 0.
- [ ] 7. Fetch `<out>/report.json`, `certify.log`, `work/` and `inventory.txt` to
      `models/marlin2b/results/E4B-box-<sha>/` (new evidence only); record the report's sha256,
      the release SHA and the drill log in the release decision; reopen the edge only if every
      step passed (`drain.sh resume`), otherwise §6's triggers.

The box report's non-PASS entries can only be `e4b.a.protocol`/`e4b.b.recovery` (SKIP
`STACK`: the dev-host report at the same SHA carries them) and `e4b.b.recovery-box` (PENDING
`BOX`: step 6's drill log closes it). Anything else is a FAIL of the candidate.

## Failure drill

| Injection | State before → after | Verdict |
|---|---|---|
| the dataset client SIGINTed mid-run (command 7, fake engine) | 12 scheduled items, 4 accepted and 1 in flight at the signal → the resume re-sends 8 with the same `sop1.<item_key>` keys and skips the 4 terminal ones | client half holds; the no-duplicate property on the server needs the metered endpoint (the fake engine does not dedupe) |
| a first run the signal never reached, a resume that exits 1, a debit that lands late, a USD usage row, a held hold, a Σ mismatch, an item accepted twice | synthetic rows and ledgers in `test_certify.py` | each is the FAIL (or the bounded wait) its case asserts, and each has a killed mutant |
| an engine refusing a clip the API admitted (P-20's presentation: 200, then a stream error) | synthetic rung rows | `duration_cap` FAIL: the cap must refuse it at admission |
| a burst answered 503, or 429 without Retry-After or with a non-overload code | synthetic rows | `e4b.b.overload` FAIL |
| the runner itself raising | `release_hashes` made to raise | a `runner-error` FAIL entry and the report still written, exit 1 |

## Artifacts

In the session scratch area (`$SC/e4b/`, not committed), sha256 prefix:
`nostack/report.json 5c840cd3e2229667` (command 7), `nostack/certify.log 4270fc1fff6328b0`,
`layer0.log d6adbdf0fa60b9f5`, `tests-i.log 5d49ccd48a808cab`,
`tests-contracts.log f974c2df7357e6f0`, `mutants-final.log 8776dc849120a823`. The client
and parity outputs of command 7 are under `nostack/work/` (bench raw files, `parity-*.jsonl`).

## Changes

`git diff --stat 7c52627..9aa7ffe`: 10 files, +3036, all new except the Makefile.

- `tests/integration/backend/`: `certify.py`, `endpoint_doc.py`, `e4b_mutants.py`,
  `test_certify.py`, `test_endpoint_doc.py`, `test_e4b_mutants.py` (owned).
- `models/marlin2b/results/E4B-protocol.md` (owned: new evidence only).
- `research/plan/evidence/e/`: `E4B-endpoint.md`, `E4B-release-decision.md`, this file (owned).
- **`Makefile`** (not an owned path; the brief asked for it): the `api-mutants` target gains a
  second command, `INFRX_MUTANTS=all $(API)/.venv/bin/python -m pytest -q -p no:cacheprovider
  tests/integration/backend/test_e4b_mutants.py`, run from the root because the list lives
  outside `apps/infrx-api` (a mixed argument list would lose `apps/infrx-api`'s pytest
  configuration). It adds ~3 min to `make check`.

No module code, contract, migration, fixture, composition root, lockfile or `tasks.json` was
changed. No existing test was edited. **Migrations: none. Rollback:** revert the commits.

The new cases under `tests/integration/backend/` are collected by the gate's backend stage
and by layer 0. They need no stack; `test_e4b_mutants.py` runs a 3-mutant subset by default
(~10 s), the whole list under `INFRX_MUTANTS=all`.

## Limits

1. **Nothing is measured.** Every number of this lane is the fake engine's and labelled so;
   no GPU, box, gateway or hosted service was used. BACKEND-READY is not claimed.
2. **The stack half did not run here** (command 8): `e4b.a.protocol` and `e4b.b.recovery`
   are unverified by this lane on this head. They are run.py's own stage functions, whose
   last full run is the phase-2 gate's and the coordinator's interim gate; this lane's code
   around them (the split, the verdict, the failure of a stack that did not come up) is
   covered by cases and mutants.
3. **The metered half is written against interfaces, not a deployment.** The dataset ledger
   reads G6B's `Operations.tenant(secret)` (`balance`, `usage`, `holds`) through
   `infrx.operations.cli.build_operations`, which refuses today (D5); the gateway cells read
   bench.py's raw rows. They are proven on synthetic rows and ledgers only.
4. **The box step is unverified** in two places, both stated in the protocol: that the
   runtime image carries every import the runner needs, and that the pilot env file plus the
   key file are what `build_operations` will read after D5.
5. **Criteria are provisional (P-18)**; the TTFT and per-clip-minute rows are 01 §2.3's
   proposal (01 and 07 disagree on 6 s vs 8 s; 01 owns the SLO), not targets.
6. **The runner does not start a local metered gateway.** A local target is always the
   engine; the E3B phase-3 journeys (in-process, on the E2 stack) are the local end-to-end
   proof, and this runner re-runs them through the backend stage.

## Integration requests

1. **Coordinator - the stack half on e2.** When `infrx-e2-*` is empty (today `codex-objstore`
   holds `infrx-e2-s3`), run at the integration head:
   `INFRX_E2_NAMESPACE=e2 apps/infrx-api/.venv/bin/python tests/integration/backend/certify.py --report <path>`
   (detached, ~10 min) and quote `e4b.a.protocol` / `e4b.b.recovery` beside command 7. Or
   give this lane a namespace block of its own (`harness.NAMESPACES` + `tasklocal.TASK_BLOCKS`,
   as E3B2's IR2-1 did).
2. **Coordinator / user - the dev host's App.** pids `3907004` and `3969240`
   (`next-server (v16.3.5)`, cwd `<main checkout>/apps/app`, running since 2026-09-20) fail the
   App/Lab precondition; stop them, or record that the dev host is exempt (the box has none).
3. **G6B / coordinator - B1.** The published serving revision must be the measured one:
   `infrx/contracts/v2/fixtures.py` `RUNTIME_IMAGE_REF` →
   `"vllm/vllm-openai@sha256:4cbfd34aac145fd1870381c030131c7f868fcad45448f401ecdb5fd4ed020b42"`
   (with the `runtime_image_digest` R76 allows once a digest exists) and
   `ENGINE_OPTIONS_DIGEST` → `"sha256:3c4bbface108e019b55a71121e1f3aaa23268bc1d1bd100257b0e2c68c036147"`,
   regenerated (`fixtures.py --write`, R77), plus one case tying both to
   `serving-version.json`. `e4b.b.config-pin` then passes its tree half. If W4 phase B changes
   a flag, all three move together.
4. **W4 phase B / coordinator - B2.** Decide `ENGINE_MAX_NUM_SEQS` (the pin 8 vs the box's 32)
   and deploy the pinned `serve.sh` (its `--allowed-local-media-path`); then re-declare
   `certify.DECLARED` in the same commit as the serving record.
5. **D5 / the cutover lane.** `infrx.operations.cli.build_operations()` composes the
   PostgreSQL adapters from the deployment's environment, so the certification tenant's
   `Operations.tenant(secret)` can be read on the box; `e4b.a.dataset-resume` pends on D5
   until then.
6. **Coordinator - the D ports this lane was given.** `infrx-d2-postgres` on 55433 belongs to
   the `infrx-impl` checkout (up 15 h), so `INFRX_D_TASK=d2` is not usable from this worktree;
   `tests/contracts/v2/test_v1_projection_pg.py` was therefore left out of command 5.

## Ruling candidates (proposed, not numbered; next free is R95)

- **The published serving revision is the measured one.** Once W3 records a digest-pinned
  runtime image and an engine-options digest, the serving revision a release publishes (and
  every admission pins, R76/R78) carries exactly those values; a placeholder digest or a
  moving tag fails E4B's config pin. R76's "absent while the runtime is a moving tag" no
  longer applies to Marlin.
- **A certification counts at one clean SHA.** A release decision cites a dev-host report and
  a box report whose `git_head` equals `git_head_end`, both clean, at the release SHA; a
  local-target number is labelled `fake-engine, not a measurement` and never fills a
  measured column.

## Handback

- **Head:** `9aa7ffe` + this report's commit.
- **Next unblocked:** the coordinator's stack run on e2 (request 1); B1's fixture change
  (request 3); after D5 and the cutover, the box protocol above.
- **Gate:** not passed, and not passable in software: BACKEND-READY needs the box.

## Verification log

- 2026-09-23: Authored at `9aa7ffe` from commands 1-8, each tail quoted from its log or from
  `report.json`. The stack run (command 8) was not started: another lane held the e2
  namespace, and the rule is to stop. This lane created, started or removed no container,
  touched no other lane's resource, and used no hosted project, AWS, pilot box, GPU or secret.

## Addendum - the stack run (command 9), after the e2 namespace cleared

`infrx-e2-s3` (the `codex-objstore` lane's) was gone at 20:56:32Z (`docker ps -a`, volumes and
networks: no `infrx-e2*`; ports 55500-55590 free). The full runner then ran once, detached,
on a clean tree at the evidence commit:

| # | Command | At | Exit | Tail |
|---|---|---|---|---|
| 9 | `INFRX_E2_NAMESPACE=e2 apps/infrx-api/.venv/bin/python tests/integration/backend/certify.py --workdir $SC/stack/work --report $SC/stack/report.json` (20:56:38Z → 20:59:02Z, 143.4 s) | `37da3b3` | **1** | the table below; **report sha256 `84643e5bce3cc35ed9884b4873c7a8c06f12c5b8f88679e1e9f1f5d453406ad3`** (log `5aa8ede4ed90529a`), `git_head` = `git_head_end` = `37da3b3…`, both `dirty: false`, `namespace "e2"` |

| Entry | Status | Owners | Detail (quoted from `report.json`) |
|---|---|---|---|
| `e4b.b.preconditions` | **FAIL** | - | the same two `next-server` pids as command 7 |
| `e4b.b.config-pin` | **FAIL** | - | B1, as command 7 |
| `preflight` / `services` / `migrate` | PASS | - | docker 29.6.2, the four digest-pinned images; `infrx-e2-{clickhouse,postgres,s3,valkey}`, PostgreSQL 17.6, Valkey 8.1.10; migrations by sha256 |
| `rls` | PASS | - | **`696` cases**, `failed: null`, roles anon/authenticated/postgres/service_role |
| `backend` | PENDING | - | `postgrest/13.0.4`; **`passed 154, pending 17, failed 0`**, `not_run: null`, `stale_pending: null`; pytest exit 0, `154 passed, 17 skipped` in 85.8 s; `pending_by_id {D5: 13, G2-R1: 12, I2B-R4: 1, M1-L2: 1}`; `detected` = E3B's db03-db11 (incl. db08b) and I3B's bk01b/bk01c/bk01d (13 live defects reported by their unchanged cases) |
| `backend-teardown` | PASS | - | `infrx-e3b-postgrest` removed |
| **`e4b.a.protocol`** | PENDING | `D5`, `G2-R1` | `passed 87, failed null, not_run null, pending_by_id {G2-R1: 11, D5: 12}` - the 9 journeys, the dataset-resume journey, dr11 and dr07/dr07c |
| **`e4b.b.recovery`** | PENDING | `D5`, `G2-R1`, `I2B-R4`, `M1-L2` | `passed 67, failed null, not_run null`; one case each: rc03 (`G2-R1`), rc04b (`D5`), rc08b (`I2B-R4`), rc05b (`M1-L2`) |
| `teardown` | PASS | - | the four `infrx-e2-*` removed, `still_named_ours_but_not_ours: []` |
| `e4b.a.sop-parity` | PASS | - | `fake-engine, not a measurement`; 9 clips |
| `e4b.a.dataset-resume` | PENDING | `BOX` | 12 items; SIGINT after 4 accepted (`exit 130`, 5 attempts), resume `exit 0` with 8 attempts; `client_problems: null` |
| `e4b.b.envelope` / `soak` / `overload` | PENDING | `BOX` | as command 7 (`0/11`, `0/19`, not run) |

**What this supersedes in the text above** (kept as written): command 8's "not run" and
Limits 2 - the stack half ran at `37da3b3` (the same code as `9aa7ffe`: `37da3b3` only adds
this report and the decision's column), with **no failed case**; the requirement-coverage
rows BACKEND-JOURNEY and OPS-RECOVER now read "PENDING on D5/G2-R1 (+ I2B-R4, M1-L2) with
0 failures"; **integration request 1 is withdrawn** (it stays true that two lanes were given
one namespace). The two FAILs of the local report are measured findings, not runner defects:
the dev host's App (request 2) and B1 (request 3). After the run, `docker ps -a | grep -c
'infrx-e2-\|infrx-e3b-postgrest'` → `0`.

- 2026-09-23 (addendum): command 9 quoted from `report.json` and `certify.log`; nothing
  earlier in this file was rewritten.

## Round 2 — review fix round (`E4B-review-37a4652.json`: 9 blocking, 5 nonblocking)

One commit per finding, each with its case and its mutants through this lane's list (the
shared R83 runner), on top of `37a4652`. No rebase, reset, amend, stash or push. Every death
line below was **re-derived at the head**: each mutant applied alone to a throwaway copy by the
shared runner's own `_copy`/`_prepare`, its named case run with `--tb=line` (51 mutants, every
death an `AssertionError` or `Failed` in a test file; `killtexts-final.tsv` sha256
`4f6d2eca03a92831`). Doing that found one real problem, fixed in `0158b7d` and listed under F2.

| Finding | Commit | Change | Killing case | Mutant → death line |
|---|---|---|---|---|
| **F1** | `36e4dd0` | `certify.identity_problems` + a `release-identity` entry added at `as_json`: FAIL when a SHA is missing, either end is dirty or of **unknown** state, or the tree moved. `run.git_head` records `dirty: None` when git cannot answer (E's e3bm47 re-anchored on the new line, still killed: `mutants.py --only e3bm47` → killed) | `test_e4b_a_report_counts_for_one_clean_known_tree_or_it_fails` | `missing_sha_accepted` → `test_certify.py:74: AssertionError: assert ['the tree at...nknown state'] == ['no git SHA ...nknown state']`<br>`dirty_tree_accepted` → `test_certify.py:71: AssertionError: assert [] == ['the tree at...art is dirty']`<br>`unknown_state_accepted` → `test_certify.py:72: AssertionError: assert [] == ['the tree at...nknown state']`<br>`moved_tree_accepted` → `test_certify.py:76: AssertionError: assert [] == ['the tree mo...dddddddddddd']`<br>`identity_never_fails` → `test_certify.py:83: AssertionError: assert ('release-identity', 'PASS') == ('release-identity', 'FAIL')`<br>`unknown_tree_recorded_clean` → `test_certify.py:90: AssertionError: assert {'dirty': False, 'sha': None} == {'dirty': None, 'sha': None}` |
| **F2** | `77eacbe, 0158b7d` | `run.git_head` catches `OSError` (no git binary: the runtime image), so `Report()` cannot crash and the report is written with the tree unknown (→ F1's FAIL). `--box` requires `--release-sha`, and the checkout's SHA must be it. `0158b7d` moves the two `--box` argument rules into one case that stops before any network: F3's rule had made F2's refusal case pass for the wrong reason (found by re-deriving each death line) | `test_e4b_a_host_without_git_writes_a_report_that_fails_its_identity; test_e4b_a_box_run_names_its_release_and_reads_its_metrics` | `missing_git_crashes` → `test_certify.py:838: Failed: no git crashed git_head: FileNotFoundError(2, 'No such file or directory')`<br>`release_sha_unchecked` → `test_certify.py:853: AssertionError: assert [] == ['the tree is...eeeeeeeeeeee']`<br>`box_without_release_sha_accepted` → `test_certify.py:980: Failed: DID NOT RAISE <class 'SystemExit'>` |
| **F3** | `b4d8182` | `--box` adds `e4b.b.served-build`: FAIL unless the gateway's `infrx_build_info` revision is the report's tree, and `INFRX_CERTIFY_GATEWAY_IMAGE` (the running gateway's image) equals `INFRX_CERTIFY_RELEASE_IMAGE` (`infrx-runtime:<release>`), both read with `docker image inspect`; `--box` requires `--metrics-url`. The refuters' correction is taken: nothing emits `infrx_build_info` today, so this check FAILs on any box until the gateway sets it (integration request 1) | `test_e4b_the_box_report_is_tied_to_the_build_the_gateway_serves; test_e4b_scrape_reads_the_series_the_soak_judges; test_e4b_a_box_run_names_its_release_and_reads_its_metrics` | `served_revision_unchecked` → `test_certify.py:865: assert [] == ['the gateway...000000000000']`<br>`missing_build_info_accepted` → `test_certify.py:869: assert 'publishes no infrx_build_info' in "the gateway serves None, the report's tree is 0123abc000000000000000000000000000000…`<br>`gateway_image_unset_accepted` → `test_certify.py:872: AssertionError: assert [] == ['INFRX_CERTI...s unrecorded']`<br>`release_image_mismatch_accepted` → `test_certify.py:877: AssertionError: assert [] == ['the gateway...111111111111']`<br>`build_info_read_from_any_series` → `test_certify.py:751: AssertionError: assert {'drift': 0.0...: 1024.0, ...} == {'drift': 0.0...: 1024.0, ...}`<br>`box_without_metrics_accepted` → `test_certify.py:980: Failed: DID NOT RAISE <class 'SystemExit'>` |
| **F4** | `4b01d4d` | `failures()`: every attempt with outcome `failed` counts (transport timeouts and resets with 5xx and broken streams; the platform-caused share stays in the detail); `answered()`: a rung or soak that accepted nothing fails and ends the envelope climb; under overload a reset is a failure | `test_e4b_an_unanswered_attempt_is_a_failure_whatever_its_cause` | `transport_failures_uncounted` → `test_certify.py:656: AssertionError: assert 'pass' == 'fail'`<br>`nothing_accepted_passes` → `test_certify.py:660: AssertionError: assert 'pass' == 'fail'`<br>`climb_ignores_answers` → `test_certify.py:668: AssertionError: assert ('FAIL', (), 0.5) == ('FAIL', (), None)`<br>`overload_resets_accepted` → `test_certify.py:35: AssertionError: no problem was reported` |
| **F5** | `9d73835` | `target_label`: `meas.` only for a `--box` run whose preconditions passed; the local target stays `fake-engine, not a measurement`; any other target is `unverified target, not a measurement` (`remote_target` starts there); the label is decided after the preconditions | `test_e4b_only_a_box_run_with_its_preconditions_met_is_a_measurement` | `local_run_labelled_measured` → `test_certify.py:898: AssertionError: assert 'meas.' == 'fake-engine,...a measurement'`<br>`remote_run_measured_by_default` → `test_certify.py:901: AssertionError: assert 'meas.' == 'unverified t...a measurement'`<br>`unready_box_measured` → `test_certify.py:903: AssertionError: assert 'meas.' == 'unverified t...a measurement'`<br>`label_never_decided` → `test_certify.py:922: AssertionError: ('PASS', {'label': 'unverified target, not a measurement', 'reported': 'unverified target, not a measurement'})` |
| **F6** | `f34b2cc` | `declared()` reads the W3/W4 values from `serving-version.json` (digest, `runtime_image.ref`, `ENGINE_MAX_NUM_SEQS`, the flag-derived encoder budget); the published release must be that record; `serve_sh_pins()` makes `serve.sh` the second source held against it. No literal, so W4 phase B's digest change moves `declared()` with the record | `test_e4b_the_declared_settings_are_the_serving_record_read_never_typed` | `published_digest_declared_as_the_placeholder` → `test_certify.py:476: AssertionError: assert ('sha256:4444...5fd4ed020b42') == ('sha256:3c4b...5fd4ed020b42')`<br>`published_image_declared_as_the_tag` → `test_certify.py:476: AssertionError: assert ('sha256:3c4b...enai:nightly') == ('sha256:3c4b...5fd4ed020b42')`<br>`seqs_declared_as_a_literal` → `test_certify.py:471: AssertionError: assert ('sha256:3c4b...b42', '32', 8) == ('sha256:3c4b...0b42', '8', 8)`<br>`digest_declared_as_a_literal` → `test_certify.py:482: AssertionError: assert ('sha256:3c4b...55555', 32768) == ('sha256:5555...55555', 32768)`<br>`encoder_budget_declared_as_a_literal` → `test_certify.py:482: AssertionError: assert ('sha256:5555...55555', 16384) == ('sha256:5555...55555', 32768)`<br>`image_read_from_the_record_not_serve_sh` → `test_certify.py:499: AssertionError: ['engine_max_num_seqs', 'published_engine_options_digest', 'published_runtime_image']`<br>`seqs_read_from_the_record_not_serve_sh` → `test_certify.py:499: AssertionError: ['runtime_image', 'published_engine_options_digest', 'published_runtime_image']` |
| **F7** | `63876db` | `next_servers`: a Next.js process in an `apps/app` or `apps/lab` package of **any** checkout, by working directory or command line; an unreadable working directory counts (unknown is not stopped); `repo_roots` deleted | `test_e4b_app_and_lab_servers_of_this_repository_fail_the_preconditions` | `unreadable_cwd_skipped` → `test_certify.py:560: assert [11, 12, 17, 18] == [11, 12, 16, 17, 18]`<br>`package_path_in_cmdline_ignored` → `test_certify.py:560: assert [11, 12, 16, 18] == [11, 12, 16, 17, 18]`<br>`only_this_checkout_counts` → `test_certify.py:560: assert [16] == [11, 12, 16, 17, 18]`<br>`any_next_server_counts` → `test_certify.py:560: assert [11, 12, 13, 16, 17, 18] == [11, 12, 16, 17, 18]` |
| **F8** | `1ffdc86` | one assertion per stated rule: `--retries 0` and `--subset full` at the box scale; a quarantined 4xx item is never re-sent; an accepted item without an Inference-Id is unreconcilable; a signal answered with exit 0 is no interruption; an unreadable engine is not idle | `test_e4b_each_stated_client_rule_holds_one_assertion_each` | `retries_hide_refusals` → `test_certify.py:939: AssertionError: assert '3' == '0'`<br>`box_runs_the_fast_subset` → `test_certify.py:940: AssertionError: box`<br>`quarantined_item_resent_ok` → `test_certify.py:942: assert [] == ["terminal it...sume: ['i7']"]`<br>`accepted_without_id_ok` → `test_certify.py:946: assert 'Σ charged 2....dger fall 2.5' == "items accept...e-Id): ['i5']"`<br>`signalled_exit_unchecked` → `test_certify.py:958: AssertionError: assert 'PENDING' == 'FAIL'`<br>`unreadable_engine_is_idle` → `test_certify.py:966: AssertionError: assert 'A' == 'the engine i...iting = None)'` |
| **F9** | `8cb994c` | the endpoint doc's cited statuses come from `errors.http_status` (descriptions and examples; a case checks every citation, and the success statuses against the modules), DELETE's cause from the jobs module's call, the no-key routes from the modules' sources; `Location` joins the Headers list; the `POST /v1/jobs` streaming refusal is documented; the committed doc regenerated | `test_e4b_every_status_the_prose_cites_is_the_one_the_code_answers; test_e4b_the_prose_names_the_cause_the_auth_and_the_headers_the_modules_implement; test_e4b_the_examples_call_only_mounted_routes_with_the_headers_the_contract_needs` | `description_status_hand_typed` → `test_endpoint_doc.py:126: AssertionError: [('result_pending', '404'), ('result_expired', '410'), ('invalid_request', '400'), ('idempotency_conflict…`<br>`example_status_hand_typed` → `test_endpoint_doc.py:126: AssertionError: [('result_pending', '409'), ('result_expired', '410'), ('invalid_request', '400'), ('idempotency_conflict…`<br>`cancel_cause_hand_typed` → `test_endpoint_doc.py:141: AssertionError: assert ('`client_cancelled`' in 'cancel (`sync_deadline`), answering the committed outcome')`<br>`models_listed_as_authenticated` → `test_endpoint_doc.py:142: AssertionError: assert [] == ['/v1/models']`<br>`location_header_dropped` → `test_endpoint_doc.py:146: AssertionError: assert '`Location`' in '\n\n`Authorization`, `Idempotency-Key`, `Idempotency-Replayed`, `Inference-Id`, `…`<br>`stream_refusal_dropped` → `test_endpoint_doc.py:148: assert '`POST /v1/jobs` is always async: a body with `"stream": true` is refused `invalid_request` (400) with `param` `st…`<br>`r94_example_dropped` → `test_endpoint_doc.py:86: AssertionError: the R94 cross-mode rule is shown` |
| **N1** | `e1a4eef` | `attributed_exit`: the halves are judged with pytest's own exit code (1 is the failed case the split attributes; any other code fails both); a backend suite that never ran is `backend=not run` | `test_e4b_the_suite_halves_carry_pytests_own_exit_code` | `exit_code_ignored` → `test_certify.py:195: AssertionError: assert 'PASS' == 'FAIL'`<br>`exit_one_never_attributed` → `test_certify.py:183: AssertionError: assert 1 == 0`<br>`unexplained_exit_one_accepted` → `test_certify.py:184: AssertionError: assert 0 == 1`<br>`unrun_backend_accepted` → `test_certify.py:197: AssertionError: assert None == ['backend=not run']` |

**Nonblocking.**
- **N1:** fixed (row above).
- **N2:** fixed in the box protocol below. `E4B_WINDOW_OK` is no longer hard-coded; it
  comes from the window record that step 1 writes.
- **N3 (B1):** routed to the cutover lane (the fixture placeholder), as the review says.
  `e4b.b.config-pin` keeps failing on it until that lands, and F6 reads the record so the
  pin follows the fix.
- **N4 (B2):** routed to the box (W4 phase B decides the value; the rollout deploys it).
- **N5, noted:** with D5's branch merged, layer 0 fails
  `test_harness::test_the_migration_set_is_the_console_one…` on `0018_terminal_settlement.sql`.
  That is D5's (the pinned migration list; the phase-3 lane owns the merge), not E4B's. D5
  does not change `cli.build_operations`, which still refuses. The owner of that wiring is
  named in request 4 below.

**What changed for the list:** 126 mutants over 37 named cases (77 before). Three existing
mutants were re-anchored on lines this round changed:
- `moved_setting_accepted` (F6, `pinned` for `declared`);
- `any_next_server_counts` (F7);
- `r94_example_dropped` (F9).

E's `e3bm47` was re-anchored in `tests/integration/mutants.py` (F1).

### Round 2's box protocol (supersedes the one above)

Changes from the first version:
- the runner runs in an image **with git**, so `release-identity` can be computed on the box;
- `--release-sha` names the release;
- `--metrics-url` reads the served build;
- both image ids come from `docker`;
- `E4B_WINDOW_OK` comes from the window record (N2).

- [ ] 0.1-0.5 as above, and additionally:
  - 0.6 the gateway publishes `infrx_build_info{revision}` (request 1). Without it,
    `e4b.b.served-build` FAILs by design.
  - 0.7 once per release, outside the window, build the certify image (the runtime image
    has no git; review F2):
    `printf 'FROM infrx-runtime:%s\nUSER 0\nRUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*\n' "$RELEASE" | docker build -t "infrx-certify:$RELEASE" -`.
    ⚠️ Unverified: the box's Debian mirror access; the runtime image's imports for the runner.
- [ ] 1. The window opens (`30-pause.sh`). Only when its output shows the maintenance site,
      write the window record: `umask 077; mkdir -p /opt/dlami/nvme/e4b;
      printf 'E4B_WINDOW_OK=1\nE4B_WINDOW_OPENED=%s\n' "$(date -u +%FT%TZ)" > /opt/dlami/nvme/e4b/window.env`.
      Remove it when the window closes (step 7).
- [ ] 2-4 as above.
- [ ] 5. The certification run (detached):
      ```bash
      # step: e4b-certify.sh (SSM), RELEASE=<sha>
      set -euo pipefail
      out=/opt/dlami/nvme/e4b/$(date -u +%Y%m%dT%H%M%SZ); mkdir -p "$out"
      test -s /opt/dlami/nvme/e4b/window.env || { echo "no window record: step 1 first"; exit 2; }
      gateway=$(docker inspect --format '{{.Image}}' infrx-gateway)
      release=$(docker image inspect --format '{{.Id}}' "infrx-runtime:$RELEASE")
      nohup docker run --rm --network host --pid host --user 0 \
        -v /opt/dlami/nvme/w3-checkout:/repo:ro -v /opt/dlami/nvme/w3-corpus:/corpus:ro \
        -v /opt/dlami/nvme/e4b:/e4b -v "$out":/out -w /repo \
        -e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory -e GIT_CONFIG_VALUE_0=/repo \
        --env-file /etc/marlin2b-gateway.env --env-file /opt/dlami/nvme/e4b/key.env \
        --env-file /opt/dlami/nvme/e4b/window.env \
        -e CORPUS_CACHE=/corpus -e INFRX_CERTIFY_GATEWAY_IMAGE="$gateway" \
        -e INFRX_CERTIFY_RELEASE_IMAGE="$release" \
        "infrx-certify:$RELEASE" python tests/integration/backend/certify.py --no-stack --box \
          --release-sha "$RELEASE" \
          --target http://127.0.0.1:8001/v1 --engine-url http://127.0.0.1:8000 \
          --metrics-url http://127.0.0.1:8001/metrics --inventory /e4b/inventory.txt \
          --parity-baseline /e4b/parity-e0.jsonl --workdir /out/work --report /out/report.json \
        > "$out/certify.log" 2>&1 &
      echo "out=$out"
      ```
      Inside the container the host's `/proc/<pid>/cwd` is usually unreadable. A Next.js
      process on the box therefore counts as a running App/Lab (F7: unknown is not stopped).
      The box runs none today.
- [ ] 6. as above.
- [ ] 7. as above. The report must show `release-identity` and `e4b.b.served-build` PASS,
      every number labelled `meas.`, and only the typed SKIPs/PENDINGs of the first
      version. Remove `window.env` when the window closes.

### Integration requests after round 2 (replacing the list above; 1 of the first list stays withdrawn)

1. **Coordinator / I3B - `infrx_build_info`.** The gateway (and the worker) must set
   `infrx_build_info{process, revision=<release sha>} 1` at startup. It is declared in
   `infrx/observe/metrics.py:153` and on the dashboard, and emitted by nobody (I3B request 1).
   Until it is set, `e4b.b.served-build` FAILs on every box run, by design.
2. **Coordinator / user - the dev host's App.** Unchanged (pids `3907004`, `3969240`).
3. **Cutover lane - B1.** In progress per the review. After it lands, the tree half of
   `e4b.b.config-pin` passes: `declared()` reads the record, so no E4B change is needed.
4. **The `build_operations` wiring.** `infrx.operations.cli.build_operations` still refuses
   after D5 (review N5). The owner is the coordinator's cutover lane, not D5 alone. Until it is
   wired, `e4b.a.dataset-resume` pends on a metered target.
5. **W4 phase B / the box - B2.** Unchanged. A new digest or flag lands in
   `serving-version.json` and `serve.sh` together; the pin follows the record.
6. **Coordinator - ports.** Unchanged.
