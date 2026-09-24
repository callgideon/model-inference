# S3 — Consumer v1 baseline reconciliation (2026-09-24)

Task S3 ([brief](../../consumer-v1/03-operations-and-verification.md), [program 22](../../22-consumer-v1-implementation.md)). Lane `codex/s3-reconcile`, base **`dff31efc`** (= `origin/main`, the wave baseline of the session-03 record `2026-09-24-session-03.md` on `claude/consumer-v1`). Read-only over the tree. This lane did **not** touch the box, the hosted database, AWS or the network. Every box or hosted value below is **recorded by** a named source. None of it was verified now. Two local checks were **re-run now** on this Linux host: the audit's seam probes and the RV-12 tests (§7).

**This record does not certify anything.** Nothing here makes BACKEND-READY, E4B or E1B accepted. Certification run3 is still in progress and none of its later cells are assumed.

## 0. Findings that change what lanes do

1. **The code at `dff31efc` is the deployed code.** `git diff bda1586..dff31efc -- apps infra models tests Makefile` shows only E1B result files and `models/marlin2b/smoke.py`, a direct-vLLM utility that is not in the runtime image path. So every code check in §2 also describes the running release.
2. **All twelve RV findings are still open at `dff31efc`.** None is fixed with evidence and none is superseded. The four 2026-09-24 lanes (CERTIFY-TREE `4db74b6`, TOKCOST `95fbb90`, INTAKE-DRAIN `a163953`, CERTIFY-POLISH `bda1586`) changed the certify runner, `bench.py` (R106 cancelled replay), the preparation count memo and the intake drain. None of them changed an RV seam; §2 gives the per-RV detail.
3. **The run3 `dataset-resume` FAIL is a regime mismatch, not a runtime defect.** The protocol pre-registers the ledger half as CREDIT-only, and the pilot runs `legacy_usd`. E4C requires CREDIT mode anyway. The same code path shows that the cell's **client half passed** on `bda1586`, so R106 now holds live (§2.2).
4. **The run3 soak cannot PASS at `bda1586`, whatever it measures.**
   - `observe.metrics.record_reconciliation` (`apps/infrx-api/infrx/observe/metrics.py:368`) has no runtime caller; only test code calls it. So `infrx_reconciliation_drift` and `infrx_unsettleable_jobs` are never published.
   - As a result, `soak_verdicts`' `reconciled_at_end` row (`certify.py:1150`) is always UNKNOWN.
   - The same gap means the ReconciliationDrift and UnsettleableJobs alerts can never fire in production. This is an RV-09/I8 item.
5. **The committed E1B `bda1586` cells never exercised the intake drain.**
   - The raw files contain **zero 429s**: 114 × 200 and 6 × 400 `unsupported_media` per cell (§7 command 3). `LARGE_BODY_LIMIT=8` admitted everything, so no mid-body refusal happened.
   - What these cells prove: no transport loss at 0.5/s, 1.0/s and burst 8 on that mix at limit 8.
   - What they do not prove: that a drained 429 reaches a client through Caddy. That stays ⚠️ TO BE MEASURED.
   - The run3 `overload` cell (32-burst sent to `127.0.0.1:8001`, bypassing Caddy) is the first live exercise of the drain. It is pending.
6. **Transaction pooling is not a drop-in switch for the runtime.**
   - `gateway/pilot.py:129-135` (`configure_connection`) and `state/jobstore.py:43-53` (`connector`) apply `set role service_role` and `set statement_timeout` as session state on autocommit connections. A transaction pooler (port 6543) does not keep session state across transactions.
   - Today the runtime login is `postgres` with `rolbypassrls` (D-31), which hides the role loss. The statement timeout would be lost silently.
   - The certify runner's ledger reads on 6543 open a fresh connection per operation. They therefore never reach psycopg's prepare threshold and say nothing about prepared-statement safety.
7. **The only rollback target on the box is not known-good.** The install backup `…T200647…-bda15866…` holds the **27af05a** runtime, which has no preparation loop and cannot complete jobs (session 02, "PILOT RELEASE 27af05a DEPLOYED … Known limit"). The 4226315 runtime is recoverable only by reinstalling its bundle with W10 (RV-10).
8. **The "no external traffic" premise needs a key inventory.** Session 02 recorded "active keys: consumer 2 (pre-cutover), operator 1" before it issued `142c7d81…` (S02 "LOGGED OP … operator key"). Whether those two legacy consumer keys are still active, and who holds them, is ⚠️ TO BE VERIFIED by a read-only coordinator op. The box `E4B_WINDOW_OK=1` rule assumes no external consumer key exists.
9. **Durable seams already exist on hosted and are unwired. D10/M5/G7 should wire them, not recreate them.**
   - `0010_media_uploads.sql` creates `infrx.media_uploads`. No Python adapter uses it (grep over `apps/infrx-api/infrx` finds nothing).
   - `jobs.result_expires_at` exists (`0003:175`) and is set by `0018:436`. `wire.JobStatus.result_expires_at` exists (`contracts/wire.py:150`). Only `TerminalOutcome` (`contracts/records.py:613`) lacks it (D-22), so the route recomputes it (`gateway/routes/jobs.py:156`).
10. **The processor identity has no digest.** `models/marlin2b/serving-version.json` pins the shards, tokenizer, chat template, config and generation config. `processor_config.json` and `preprocessor_config.json` have no served-bytes digest; the processor is identified only by class and profile v1 in `research/workloads/marlin-sop.md` §1.3. E4C's freeze list needs one.
11. **Two provisional card identities exist.**
    - Hosted seed: `rc_marlin2b_2026_09_provisional` (`seed_marlin_provisional.sql`; the v2 fixtures).
    - G6B `marlin_release`: `rc_marlin2b_<effective-ts>_provisional_p01` (`operations/service.py:442`). This id appears in the certify report's `published_release`.
    - Neither card is approved (P-01). F2C.c and G8 must name one public card identity.
12. **Record slip.** Session 02's last entry says the third run3 launch started "(21:29Z box clock)". The output directory is `20260924T202924Z`, and 609 soak rows at 0.25/s by 21:26Z imply a soak start near 20:46Z. So the launch was about 20:29Z (§6.6 proposes the correction line).

## 1. Identity table

| Axis | Value | Source |
|---|---|---|
| Source `main` | `dff31efc` (three docs commits over `726d004d`) | recorded by session 03 Baseline; `git log` here |
| Integration branch | `claude/consumer-v1` from `dff31efc`; head `98bfdfa3` (session-03 record only) | recorded by session 03 |
| This lane | `codex/s3-reconcile` from `dff31efc` | here |
| Deployed release | `bda15866e5700f3856d7142580da842fba9bbd23` (third install, W10 2026-09-24 ~20:06Z box clock) | recorded by session 02 "THIRD RELEASE … DEPLOYED"; coordinator SSM `3588e56e` (21:26Z box clock) |
| Deployed code vs source | identical in `apps/ infra/ tests/ Makefile`; `models/` differs only by E1B results and `smoke.py` | computed here (`git diff --stat bda1586 dff31efc`) |
| Runtime image | `infrx-runtime:bda1586…` = `sha256:cc2a80c9396f6ebec8cd151770a0b8f221a306a56364f2562f90afd82a1cbebb` | recorded by session 02 W10/W13; handoff 20 §14.1 |
| Engine image | `vllm/vllm-openai@sha256:4cbfd34aac145fd1870381c030131c7f868fcad45448f401ecdb5fd4ed020b42` ("engine digest unchanged") | recorded by session 02 W13; `serving-version.json` |
| Engine options | digest `sha256:3c4bbface108e019b55a71121e1f3aaa23268bc1d1bd100257b0e2c68c036147`. `serve.sh` sha256 `9e1f473b…`; `serving-version.json` sha256 `6b1024b6…` (**unchanged** at `dff31efc`, computed here). run3 config-pin ok | recorded by the run2 report `hashes`; coordinator (run3 config-pin ok) |
| Model | `NemoStation/Marlin-2B@fd111fca4fc7897876fb0d7e9df22ca5ac8ab965`. Shards `sha256:5d78fa4d…`, `sha256:01d40ec9…`; tokenizer `sha256:06b95093…`; chat template `sha256:273d8e0e…`; config `sha256:1325d779…`; generation config `sha256:0d54a28c…` (served bytes, 2026-09-23). Registry oid equality ⚠️ TO BE VERIFIED (`HF_TOKEN`, D-17) | recorded by `serving-version.json` (W3 inventory) |
| Processor | `Qwen3VLProcessor` / `Qwen3VLVideoProcessor`, preprocessing profile v1 (2 fps, 4–240 frames). **No digest recorded** (finding 10) | recorded by `research/workloads/marlin-sop.md` §1.3 (S2M, P-06) |
| Certify image | `infrx-certify:bda1586…` (runtime image + git + `safe.directory`). Image id **not recorded in the tree** (the 4226315 one is `sha256:4e48efa3…`) | recorded by session 02 "E1B ACCEPTANCE … run3"; handoff 20 §10.2 |
| Migrations | 0001–0018 applied hosted; hosted migration digest `4524cbc02ef70e080f65999d8c797f4c25831985107e9750afb4d261069db181` unchanged (`MIGRATION_DIGEST=nothing-pending`). The certify tree hash of `migrations/*.sql` at 4226315 is `eeb1645c…` (a different hash function over the same files) | recorded by session 02 W10/W13; run2 `hashes.migrations` |
| Regime / flags | `INFRX_MODE=pilot`, `ACCOUNTING_REGIME=legacy_usd`; `credit_admission` false, `legacy_usd_admission` true, `signup_grant` true | recorded by handoff 20 §14.1; coordinator 21:26Z |
| Effective limits (env) | `MAX_VIDEO_SECONDS=82`, `ENGINE_MAX_NUM_SEQS=8`, `WORKER_CONCURRENCY=8`, `LARGE_BODY_LIMIT=8`, `S3_MEDIA_BUCKET=llm-bootcamp-641134885443`, `PROCESSING_CACHE_DIR=/opt/dlami/nvme/processing` | recorded by session 02 post-install `1944e50c`; coordinator SSM `3588e56e` |
| Limits left at code defaults (box value unread) | `DATABASE_POOL_MAX_SIZE` default 10 per process pool (`config.py:330`; gateway and worker each open one); `MAX_MEDIA_BYTES` 64 MiB; `PREPARATION_CONCURRENCY` 2; edge `request_body max_size 96MiB` | code at `dff31efc`; handoff 20 §14.1 |
| Pooler | session port 5432 (15 slots) exhausted by the runtime pools (`EMAXCONNSESSION`); operator reads and the certify runner use transaction port 6543 | recorded by session 02 "Hosted read-only drift check …"; coordinator |
| Snapshots | `snap-09b047db6284df302` (pre-bda1586 window), `snap-0c3f41caccbffbfcf` (pre-27af05a), `snap-08732d3ac6376e850` (pre-wave-3) | recorded by session 02 W2; handoff 20 §14.1 |
| Install backups (box, root-only, contain secrets) | `/var/backups/infrx/20260924T200647.467964697Z-bda15866…` (holds the **27af05a** runtime); `…T100129.620865008Z-4226315…` (holds 27af05a); `…T054001…-27af05a8…`; `…T051144…-27af05a8…` | recorded by session 02 THIRD RELEASE; handoff 20 §14.1 |
| Hosted DB backup | only a pre-first-install dump on the coordinator host `/home/rey/infrx-backups/hosted-20260924T050746Z` (W6). Hosted backup/PITR policy ⚠️ TO BE VERIFIED ("the hosted project has no backup today", `infra/runbooks/README.md:10`) | recorded by session 02 "LOGGED OP: the operator seed"; runbooks README |
| Edge | Caddyfile saved `/opt/dlami/nvme/w4-logs/Caddyfile.live-20260924T194645Z` sha256 `31df273c…` | recorded by session 02 W4 |
| Traffic | App `app.callbill.ai`: login answers "Accounts are created by invitation"; public signup disabled. Lab not deployed. One verified individual tenant; active consumer key `142c7d81…`; revoked `0fdbb31f…`; two pre-cutover consumer keys ⚠️ (finding 8); operator key `d554db80…`. **Certification run3 in progress** (§3) | recorded by review 21 §2 (20:26Z observation); handoff 20 §10.6; coordinator |
| Owned resources: box | run outputs `/opt/dlami/nvme/e4b/{20260924T165408Z,20260924T172244Z,20260924T202739Z (two aborted launches),20260924T202924Z (run3)}`; `/opt/dlami/nvme/e4b/key.env` (0600, from SSM `/model-inference/e4b_api_key`), `parity-e0.jsonl`, `inventory.txt`; certify images `infrx-certify:4226315…`, `:bda1586…`; corpus `/opt/dlami/nvme/w3-corpus`; measurement checkout `/opt/dlami/nvme/w3-checkout` (at 4226315); bundles `/opt/dlami/nvme/releases`; `/root/infrx-deploy-<release>`; S3 `s3://llm-bootcamp-641134885443/{releases/,w4/e4b-box/}` | recorded by handoff 20 §10.2, §14.1; session 02 |
| Owned resources: this host (read now, 21:40Z) | containers `infrx-d2-postgres` (127.0.0.1:55433, up 40 h, left as found by session 03); `gideon-migration-order-test-caae059890` (unrelated, 5 weeks); **`infrx-d1-postgres` (127.0.0.1:55432, started ~21:39Z, owner unknown**: a wave-4 lane using the shared default d1 namespace outside its 555xx range). Listening ports: 22 53 443 3111 3112 7681 18789 49301 52717 55432 55433. `next-server` pids 3907004, 3969240: the App dev servers running since 2026-09-20, which fail the dev-host certify precondition. Lane ranges: E2C 55510–55519 `infrx-e2c-`, E1C 55520–55529 `infrx-e1c-`, I8 55530–55539 `infrx-i8-` | `docker ps`, `ss -ltn`, `pgrep` here; session 03 dispatch 1 |
| This lane's own resources | `apps/infrx-api/.venv` (gitignored) created offline from the uv cache; no containers, no ports | here |

## 2. RV-01 … RV-12 at `dff31efc`

"2026-09-24 lanes" means CERTIFY-TREE `4db74b6` (certify.py, bench.py R106, protocol amendment 5), TOKCOST `95fbb90` (preparation.py memo, jobstore pin, worker main), INTAKE-DRAIN `a163953` (intake.py drain; `routes/jobs.py` and `routes/uploads.py` only changed the `intake.guard(…, limits)` signature) and CERTIFY-POLISH `bda1586` (certify.py, bench.py N7/N9). File lists come from `git diff --stat <merge>^1 <merge>`.

| RV | Class | Code-level check at `dff31efc` (file:line) | What the 2026-09-24 lanes changed | Closure owners (manifest) |
|---|---|---|---|---|
| RV-01 public claims | **open** | `gateway/routes/models.py:9-15` returns the static `MODELS_DOC` (`config.py:60`) = `openrouter/provider-models.json`: `max_prompt_length` 120 s (`:29`), `tools` (`:50`), concurrency 16 (`:61`), `compliance.zdr: true` (`:69`). App docs: `apps/app/app/(console)/docs/page.tsx:28` ("Trim the clip to 120 seconds"), `:118` (fallback 120), `:147` ("never store prompts, videos or completions") | none | F2C, G7, A3 |
| RV-02 upload restart | **open**, re-run now: `upload_after_gateway_reconstruction` → `not_found`, bytes present | `media/uploads.py:88` `self.uploads: dict`; `:249` `resolve_owned` reads it; `media/store.py:175` `self.refs: dict`; `gateway/pilot.py:280` composes `MediaUploads`. Durable table `infrx.media_uploads` (0010) exists with no adapter | none | D10, M5, E1C, E3C |
| RV-03 cleanup | **open**, re-run now: `collector_after_gateway_reconstruction` → source deleted after grace, 0 live-job and 0 attachment queries | `media/gc.py:69` liveness = `store.by_job ∪ store.prepared_by_job` (process maps); `:87`, `:140` sweep only `store.uploads`. `MediaCollector` (`gc.py:51`) is referenced nowhere else in `infrx/` (not scheduled). TOKCOST's `933dda3` pop of `prepared_by_job` bounds one map; it is not a durable scan | map bound only | D10, M6, I8, E3C |
| RV-04 final certificate | **open** | No committed box report on `bda1586`. `research/plan/evidence/e/E4B-release-decision.md` is still the template (§1 PENDING; §5 B1, B2 and build_info are stale, see §6.4). run3 in progress: dataset-resume FAIL (regime), envelope FAIL (§2.3) | runner only; no release decision | S3 (this record), E4C |
| RV-05 readiness barrier | **open** | `media/attachments.py:53-54`: `put` returns on an empty `refs` without writing. `worker/preparation.py:237` waits for attachment only `if work.media_refs`. `gateway/routes/relay.py:242` `_admitted` runs the CREDIT rechecks after the admission transaction. TOKCOST added only the memo after that wait (`preparation.py:243-250`) | none to the barrier | F2C, D10, W5, G7, E3C |
| RV-06 App integration | **open** | `apps/app/app/(console)/usage/fake-console-context.ts:22,29` returns `null` in a production build; `app/(auth)/` holds only `login`, `forgot-password` and `update-password` (no signup) | none | C0, C3A, A2, A3, U1R, U2, U3, U4, E3A |
| RV-07 upload client | **open**, re-run now: `benchmark_against_real_upload_route` → 400 `invalid_request` at create | `models/marlin2b/bench.py:815-816` sends `purpose`, `filename`, `sha256`, `content_type`; `:827-828` sends a body to `/complete`. The route (`gateway/routes/uploads.py:5`) takes "no body"; `:113-115` answers `upload_handle`/`destination_ref`. CERTIFY lanes edited `bench.py` for R106/N7/N9 only | none to `upload()` | E1C, M5, G7 |
| RV-08 replay in performance | **open** (mitigated by construction in certify) | `bench.py:1267` `accepted` includes `idempotency_replayed` rows; `:1333` only counts them. The certify runner gives each run a fresh `dataset_version` (`certify.py` `load_cells`/`dataset_check`, `e4b-<sha7>-<UTC>-<name>`), and the E1B bda1586 launcher used `e1b-2026-09-24c-<cell>` (replayed 0). No code invalidates a replayed capacity cell. The E1B protocol amendment (per-cell identity) is not written (`E1B-protocol.md:32` still pins one version) | R106 made the *cancelled* replay terminal; general replay is untouched | E1C, E4C |
| RV-09 operations | **open**, with more evidence | Pool: default 10 per process pool (`config.py:330`), and gateway and worker each open one (`pilot.py:151-160`); `EMAXCONNSESSION` observed live. Session-level SETs in `pilot.py:129-135` and `jobstore.py:43-53` break under transaction pooling (finding 6). Runtime login `postgres` (bypassrls, D-31). Alerts: `observe/alerts.py` exists, but `apps/infrx-api/deploy/` has no evaluator unit or timer (only the vllm, gateway, worker and valkey services); no delivery destination (D-33); reconciliation gauges never published (finding 4). Weights on instance-store NVMe, not mirrored (D-32); hosted backup/PITR ⚠️ | none | D10, I8, E4C |
| RV-10 rollback | **open** | Box backup `…T200647…-bda15866…` holds 27af05a, which cannot complete jobs (finding 7). The drill on 2026-09-24 measured readiness only (5 s to `/readyz` 200 on 27af05a; the public edge still answered 503 about 25 s after readiness, ⚠️ TO BE VERIFIED). No inference, result or settlement ran after the rollback | none | I8, E4C |
| RV-11 result TTL | **open** | `gateway/routes/jobs.py:145-156` recomputes `settled_at + limits.result_ttl_s` (the docstring's ponytail names the upgrade). `TerminalOutcome` (`contracts/records.py:613`) has no `result_expires_at`. The column exists (`0003:175`) and is set by `0018:436`; `wire.JobStatus` carries the field (`wire.py:150`) | INTAKE-DRAIN changed one `jobs.py` line (the guard signature) | F2C, D10, G7, U4, E3C |
| RV-12 verification portability | **open** (a harness defect, not a Linux product defect) | `observe/route.py:53` calls `collect_host(…)` with the default `proc="/proc"` (`observe/host.py:73`), so `tests/g/test_startup.py:165` needs Linux `/proc/meminfo`. `tests/i/test_release_bundle.py:92-94` fetches into `refs/heads/main` of a fresh `git init`. **Re-run now on Linux:** 3 passed with git's default branch. With `init.defaultBranch=main`, `test_release_bundle` fails at the `:93` fetch (exit 128), which reproduces the Mac failure mechanism on Linux | none | E2C, E3C |

### 2.1 What the 2026-09-24 lanes closed (not RV findings; do not redo)

- **Handoff 20 D-6:** cancelled replay is terminal (R106, `bench.py`), live on `bda1586`. The run3 dataset client half passed (§2.2).
- **D-7:** served-build and release-identity now pass in the certify image (run3 ok).
- **D-1:** the intake drain (R108). Code merged and deployed; **live exercise pending** in the run3 overload cell (finding 5).
- **D-2:** `LARGE_BODY_LIMIT=8` deployed; the sizing row is in `infra/runbooks/rollout.md`.
- **D-3:** the soak bound is now schedule + 900 s (N12).
- **D-4:** short-clip sample sizing (N14).
- **D-9:** the video `/tokenize` cost is 0.13–0.2 s warm; the 16.8 s was the first-video cold path, H1.
- **D-12:** the worker build gauge now carries `--worker-metrics-url`. The GPU and reconciliation gauges for the soak are **still missing** (finding 4).
- **E4B release-decision §5:** B1, B2 and build_info are closed by the run2 config-pin PASS and the run3 served-build ok (D-35; edit in §6.4).

### 2.2 Interpreting the run3 `e4b.a.dataset-resume` FAIL

- **How the cell runs** (`certify.py:693-763`):
  1. Client invariants first (`resume_problems`, the resumed run's exit code, over-cap items).
  2. If any client problem exists, the cell FAILs **with those problems and returns**; the ledger is never read.
  3. Only with clean client invariants does it read the tenant ledger (`tenant_ledger`, `:591`, over `cli.build_operations` → the fresh-connection `connector` on the DSN the launcher passed, port 6543). It then loops up to 300 s on `reconcile_problems` (`:544`).
- **Consequence:** the message `usage recorded outside CREDIT: […]` can only come from `reconcile_problems`, so on `bda1586`:
  - the SIGINT really interrupted the first run;
  - every item was terminal after the resume;
  - nothing terminal was re-sent;
  - the interruption's cancels were replayed once (R106);
  - the resume exited 0;
  - no within-cap item was refused as over the cap.
- **The assertion is regime-dependent by pre-registration.**
  - Protocol §3 requires "every record in CREDIT". `reconcile_problems` checks `entry.unit != "CREDIT"` (`:567-569`).
  - `PgAccountView.holds` reads only `accounting_regime = 'credit'` holds (`state/operations.py:348-349`).
  - The balance is the CREDIT consumer wallet (`consumer_wallet_for_user` … `kind = 'consumer'`, `state/operations.py:436-438`).
  - Under `legacy_usd`, the verdict can never pass. Expected detail: (a) `usage recorded outside CREDIT`, (b) `jobs without exactly one hold: {…: 0}` (no CREDIT hold exists), (c) `Σ charged <USD sum> != ledger fall 0` (the CREDIT wallet does not move). `reserved` stays equal.
- **Verdict: regime mismatch: not a runtime defect; E4C requires CREDIT mode anyway.**
- **When `report.json` lands, confirm:**
  1. The problems list contains **no** `items accepted as more than one job` and **no** `jobs without exactly one usage record`. Those checks precede the unit check and are regime-independent. Their absence is positive MARLIN-SOP evidence on the live ledger: one Inference-Id per item, one USD usage record per job.
  2. Every other listed problem is one of (a)–(c). Anything else is a real finding.
- **Runner proposal for E4C** (owner E4C/E2C, not applied here): read the deployment's regime (the env file already passed via `--env-file`). A `legacy_usd` target should mark the ledger half NOT RUN with reason `regime legacy_usd`, not FAIL. The alternative is to state CREDIT as a box precondition.

### 2.3 Interpreting the run3 `e4b.b.envelope` FAIL (`supported_rate_per_s: 0.5`)

**How the verdict is computed** (`certify.py:1086-1128`, criteria `:95-105` = protocol §5):

1. Each rung is judged over within-cap attempts: `duration_cap`, `failure_rate`, `answered`, `rejections` (any refusal within the cap), `ttft_p95_short`, `e2e_p95_per_clip_minute`, `client_exit`.
2. **The supported rate** is the highest rung, climbing from 0.5, whose **core** rows (`failure_rate`, `answered`, `rejections`, `client_exit`) all pass. Latency rows never lower it.
3. **The cell status** is the chosen rung's non-cap rows plus every rung's `duration_cap`. FAIL if any is fail; PENDING if any is unknown.

**What the given facts establish:**

- Client exits were 0 at all three rungs, so the 1.0 rung failed `failure_rate` (≥ 1 % of within-cap attempts got no answer) or `rejections` (≥ 1 within-cap refusal, e.g. 429 `capacity_exhausted` at 8 slots). run2's 1.0 rung failed `rejections` with 3 × 429.
- The cell FAIL comes from a fail row in `rungs["0.5"]` or from any rung's `duration_cap`.

**Provisional thresholds applied (all P-18, protocol §5, `15-pending-inputs.md` P-18):**

| Criterion | Value | Kind |
|---|---|---|
| `max_failure_rate` | 0.01 | provisional |
| `ttft_p95_short_s` | 6.0 s for clips ≤ 30 s at ≤ 1280 px | provisional |
| `e2e_p95_s_per_clip_minute` | 45 s | provisional |
| `p95_min_accepted` | 60 | a method rule |
| Soak: host / GPU growth | 512 / 256 MiB | W4 engineering |
| Soak: `soak_latency_drift` | 1.5 | provisional |

The cap is the deployed 82 s. For reference, run2's 0.5 rung failed only `e2e_p95_per_clip_minute` (91.39 s over 112 samples); its `ttft_p95_short` was unknown with 54 samples.

**Read when the report lands:**

- If the only fail rows are the two P-18 latency rows, the FAIL is a **measurement against unagreed provisional targets, not a defect**, and "supported 0.5/s on the video_b64-only certify mix" is the measured envelope.
- A `duration_cap` fail (over-cap admitted, or within-cap refused as over) is a real defect.
- A `failure_rate` fail at 0.5 is a real defect.

**Acceptance stays pending until P-18 targets exist** (brief E4C.2).

## 3. E4B / E1B evidence on each candidate, and reuse by E4C

Candidates: **27af05a** (first pilot, no preparation loop), **4226315** (second release), **bda1586** (deployed). E4C's candidate will change `media/`, `worker/`, `gateway/routes/`, `state/`, `operations/`, `deploy/`, add migrations ≥ 0019 and switch to CREDIT. So a cell is reusable only where its measured path is unchanged, and it is never reusable as acceptance.

| Cell / evidence | 4226315 | bda1586 | Reusable by E4C? |
|---|---|---|---|
| dev-host `certify.py --report` (E2 stack): protocol 117 PASS, recovery 71 PASS, parity 9 clips PASS, release-identity PASS, preconditions FAIL (App dev servers) | PASS/FAIL as listed (`evidence/e/E4B-dev-4226315.json` sha256 `26c2e97e…`) | NOT RUN (gate-3's layer 3 green at `95fbb90`, but no certify report; the delta at `bda1586` ran no layer 3) | **No.** Superseded by E3C on the combined SHA |
| `e4b.b.preconditions` (box) | PASS (run1, run2) | ok (run3) | No: per run |
| `e4b.b.config-pin` | PASS | ok | Engine pin unchanged since 2026-09-23 (hashes above); must rerun (cheap) on the E4C image |
| `e4b.b.served-build` / `release-identity` | FAIL (no git in the runtime image; protocol defect, fixed) | ok / ok | No: bound to the SHA |
| `e4b.a.sop-parity` | FAIL (over-cap clips; protocol, fixed in 4db74b6) | PASS ("2 clips" within cap; over-cap → 400 `unsupported_media`) | The engine half is reusable **only if** engine pin, model/processor digests and profile v1 are unchanged. The gateway over-cap half must rerun. "2 clips" is parity, not SOP accuracy (P-07) |
| `e4b.a.dataset-resume` | FAIL run1 (all 400, price gap); FAIL run2 (cancelled replays → R106) | FAIL: **INVALID** ledger half (regime mismatch §2.2); client half passed (inferred from code) | Client half: behavior evidence for R106 only. Ledger half: **must run in CREDIT** |
| `e4b.b.envelope` | FAIL run2: supported 0.5; e2e p95/clip-min 91.4/91.7/96.8 s; r1.0 3 × 429, r2.0 33 × 429 | FAIL, supported 0.5 (§2.3; detail pending) | Baseline for regression comparison only. Rerun on the final config: CREDIT admission adds DB work, and gateway/worker paths change |
| `e4b.b.soak` | FAIL run2 (exit 124 at 3600 s; the 65 min measured 809/0 failures, RSS −52 MiB) | RUNNING (609 rows at 21:26Z; arrivals end ≈ 00:46Z est. from 0.25/s; hard bound start + 15,300 s) | Cannot PASS at bda1586 (finding 4). Rerun after I8 publishes the reconciliation and GPU gauges |
| `e4b.b.overload` | FAIL run2 (14 ReadError; the drain defect) | PEND (after the soak) | Pending; first live drain evidence. Rerun if `intake.py`, ingress, `LARGE_BODY_LIMIT` or the edge change |
| `e4b.b.recovery-box` | PEND in the report; drills run by the coordinator: engine restart 172 s, worker SIGKILL 8 s, Valkey loss ~6 s, drift 0/0 (session 02 step-6) | PEND (coordinator drills) | Restart **timings** are reusable as descriptive while the engine pin is unchanged. P7 needs post-recovery inference, result and settlement, which these drills did not do |
| Rollback drill | — | backward only: 5 s to readiness on **27af05a**; edge 503 ~25 s ⚠️; forward = the install | **INVALID as rollback proof** (RV-10): readiness only, and a target that cannot complete jobs |
| Restore B1 (snapshot list) / B2 / B3 | B1 listed; B2/B3 NOT RUN | — | NOT RUN |
| W12 smoke 15/15 | PASS | PASS | Per install; rerun |
| E1B through the edge, first pass (`E1B-box-4226315/bench.jsonl`) | L2 r=0.25 valid (57/60, 0 failed); L2 r≥0.5, L3, L5, L6: **INVALID for performance** (replayed = accepted) | — | No |
| E1B run2 distinct keys (`…/run2-distinct-keys/`) | valid: r0.5 113/120 (1 ReadError), r1.0 111 (4 ReadError), L3 105 (9 ReadError), L5 cancel 49 + 8 cancelled / 0 failed, L6 resume no second acceptance | — | Historical pre-drain baseline only |
| E1B acceptance (`E1B-box-bda1586/`) | — | 3 × 120: 114 × 200, 6 × 400 over-cap, 0 failed, 0 replayed, **0 × 429**; one tenant; 60 text + 60 video_b64; legacy_usd; TTFT p95 8.66 / 5.21 / 7.60 s | Only for "no transport loss at 0.5/1.0/burst-8 on this mix at `LARGE_BODY_LIMIT=8` through the edge", and only if intake, ingress, the edge and the limit are unchanged. **Not** for drain-429 delivery (finding 5), video-only latency, CREDIT, two tenants or overload |
| E1B L4 (paired direct), L7 (4 h soak), L8 caption parity | NOT RUN / NOT RUN / 0/3 undiagnosed (D-13) | NOT RUN | Open for E1C/E4C |
| `/tokenize` split (0.205 s cold body / 0.13 s warm; first video after start 16.8 s) | measured on the idle engine | — | Reusable as engine behavior while the pin is unchanged; feeds W5 warmup |

## 4. Inputs and authorizations

### 4.1 Pending inputs P-01 … P-25

Sources: `15-pending-inputs.md` (including "Consumer v1 closure input routing"), handoff 20 §9, program 22 §Inputs. The status is as of `dff31efc` plus this record.

| ID | Status | Owning lane(s) | Gate it blocks |
|---|---|---|---|
| P-01 approved CREDIT card | **open**. Provisional hosted card `rc_marlin2b_2026_09_provisional` (400/1,200 per M, `provisional = true`); a second provisional identity comes from G6B `marlin_release` (finding 11) | operator → G8, D10, F2C.c, A3 | E4C (CREDIT), public publication, E4 |
| P-02 USD balances and transition | **historically resolved (2026-09-22, $0.00) and now stale.** Since 2026-09-24 the pilot org has a USD 5.00 test grant and legacy_usd usage from W12, E1B and E4B. It needs a new inventory | G8, D10, I8 | CREDIT activation on the pilot |
| P-03 local services | resolved (Docker on this host) | E2C pins versions | — |
| P-04 target | resolved: pilot box `i-0e8449a4ffca29bab`, full operational authorization | I8, E4C reuse | — |
| P-04-sweep | resolved 2026-09-23; rerun after any engine change | W (only if the engine changes) | — |
| P-05 signup email/abuse | **open** (hosted signup disabled). Also blocks a **second verified tenant**: the second dev account's email is unconfirmed (handoff 20 §14.1), so hosted two-tenant cells cannot run | A2, I2A, operator; coordinator for the test tenant | public onboarding; E1B `--tenant-keys`; E4C P5 fairness |
| P-06 artifact/capability profile | resolved as profile v1; registry oids ⚠️ (`HF_TOKEN`); processor digest missing (finding 10) | E4C freeze, I8 mirror | a "pinned artifact" claim |
| P-07 SOP rubric/ground truth | open | provider/product | accuracy claims only |
| P-08 … P-15 | open (Lab origin, source-purpose permissions, teacher rates, training connector, rollout population, live video, robot, non-NVIDIA) | later programs | none in consumer v1 backend |
| P-16 fleet | open; notes in `19-fleet-scale-TO-BE-IMPLEMENTED.md`; user: "leave it in TO BE IMPLEMENTED notes" | — | any HA claim |
| P-17 backend acceptance → App | **open**: E4B decision PENDING; E4C is the gate root now | coordinator/user | App feature dispatch |
| P-18 workload/SLO targets | **open**. Only the provisional criteria in §2.3 exist | workload owner with E1C/E4C | E4C acceptance (measurements can proceed) |
| P-19 g6e price row | open | pricing owner / E1C | any cost-per-video-hour figure |
| P-20 duration cap | **decided**: 82 s deployed; user re-confirmed "keep it at 82sec" 2026-09-24 | G7/A3 remove the stale 120 s claims | — |
| P-21 ephemeral-port reservation | open (root change; user approval) | coordinator/E2C | flake-free gates |
| P-22 USD price by literal model string | open; W7e rows for both alias forms seeded hosted (`pv_marlin2b_usd_2026_09`, `…_r1`) | F2C.c, D10, G7 | examples/publication; certify `--model` |
| P-23 long clips | open note (LONGCLIP shelved at `0665bdb` on `codex/longclip`) | — | only clips > 82 s |
| P-24 bounded test allocation | **partially satisfied.** Target, authorization, window rule and stop rule exist (§4.2). No versioned profile records the numeric caps (requests, bytes, spend) yet; the key inventory (finding 8) and a second tenant (P-05) are missing | E1C (profile schema), E4C, coordinator | resource-consuming E4C cells without bounds |
| P-25 operations/retention ownership | **open**: no approved serving-content TTL; no alert destination (D-33); hosted backup/PITR ⚠️; known-good rollback bundle not identified (finding 7) | I8, D10, M6 | truthful retention claims; operated-release acceptance |

### 4.2 Authorizations already granted (do not ask again)

Scope rule: an authorization covers its stated scope, and the coordinator's operating rules (session 02, 2026-09-22 15:52Z, items 1–6) still apply: log before acting, bounded spend, rollback path.

| Grant | Scope | Source |
|---|---|---|
| Merge to `main` (App auto-deploys) | at reviewed green checkpoints | session 02 "Authorization update (2026-09-22 15:49Z)" |
| Full operational authorization | "applying supabase migration, public cutover, paid provider calls, compute purchases, any operations", under operating rules 1–6 (paid calls only inside a recorded budget) | session 02 "Authorization update (2026-09-22 15:52Z)" |
| GPU target | "you can use the GPUs now … check for live instances" → the pilot box, no new instance | session 02 "Parallelism decision (2026-09-22 15:58Z)"; P-04 |
| Keys, flags, grants on hosted | operator key minted (16:27Z), `signup_grant` flag (16:32Z), USD 5.00 test grant (16:45Z): "do whatever it takes, to test it out end to end and fix all the issues, you have my full permission" | session 02 LOGGED OPs 2026-09-24 |
| Box certification window | E4B box runs with `E4B_WINDOW_OK=1` while no external consumer key exists or the window is announced | session 02 16:51Z; handoff 20 §10.2 |
| Recovery drills on the box | restart, index-loss, rollback/forward, restore B1 (list); B2/B3 not authorized as live-pilot drills (a restore discards root-volume writes) | session 02 "LOGGED OP: E4B step 6 recovery drills" (19:30Z) |
| Database changes | "you have all the permissions for database" (18:45Z, given with the 1200 s request; the cap decision then reverted to 82 s at 19:25Z) | session 02 CERTIFY-TREE MERGED / RUN2 DONE entries |
| Wrap-up, merge, deploy, keep collecting evidence | 19:38Z | session 02 TOKCOST fix-round entry |
| Hosted read rule | Reads from this host are classifier-blocked **except** `apps/infrx-api/.venv/bin/python infra/runbooks/drift.py:*` (local allow rule, port 6543, read-only aggregates). Otherwise use box-side reads through the worker container's connection. Lanes never read hosted | session 02 19:55Z; handoff 20 §14.3; lane rules 3 |
| SSM ops | coordinator only, via `infra/rollout/ssm.sh`, one step per command, each logged with purpose, cost, rollback and command id | handoff 20 §10.4–10.5, §14.3 |
| Not granted | new GPUs, standby replicas, Modal (fleet → notes only); paid provider calls without a recorded budget; a numeric spend cap for E4C (none recorded: P-24) | session 02 RUN2 DONE; program 22 §Inputs |

## 5. Per lane: already done (do not redo) / still open

**F2C** (contracts)
- Done:
  - v2 units (`UsageRecordV2` unit per regime, `records.py:755`).
  - `wire.JobStatus.result_expires_at` (`wire.py:150`).
  - Upload wire DTOs (G4U: create → authenticated same-host PUT → empty-body complete).
  - G6B `marlin_release` pins the digest and image (B1 closed).
  - Rulings R104–R108 are numbered; next free is **R109**.
  - Do not redesign the upload protocol or re-rule R106 or R108.
- Open:
  - F2C.a: empty-attachment completion and the upload/liveness/deletion ports.
  - F2C.b: `TerminalOutcome.result_expires_at` (D-22; `records.py:613`).
  - F2C.c: the published-model projection replacing `provider-models.json`; P-22 amendment; one card identity (finding 11).
  - F2C.d: fixtures and the consumer matrix.

**D10** (SQL/state)
- Done, applied hosted, immutable:
  - 0001–0018.
  - `infrx.media_uploads` (0010) with constraints.
  - `jobs.result_expires_at` (0003, set by 0018).
  - Seed and W7b–W7e rows.
  - Next migration number: 0019 (verify at dispatch).
- Open:
  - Durable ready marker for zero and nonzero media (RV-05).
  - Upload adapter over 0010 (plus additive columns if needed).
  - Lifecycle/tombstone repository (RV-03).
  - Persisted expiry in the read adapters (RV-11).
  - Reconcile `23505` → typed conflict (D-21).
  - Runtime role privilege list for I8 (D-31).
  - P-22 resolved pricing.

**M5** (uploads)
- Done: `MediaUploads` semantics (M3), `resolve_owned` for `infrx-upload:` refs (MPILOT), routes (G4U). Keep the protocol.
- Open: replace the `self.uploads`/`self.refs` authority (`uploads.py:88`, `store.py:175`) with the D10 port; cross-process A/B/C/D test; the RV-02 probe must flip.

**M6** (cleanup)
- Done: `MediaCollector` code (`gc.py`); `ProcessingCache` TTL; `prepared_by_job` pop (`933dda3`). **Do not schedule the current collector.**
- Open: durable candidates, claims and tombstones; content-bearing DB fields; cache and map bounds (D-20); the RV-03 probe must flip.

**W5** (worker)
- Done, deployed:
  - Preparation loop (PREP-WORKER).
  - Engine-exact counts (R104/R105).
  - Count memo (R107).
  - Worker `PartOf` restart verified on systemd 255.
  - The 16.8 s first-video cold path diagnosed (H1).
- Open:
  - Readiness barrier for text-only jobs (`preparation.py:237`).
  - A permanent refusal costs up to 3 lapsed leases (D-19).
  - Explicit warmup video after engine start.
  - D-25 F1/F2 (unbounded renewal cases; HeadBucket under a mutant).
  - Worker `/metrics` queue/index gauges (with I8).

**G7** (routes/discovery)
- Done, deployed:
  - Bounded intake drain (R108).
  - `LARGE_BODY_LIMIT=8`.
  - Capacity-before-duration order judged by certify N15.
- Open:
  - `/v1/models` projection (RV-01; `models.py:9-15`) and the App docs claims (with A3).
  - Persisted expiry on status/result/replay (`jobs.py:145-156`).
  - P-22.
  - INTAKE-DRAIN N1 (finished-guard case plus mutant) and N5 (`Expect: 100-continue`).
  - D-28 J5–J8 (SSE resume `role`; `stream:false` identity; disconnect hold released only at reconciliation).
  - Caddy pass-through of a drained 429 (finding 5; with E4C).

**G8** (headless CREDIT ops)
- Done: operator CLI (G6B: grant, issue/revoke key, suspend, publish, reconcile); operator key in SSM; `signup_grant` on; one A1 grant (10,000 CREDIT) for `f997131f…`; USD 5.00 test grant; consumer key `142c7d81…`.
- Open:
  - CREDIT activation procedure with a USD inventory (P-02 stale).
  - Refuse an unapproved card (P-01).
  - Dry-run transition report.
  - Key inventory (finding 8).
  - Second verified tenant (P-05).

**E1C** (client/profile)
- Done: R106 cancelled-replay classification; N7/N9 denominators; per-cell dataset identity used in practice (the E1B bda1586 launcher).
- Open:
  - `bench.upload` protocol (RV-07; `bench.py:808-838`).
  - Invalidate unexpected replay (RV-08; `bench.py:1267`).
  - Amend `E1B-protocol.md` (per-cell identity; flag `replayed > 0`).
  - Profile schema and `--validate-only` (P-24).
  - L4, L7 and L8 (D-13).

**I8** (operations)
- Done: runbooks and local drills; rollout W1–W13 (W7b–W7e learned); box drill timings; `drift.py` on 6543; `LARGE_BODY_LIMIT` sizing row.
- Open:
  - Pool budget (finding 6; the session SETs must move to a per-transaction `SET LOCAL` or a dedicated login with role and settings defaults before any 6543 switch; prepared statements ⚠️).
  - Runtime role (D-31).
  - Alert evaluator unit plus delivery (D-33).
  - Publish reconciliation/unsettleable gauges from a real reconciliation pass (finding 4).
  - GPU gauge the soak can read.
  - Weights and processor mirror plus restore (D-32).
  - Hosted backup/PITR (P-25).
  - Known-good rollback bundle and a real-request rollback (finding 7).
  - D-24 (psycopg WARNING echoes the DSN host).

**E2C** (verification)
- Done: this host is Linux (kernel 7.0.0-1010-aws), git 2.43 with no `init.defaultBranch`, Docker 29.6.2 (per the dev report). The RV-12 tests pass here, and the Git-default failure reproduces with `init.defaultBranch=main` (§7).
- Open:
  - Inject `/proc` or a platform abstraction at `observe/route.py:53`.
  - Git-default fix at `test_release_bundle.py:92-94`.
  - D-26 (a Q3 drill without a deadline).
  - D-27 (perf stubs match "verify").
  - D-30/P-21 ports.
  - The dependabot moderate alert (D-34).
  - The App dev servers failing the dev-host precondition.
  - The stray `infrx-d1-postgres` on 55432.

**E3C** (local gate)
- Done: E3B harness; layer 3 green at `95fbb90` (gate-3); backend 188/0/0 at 4226315. Reuse the harness; do not rebuild it.
- Open: every corrective scenario in the brief. BACKEND-LOCAL has never been formally declared (D-35).

**E4C** (release)
- Done: `certify.py` with E4B list 226 mutants; the certify image pattern; the run3 cells in §3.
- Open:
  - Regime-aware ledger half (§2.2).
  - P-18 decision.
  - Two tenants (P-05).
  - P0–P8 profiles.
  - Fill `E4B-release-decision.md` or supersede it with an E4C decision.
  - Fetch run3 `report.json` and confirm §2.2/§2.3.

## 6. Proposed edits (the coordinator applies; history preserved)

### 6.1 `research/plan/tasks.json`

- **S3:** `"status": "planned"` → `"implemented"`; add `"evidence": ["research/plan/evidence/coordinator/2026-09-24-S3-reconciliation.md"]`. The residual (§8) is a post-run3 addendum, not a new task.
- **E1B:** keep `planned` (L4/L7/L8, video-only envelope and two tenants are unmet). Add `"evidence": ["models/marlin2b/results/E1B-box-4226315/", "models/marlin2b/results/E1B-box-bda1586/", "research/plan/evidence/coordinator/2026-09-24-S3-reconciliation.md"]`.
- **E4B:** keep `implemented` (the runner). Add `"evidence": ["research/plan/evidence/e/E4B-dev-4226315.json", "models/marlin2b/results/E4B-box-4226315/"]`. The release decision stays PENDING.
- (M3 already carries an `evidence` key, so the validator accepts the field. Regenerate the ledger after the edit.)

### 6.2 `research/plan/15-pending-inputs.md`: append this section verbatim

```markdown
## S3 reconciliation (2026-09-24, `2026-09-24-S3-reconciliation.md` §4)

- **P-02 re-opened for the pilot:** the 2026-09-22 resolution ($0.00) predates the pilot's USD 5.00 test grant and its legacy_usd usage (W12, E1B, E4B). G8/D10 inventory the USD wallet, jobs and holds read-only before any CREDIT activation; no conversion.
- **P-05 also blocks a second hosted test tenant:** the second dev account's email is unconfirmed, so E1B `--tenant-keys` and E4C's two-tenant/fairness cells cannot run on the pilot. The coordinator confirms a second verified individual as a logged op, or the cells are NOT RUN.
- **P-06:** add served-bytes digests of `processor_config.json` and `preprocessor_config.json` to the pinned serving record before E4C freezes the candidate.
- **P-24:** target, window and stop rules exist (S3 §4.2); a versioned profile with numeric request/byte/spend caps does not. The two pre-cutover consumer keys recorded active on 2026-09-24 need a read-only inventory before the next `E4B_WINDOW_OK=1` run.
- **P-25:** the only hosted backup on record is the coordinator-host dump `hosted-20260924T050746Z`; the hosted backup/PITR policy is ⚠️ TO BE VERIFIED. The box's newest install backup holds 27af05a, which is not a known-good rollback target.
```

### 6.3 `research/plan/22-consumer-v1-implementation.md` / briefs: evidence-justified clarifications (append-only lines, no scope change)

- **03 §E4C, item 1, append:** "The certify dataset ledger half asserts CREDIT (E4B protocol §3). A `legacy_usd` target yields a regime mismatch, not a runtime verdict (S3 §2.2). Run E4C in CREDIT mode, or record the ledger half NOT RUN with the regime."
- **03 §I8, item 1, append:** "`gateway/pilot.py` `configure_connection` and `state/jobstore.py` `connector` set role and statement timeout as session state; under transaction pooling that state is lost. The certify runner's fresh-connection reads on 6543 are no evidence for prepared statements (S3 finding 6)."
- **03 §I8, item 3, append:** "`observe.metrics.record_reconciliation` has no runtime caller at `dff31efc`: the drift/unsettleable gauges and their alerts are absent, and the certify soak's `reconciled_at_end` is always unknown (S3 finding 4)."
- **01 §D10.a, append:** "`infrx.media_uploads` (0010) and `jobs.result_expires_at` (0003, set by 0018) already exist on hosted; wire and extend them additively."
- **05 §2, item 3, append:** "The committed E1B `bda1586` cells contain zero 429s. They show no transport loss at `LARGE_BODY_LIMIT=8`, not drained-429 delivery; measure that through the public edge in P4 (S3 finding 5)."

### 6.4 `research/plan/evidence/e/E4B-release-decision.md`: append to its verification log

```markdown
- 2026-09-24 (S3 reconciliation): §5 B1 closed (run2 `e4b.b.config-pin` PASS: the published release pins `sha256:3c4bb…` and the digest-pinned image), B2 closed (the box engine runs the pinned 8; config-pin PASS on 4226315 and bda1586), build_info closed (run3 `e4b.b.served-build` ok on bda1586, gateway and worker). Decision remains ⚠️ PENDING. run3 on bda1586: dataset-resume FAIL = regime mismatch (legacy_usd vs the CREDIT ledger oracle), envelope FAIL supported 0.5/s; soak/overload pending. E4C supersedes this decision per program 22; see `research/plan/evidence/coordinator/2026-09-24-S3-reconciliation.md`.
```

### 6.5 `research/plan/20-platform-handoff-2026-09-24.md` §7: append one line under the table

```markdown
- 2026-09-24 (S3): D-1/D-2/D-3/D-4/D-6/D-7/D-9/D-12(worker build) addressed by the 2026-09-24 merges (see S3 §2.1); D-35 release-decision rows closed by evidence (S3 §6.4); new: the reconciliation gauges have no runtime producer (S3 finding 4); the install backup of bda1586 holds 27af05a (S3 finding 7).
```

### 6.6 Session record correction (append to `2026-09-22-session-02.md` or `2026-09-24-session-03.md`)

```markdown
- Correction (S3): the run3 third launch began ≈ 20:29Z box clock (out `20260924T202924Z`), not "21:29Z" as the 21:35Z entry reads.
```

### 6.7 Tracker (06 / TRACKER lane)

Gate BACKEND-READY stays pending, with the RV table as its open list. BACKEND-LOCAL should show "harness green at 95fbb90; not declared" instead of "not started" (D-35).

No ruling is needed from S3. A candidate for E4C to propose, not numbered here: "a regime-specific oracle on a target running another regime is NOT RUN with the regime named, never PASS or FAIL."

## 7. Commands run by this lane (local, read-only over the tree)

| # | Command (from the worktree root unless noted) | Exit | Result |
|---|---|---|---|
| 1 | `git diff --stat bda15866e5700f3856d7142580da842fba9bbd23 dff31efc -- apps infra models tests Makefile` | 0 | 5 files: 4 E1B result files and `models/marlin2b/smoke.py` |
| 2 | `git diff --stat <m>^1 <m>` for `4db74b6 95fbb90 a163953 bda1586` | 0 | file lists in §2 |
| 3 | python over `models/marlin2b/results/E1B-box-bda1586/raw/*.jsonl` (Counter of outcome/status/code, replayed, forms) | 0 | each: 114 × (accepted, 200), 6 × (rejected, 400, `unsupported_media`), replayed 0, forms 60 text / 60 video_b64; no 429 |
| 4 | python over `E4B-box-4226315/run{1,2}/report.json` and `evidence/e/E4B-dev-4226315.json` | 0 | verdicts in §3; sha256 run1 `badd8814…`, run2 `67943709…`, dev `26c2e97e…` |
| 5 | `cd apps/infrx-api && uv sync --frozen --all-extras --offline` | 0 | pinned venv from the local uv cache; no network |
| 6 | `apps/infrx-api/.venv/bin/python research/plan/evidence/v1-review-20260924/reproduce.py` | 0 | output **byte-identical** to the committed `observations.json` (sha256 `af7d5bd6…`): RV-07 400 at create; RV-02 `not_found` with bytes present; RV-03 source deleted, 0 durable queries |
| 7 | `cd apps/infrx-api && .venv/bin/python -m pytest -q -p no:cacheprovider tests/i/test_release_bundle.py "tests/g/test_startup.py::test_ops_recover__the_gateway_exposes_the_build_it_was_installed_as"` | 0 | 3 passed (Linux, git default branch) |
| 8 | the same `test_release_bundle.py` with `GIT_CONFIG_GLOBAL=<scratch file: init.defaultBranch=main>` | 1 | 1 failed, 1 passed: `git fetch … refs/tags/old:refs/heads/main` exit 128 (RV-12 mechanism reproduced) |
| 9 | `docker ps`, `ss -ltnH`, `pgrep -af next-server` | 0 | §1 host inventory |
| 10 | greps cited in §2 (`record_reconciliation`, `media_uploads`, `MediaCollector`, `prepare_threshold`, card ids) | 0 | as cited |

No box, hosted database, AWS, S3 or network call was made. No test file or code file was changed.

## 8. Remaining S3 effort

- **Residual:** after run3 exits, the coordinator fetches `report.json`. S3 then confirms the §2.2 checklist (regime-only problems) and the §2.3 fail rows, reads the soak/overload verdicts and appends a dated addendum to this record.
- **Estimate:** optimistic 0.5 h, likely 1 h, pessimistic 2 h, confidence med.
- **Basis:** reading one report against the code paths already traced here; the pessimistic case is a regime-independent ledger problem or an overload 5xx needing root-cause reading.

## Verification log

- 2026-09-24T21:47Z (S3, Opus): record written on `codex/s3-reconcile` from `dff31efc`.
  - Box and hosted facts are quoted from session 02, session 03, handoff 20 §14 and the coordinator's SSM `3588e56e` summary, each labelled "recorded by".
  - Code checks and commands 1–10 were run on this host.
  - No live-state claim is made; run3's remaining cells are not assumed.
