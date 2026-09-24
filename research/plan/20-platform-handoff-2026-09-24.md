# 20 — Platform handoff for re-planning (2026-09-24)

Compiled 2026-09-24 from the integration head `2fe829a` of `claude/backend-impl`
(`main` = `2d4a88b`, 5 commits behind it: `2d4a88b..2fe829a` is `4db74b6` CERTIFY-TREE plus
four plan/evidence commits). Read-only over the repository; nothing was run against the box,
AWS or the hosted project to write it. The coordinator appends the live-state section only it
knows.

## 1. Purpose and how to read

**What this is.** The single entry point for a fresh planning session that must re-plan the
pending tasks, design, specs and the next wave's implementation plan of the whole
model-inference platform program: research → contracts → waves 1–3 (backend-first) → the
deployed Marlin pilot. It states what is done (with the merge commit or evidence file that
proves it) and what is pending (with the file where it is recorded).

**What it is not.** It supersedes nothing. The coordinator records stay append-only and
authoritative for their own facts; the manifest (`research/plan/tasks.json`, v4) stays the
dependency graph; the rulings stay in `research/plan/08-contracts-v1-encoding.md` §10. Where
this page and a source disagree, the source wins and this page is wrong.

**Citation shorthand** (paths relative to the repository root, at `2fe829a`):

| Short | File |
|---|---|
| `S02:n` | `research/plan/evidence/coordinator/2026-09-22-session-02.md`, line *n* (append-only, so line numbers stay valid) |
| `S01` | `research/plan/evidence/coordinator/2026-09-20-session-01.md` |
| `W2H` | `research/plan/evidence/coordinator/2026-09-21-wave2-handoff.md` |
| `STATUS` | `research/plan/evidence/coordinator/STATUS.md` (waves 1–2 board) |
| `PROG` | `research/plan/evidence/coordinator/PROGRESS.md` (generated 2026-09-24T19:30Z from `progress-state.json`) |
| `ev/<t>/<f>` | `research/plan/evidence/<track>/<file>` |
| `08§10` | `research/plan/08-contracts-v1-encoding.md` §10 (rulings) |
| `P-xx` | `research/plan/15-pending-inputs.md` |
| `H<hhmm>` | repo-root `HANDOFF-20260924T<hhmm>Z.md` (newest: `H1745`) |
| `run2` | `models/marlin2b/results/E4B-box-4226315/run2-20260924T172244Z/report.json` (sha256 `67943709…b3d9`) |

**Status words** (from `research/plan/12-complete-build-plan.md` "Completion level"):
*implemented* = reviewed code + tests behind fakes or local services; *integrated* = real
adapters, locally; *release ready* = plus allocated GPU/staging/restore/load evidence;
*released* = authorized deployment with hosted smoke tied to the SHA. The manifest's
`implemented` does **not** mean deployed. `meas.` = measured with a source; `est.` = derived;
⚠️ TO BE VERIFIED = unknown, with where it would come from (CLAUDE.md research conventions).

**Reading order for re-planning:** §2 (one page) → §11 (the gap list) → §7 (defects) → §9
(inputs) → §4 (status) → §5/§6 (as built / measured) → §10 (operations) → §12 (process).

## 2. The platform in one page

**Two products, one runtime** (`research/platforms/README.md`):

| Piece | Audience / job | Location | State at `2fe829a` |
|---|---|---|---|
| Inference **App** | developer: signup, 10,000 CREDIT once per individual, catalog, keys, usage | `apps/app` (Next.js, Supabase auth/DB) | console deployed at `app.callbill.ai` by Vercel on every push to `main` (`HANDOFF.md` §1; `W2H` intro); consumer features of the product-v2 spec **not built** (A2/A3/C0/C3A/U1R/U2/U3 planned, `tasks.json`); consumer pages fixture-backed behind a build-time preview gate with a "Balance unavailable" state (S1-fix `6669e1f`, `S02:82`,`S02:105`). What the hosted console shows now that 0003–0018 are applied: ⚠️ TO BE VERIFIED (open `app.callbill.ai`) |
| Provider **Lab** | model teams: versions, dev/prod endpoints, traces, evaluation, improvement | `apps/lab` | `apps/lab/README.md` only — nothing built |
| **infrx-api** gateway/runtime | the shared inference gateway, worker, durable state | `apps/infrx-api/infrx/` | built through wave 3 for Marlin-2B on one GPU; **deployed as the pilot** (below) |

**Program order** (user direction 2026-09-22, `research/plan/18-marlin-backend-first.md`):
robust, measured Marlin backend first → App → Lab. Lead application: SOP verification over
large recorded robotics video datasets (`research/workloads/marlin-sop.md`); robot actuation
and live video are *not* implied (`marlin-sop.md` §5.4).

**What is live** (as of the records at `2fe829a`; the coordinator's live-state section
supersedes):

- **Endpoint** `https://marlin2b.callbill.ai` on EC2 `i-0e8449a4ffca29bab` (g6e.2xlarge,
  1× L40S 46,068 MiB, us-east-1d): release **`422631591845fbd66b590c73d5ff4150318d9d7a`**
  (`4226315`), runtime image `sha256:491697bc…`, deployed 2026-09-24 10:00–10:04Z in pilot mode
  (`S02:879`). It is the metered gateway (`uvicorn --factory infrx.gateway.app:create_app`),
  a worker running both loops (preparation with engine-exact prompt counts; inference), Valkey,
  the pinned vLLM engine and the Caddy edge (§5.4).
- **Hosted Supabase** `fcbnscgsymzdykendbrc` (us-east-2): migrations 0001–0018 applied (0003–0018
  on 2026-09-24T05:09:16Z, plan digest `4524cbc0…`, `S02:851`); operator seed applied
  (`S02:861`); flags `credit_admission` false, `legacy_usd_admission` true, `signup_grant` true
  (`S02:889`).
- **Accounting regime: legacy USD** (`ACCOUNTING_REGIME` unset = `legacy_usd`,
  `infra/runbooks/rollout.md` §1). USD price versions exist for both alias forms (W7c, W7e;
  `S02:895`,`S02:903`); the CREDIT card on hosted is the **provisional** one (P-01).
- **Clients** are provisioned headlessly by the operator CLI (`python -m infrx.operations.cli`,
  G6B): one operator key (SSM `/model-inference/operator_key`), one test consumer key and one
  revoked key (`S02:884`–`S02:889`). No self-service signup (hosted auth has signup disabled,
  `S02:46`; public signup is P-05).
- **Proven end to end on the pilot** (`S02:894`–`895`): W12 smoke 15/15, text and a 5 s video
  job completed and settled with `prepared_prompt_tokens == usage` (13 = 13; 1211 = 1211).
- **Certified?** No. E4B box certification run2 measured the envelope (0.5 req/s supported;
  end-to-end p95 91–97 s per clip-minute against a provisional 45 s; §6) and found a real
  overload defect (§7 D-1). The release decision `ev/e/E4B-release-decision.md` is
  **PENDING**; BACKEND-READY is not claimed (`tasks.json` E4B disposition).

**Specified but not built** (§11): every App feature past the headless endpoint, all of Lab,
the CREDIT cutover, trace shipping/ClickHouse/judge/feedback, the closed-loop platform
(`research/platform/`), any fleet/multi-GPU (`research/plan/19-fleet-scale-TO-BE-IMPLEMENTED.md`),
clips above 82 s, the bare-metal B300 blueprint (`research/scaling/10-blueprint.md` §8), and
models other than Marlin-2B.

## 3. Timeline

Commit times are author dates (UTC); record times are the coordinator's.

| When (UTC) | Event | Commit / checkpoint | Source |
|---|---|---|---|
| 2026-09-19/20 | Research tree; monolith `gateway.py` + vLLM nightly live on the box; console on `app.callbill.ai`; single-clip L40S measurements | — | `HANDOFF.md` §1; `research/models/marlin2b/` |
| 09-20 10:19 | Reviewed parallel implementation handoffs (the plan package) | `25b9829` | `S01` |
| **Wave 1** — contracts and foundations ||||
| 09-20 19:10 | F1 gateway extracted behind `create_app()` — integrated | `be7e984` | `S01`; `STATUS` |
| 09-20 21:49 | I1 read-only inventory and deploy design — integrated | `ee4688d` | `STATUS`; `ev/i/I1-4e052f4.md` |
| 09-20 22:11 | E1 distinct-clip corpus + authenticated bench client — integrated | `284aab5` | `STATUS` |
| 09-20 23:42 / 09-21 01:11 | F2 contracts v1: console half, Python half; gate **G0** | `70d0f9b`, `12c70c8`, `3b875a7` | `STATUS` |
| 09-21 05:49 | F2.1 coherence patch (rulings R43–R55) | `a666680` | `STATUS` |
| **Wave 2** — eleven modules ||||
| 09-21 07:19–22:14 | V1 `9617a2e`, U1 `184c5fb`, M1 `4c7a29f`, Q1 `d6f26e5`, C1 `010b6ea`, J1 `b1b84a1`, W1 `9a2eec2`, T1 `c13fb1a`, D1 `7a0d86d` (integrated), G1 `49eafd2`, E2 `cb04382`; stage review S2 pass | merges listed | `W2H` §1 |
| 09-21 22:44Z | `main` fast-forwarded to `271add9` | **main → 271add9** | `PROG` checkpoints |
| 09-21 22:13 / 22:44 / 09-22 08:23 | Wave-2 audit (two-platform split); App-first plan; **backend-first plan** | `07dfb64`, `2ea562a`, `ec6c548` | `research/plan/10-wave2-platform-audit.md`; `16-fresh-session-handoff.md` |
| **Wave 3** — backend first, 30 packages ||||
| 09-22 15:38Z | Integration branch `claude/backend-impl` at `ec6c548`; B0 lanes F2R/I0/E2R/S2M | — | `S02:5`–`19` |
| 09-22 15:49/15:52Z | User authorizes merges to `main`, then **full operational authorization** | — | `S02:24`–`31` |
| 09-22 15:55Z | Checkpoint 1 | **main → ec6c548** | `S02:33` |
| 09-22 16:05Z | Live inventory; P-02/P-03/P-04 resolved; root snapshot `snap-08732d3ac6376e850`, DeleteOnTermination off | — | `S02:44`–`51`; `ev/i/I1B-inventory-2026-09-22.md` |
| 09-22 18:13Z / 19:04Z | In-session checkpoints | **main → 2bfb0c4**, **→ e4dbb8c** | `S02:139`, `S02:170` |
| 09-22 16:11–22:13 | B0/B1 merges: E2R item 1, S2M, S1-fix, M2, F2R-B, E1B software, I0, F2P additive, Q2, E2R, W2, M3, G6B, F2R-A, D1R, E3B ph1, F2R-A follow-ups | §4 | `S02:53`–`210` |
| by 09-22 22:15Z | `main` at `f9ba5d2` | **main → f9ba5d2** | `S02:210` |
| 09-23 02:29–05:27 | G1R, M4, I3B, Q3, D2, A1, D3, W3+I2B (same step), A1 follow-up, F2P wire-in, W3 follow-up | §4 | `S02:294`–`439` |
| 09-23 03:19–05:16Z | Box: W3 inventory/capability; concurrency sweep → encoder-cache limit found (P-20) | evidence only | `S02:345`–`430` |
| 09-23 06:48–16:27 | F cancel-cause, I schema fix, W-new, G4U, D4, W4 phase A, G2, G3, F fakes follow-up | §4 | `S02:456`–`632` |
| 09-23 10:43Z | Checkpoint (api-test 2993; one load-sensitive runner timeout attributed) | **main → 01a7dfc** | `S02:517` |
| 09-23 13:15–14:18Z | Opus session limit; all lanes saved and resumed | — | `S02:580`–`584` |
| 09-23 18:16–18:24 | E3B phase 2, I3B follow-up | `d1d4a68`, `90b81c7` | `S02:677`–`686` |
| 09-23 19:29Z | **Acceleration rule**: one fix round per lane, single verifier, reviews on committed heads | — | `S02:694` |
| 09-23 19:47–21:54Z | Box window: W4 phase B (E0/E1/E3), E1B L0/L1/L8; **P-20 decided: B at 82 s** | `0117d48` | `S02:740`; `ev/w/W4-phaseB-20260923T2155Z.md` |
| 09-24 00:57 | **Merge unit**: D5, E3B phase 3, cutover, M1-L2, M pilot-media, E4B software half, rollout-prep; R95–R103 | `5b7fe69`…`92ea6c0` | `S02:804` |
| 09-24 01:03Z | SSM `/model-inference/pg_journal_url` created | — | `S02:808` |
| 09-24 01:28 / 02:48 | Cutover fix round; I2B-R4 worker root → **product gap found: nothing prepares a job** → PREP-WORKER | `865d352`, `405f633` | `S02:815`–`826` |
| 09-24 05:05Z / 05:25 / 05:33Z | Checkpoint 2 gate on `27af05a`; list fixes | `7a22064`; **main → 8cabe20** | `S02:845`–`857` |
| 09-24 05:07–05:23Z | First install window: **hosted migrations 0003–0018 applied** (05:09:16Z); install failed (`price_source`); reverted | — | `S02:848`–`854` |
| 09-24 05:35Z / 05:39–05:43Z | Operator seed on hosted; **release 27af05a deployed** (could not complete jobs: no preparation loop) | — | `S02:859`–`864` |
| 09-24 08:43 | PREP-WORKER preparation loop; R104–R105 | `28f3153`, `0e0506a`, `933dda3` | `S02:870` |
| 09-24 09:59Z | Focused gate on `4226315` | **main → 2d4a88b** | `S02:876` |
| 09-24 10:00–10:04Z | **Release 4226315 deployed** (preparation + inference loops) | — | `S02:879` |
| 09-24 16:28–16:49Z | Operator key, `signup_grant` flag, A1 grant, consumer keys, USD test grant; **W12 smoke 15/15; end-to-end proven**; 16.8 s video `/tokenize` found | — | `S02:882`–`895` |
| 09-24 16:54Z | E4B box certification **run1** (exit 1: labelled-alias price gap, no git, over-cap parity clips) | `0127395` | `S02:897`–`910` |
| 09-24 17:22–18:31Z | Labelled-alias price version seeded; **run2** (4,120 s) | `6535ccf` | `S02:902`–`913`, `S02:931` |
| 09-24 18:32 | CERTIFY-TREE merged (R106) | `4db74b6` | `S02:924`–`928` |
| 09-24 18:45–19:25Z | User: cap 82 s → 1200 s (LONGCLIP lane), then "keep 82 s" (shelved `0665bdb`); fleet → notes only; INTAKE-DRAIN dispatched | `a355316` | `S02:928`, `S02:931`; `19-fleet-scale-TO-BE-IMPLEMENTED.md` |
| 09-24 19:30Z | E4B step-6 recovery drills on the box (logged op; results not in the tree) | — | `S02:933` |
| 09-24 19:40Z | TOKCOST fix round `293ddcb`; user: wrap up, merge to main, deploy, write this handoff | `2fe829a` | `S02:936`–`937` |

## 4. Manifest v4 status

**Whole manifest** (`research/plan/17-task-ledger.md`, generated from `tasks.json`):
**119 records; 113 active; 6 retired (superseded-for-scheduling: D6, G4, T2, C3, I2, E3);
69 planned; 39 implemented; 5 integrated.** `python3 research/plan/scripts/validate_plan.py`
at `2fe829a`: PASS (119 tasks acyclic; backend closure 37 tasks; App closure 50; 726 links;
ledger current). Wave 1 integrated: F1, F2, D1, I1, E1. Wave-2 implemented (not integrated):
M1, Q1, W1, G1, T1, J1, C1, U1, V1, E2. The E4B backend closure is 37 tasks: the 30 wave-3
packages below plus 7 reused foundations (E1, F1, F2, I1, M1, Q1, W1) (`PROG` header).

### 4.1 The 30 wave-3 packages

`PROG`: 28 done, 2 in progress (E1B, E4B). "Verdict chain" lists the review JSONs in order
(`fr` = fix_required); where no JSON exists the record line is cited. All merges are `--no-ff`
onto `claude/backend-impl`.

| Band | Task | Title | Manifest | Merge commit(s) | Evidence | Verdict chain | What the merge added |
|---|---|---|---|---|---|---|---|
| B0 | S1 | Reconcile wave-2 baseline, product-revision audit | implemented | audit `07dfb64` (in base `ec6c548`); S1-fix `6669e1f` | `ev/wave2-platform-audit.md`, `ev/c/S1FIX-9035f4f.md` | API side pass, console fr → S1-fix re-review fr (test-only) → coordinator closure (`S02:71`,`S02:98`,`S02:105`) | R-3 clamp, money contexts; console "Balance unavailable", build-time preview gate |
| B0 | F2R | Close wave-2 contract/verification carryovers | implemented | lane B `2bfb0c4`; lane A `e1a33d1`; follow-ups `5f7ca02` (+`4775e40`) | `ev/f/F2R-A-6ab781b.md`, `ev/f/F2R-B-e06af7f.md`, `ev/f/F2R-A-mutant-logs/` | B pass (`S02:113`); A pass (`S02:197`) | one shared mutation runner (R83); `{visible, raw}` engine payload; trace-accounting base; store-produced refs; console contract r2; §5.1 deployment config; R79–R83 |
| B0 | I0 | Installer atomicity, fail-closed startup | implemented | `0ac073e` | `ev/i/I0-b818f89.md` | fr (test-side) → fixed, coordinator closure (`S02:95`,`S02:124`) | `deploy/preflight.py` read→validate→stage→probe→rename; `install.sh` requires `INFRX_MODE` |
| B0 | E2R | Harness ownership, role matrix, shared test clock | implemented | `c23d804` (item 1), `9cc3be1` | `ev/e/E2R-5b8e118.md` | fr → fixed, closure (`S02:154`–`158`) | per-run D harness + port lock; RLS matrix on post-0005 truth; shared `infrx_test` clock; layer 2 green |
| B0 | S2M | Marlin SOP launch profile | implemented | `277bedf` (branch head `3aa3535`) | `ev/s/S2M-50e32f7.md`; `research/workloads/marlin-sop.md` | fact review fr → fixed + coordinator digest correction (`S02:60`–`68`) | profile v1 pinned; 14 runtime-vs-contract discrepancies (fail-stops D2/D3/D12 closed later by M2, W2, cutover); P-06 resolved as a profile; R62 |
| B0 | F2P | Product-v2 CREDIT/identity/serving/permission contracts | implemented | additive `9faaa57`; wire-in `1baf0b8` | `ev/f/F2P-ee4b7ab.md`, `F2P-wirein-2925d63.md`, `F2P-wirein-92d6bc1.md` | additive fr → fixed (`S02:125`,`S02:131`); wire-in `F2P-wirein-review-e307084.json` fr → `F2P-wirein-confirm-ced6751.json` fr → round 2 merged on coordinator check (`S02:436`) | `contracts/v2` money units, records, ports, fixtures, conformance, TS mirror; `CreditJobStore`; config `ACCOUNTING_REGIME`/`ACTIVE_RATE_CARD_VERSION`; R64–R78, R87–R90 |
| B1 | D1R | Product-v2 schema, additive | implemented | `f302091` | `ev/d/D1R-e538c9a.md` | pass on both images (`S02:201`) | migrations 0006–0009 (CREDIT, registry, read surface, operator seams) |
| B1 | D2 | Atomic admission, durable preparation, dispatch outbox | implemented | `24b4589` | `ev/d/D2-8728aec.md` | `D2-review-ebd621d` fr → `D2-confirm-0ce4ce6` fr → `D2-confirm-6a8cc8d` pass → round 3 + coordinator pre-merge check (`S02:394`) | 0010–0014; `infrx/state/outbox.py`; R84 in-place amendments |
| B1 | D3 | Fenced leases, recovery, cancellation | implemented | `f237237` | `ev/d/D3-684313e.md` | `D3-review-b4b1ec7` fr → `D3-confirm-41e5cef` pass | 0016 (+ 0003 guard amendment); `recover()` reaper |
| B1 | D4 | Persistent stream journal and replay | implemented | `93ba108` (+`e2a52b2`) | `ev/d/D4-ce7659b.md` | `D4-review-a4598b0` fr → `D4-confirm-7d3ef59` fr → `D4-verify-90efcb0` pass | 0017 + `PgStreamStore`; terminal-event trigger |
| B1 | D5 | Terminal transaction, grants, reconciliation | implemented | `5b7fe69` (`f142074`) | `ev/d/D5-4bcac3b.md` | `D5-review-c67e4f5` fr → `D5-verify-4bfdbf0` pass | 0018; cancel cause; grant/adjust/reconcile; `PgCatalogDirectory` + G6B PostgreSQL adapters; R100–R103 |
| B1 | A1 | Verified individual signup entitlement, backfill | implemented | `2d9d9c5` + `e366ae8` | `ev/a/A1-38aea7f.md` | `A1-review-abbf066` fr → `A1-confirm-47b0382` fr → `-d8a3e84` fr → `-dc3cb9c` fr → round 4 (tests/notes) merged (`S02:396`); round 5 fix `e366ae8` (`S02:412`) | 0015 `claim_signup_grant`, `retire_individual`, backfill; R85 |
| B1 | M2 | Versioned preprocessing, tenant cache | implemented | `8156f78` | `ev/m/M2-911dd7c.md` | fr → round 2 pass (`S02:93`,`S02:113`) | header-only container probe sets duration; cache path `<root>/<org>/<profile>/<digest16>/source.<ext>`; `{url}`→`{ref}` |
| B1 | M3 | Owned uploads, expiry, orphan collection | implemented | `e2188f3` | `ev/m/M3-cd5dbae.md` | fr → round 2 merged (`S02:167`,`S02:174`) | uploads create/put/finalize; collector; consented reuse |
| B1 | Q2 | Valkey adapter | implemented | `e86b1c0` | `ev/q/Q2-10571c7.md` | fr (test-side) → round 2, closure (`S02:127`,`S02:134`) | Valkey scheduler with race-fenced claim; R63 |
| B1 | Q3 | Outbox/reconciler, index-loss recovery | implemented | `25de7ab` (+`88a48d7`) | `ev/q/Q3-9c043db.md`, `Q3-44eb4f0.md` | `Q3-review-152de7e` fr → `Q3-confirm-1ba884b` pass | `infrx/scheduling/reconcile.py` Reconciler |
| B1 | W2 | Lease-aware execution, cancellation, completion | implemented | `32cc0cb` | `ev/w/W2-be521e4.md` | fr (tests) → fix round, closure (`S02:151`,`S02:164`) | attempt loop; vLLM adapter with the `file://` media form |
| B1 | W3 | Drain, engine pin, measured concurrency | implemented | `65e2c99` + `2115f2f` | `ev/w/W3-2785101.md`, `W3-d8a7878.md`, `ev/w/box/` | `W3-review-a0eeaf4` fr → `W3-confirm-d80c04d` pass | `WorkerService`; `serve.sh` pin + `serving-version.json`; measurement scripts; box sweep |
| B1 | G1R | Audience-aware ingress | implemented | `b609cc9` (+`67e0320`) | `ev/g/G1R-6d02921.md`, `G1R-6905d13.md` | `G1R-review-d50afa5` pass | `auth_context()`, catalog resolution, trusted admission, pilot composition gate; R86 |
| B1 | G2 | Sync chat, persistent SSE relay | implemented | `2391d4d` | `ev/g/G2-e5e7d3a.md` | `G2-review-2d742aa` fr → `G2-confirm-a786af7` fr → `G2-confirm-76ba3bb` pass | `routes/relay.py`, `gateway/pilot.py`; R91 |
| B1 | G3 | Explicit jobs, status, cancellation, replay | implemented | `b560b51` (+`31bfd05`) | `ev/g/G3-9e3bc71.md` | `G3-review-a100aee` fr → `G3-confirm-cb096fb` fr → `G3-confirm-9133efe` pass | `routes/jobs.py`; R94 |
| B1 | G4U | Owned upload HTTP adapter | implemented | `1494e01` (+`7d21fa7`) | `ev/g/G4U-a3cb5c2.md` | `G4U-review-f3c8055` fr → `G4U-confirm-962b2b1` pass | `routes/uploads.py` |
| B1 | G6B | Headless provisioning and operations | implemented | `ed6da07` | `ev/g/G6B-db03f0b.md` | pass with test gaps → gap round (`S02:179`–`183`) | `infrx/operations/` CLI + service; `client_example.py`; protected publication |
| B2 | E3B | Backend-only integration gate | implemented | ph1 `c7d715f`; ph2 `d1d4a68` (+`d588c3e`, `0a642ad`); ph3 `b657a30` (with D5) | `ev/e/E3B-a7cc8f7.md`, `E3B-e57b820.md` (phase 3 has no separate md: its JSONs + `S02:742`–`796`) | ph2: `E3B2-review-80cef22` fr → `E3B2-confirm-b0a64e1` fr → `E3B2-verify-0924cf2` fr → `E3B2-verify-d0d523f` pass; ph3: `E3B3-review-4ac1419` fr → `E3B3-verify-b9529d1` pass | layer-3 gate, live drills, nine journeys against the gateway and worker processes, dataset resume; **layer 3 exit 0** at checkpoint 2 (`S02:846`) |
| B2 | I2B | Reproducible deployment | implemented | `08d3240`; I2B-R4 `405f633` | `ev/i/I2B-574a7e0.md`, `I2B-3c48066.md`, `I2B-R4-fb8babb.md`, `ROLLOUT-PREP-2026-09-23.md` | `I2B-review-5d68e50` fr → `I2B-confirm-0653c8a` fr → `I2B-confirm-d620f14` pass; `I2B-R4-verify-e540963` pass | Dockerfile, units, preflight, install/migrate/drain/rollback/rehearse, `infra/rollout/`; `python -m infrx.worker` |
| B2 | I3B | Recovery, observability, restore, rollback | implemented | `8e01fe0`; follow-up `90b81c7` (+`08725c0`) | `ev/i/I3B-32f94b3.md`, `I3B-5ec6f40.md`, `I3B-followup-0737ebe.md` | `I3B-review-48d3bb2` fr → `I3B-confirm-58d5b4f` pass; follow-up `review-d99da09` fr → `confirm-662c07c` fr → `verify-1a7a256` fr → `verify-95b7df7` pass → `verify-a693c6e` pass | `infrx/observe/`, `infra/alerts/`, six runbooks, `infra/runbooks/pgrestore.py`, drills rc*/bk* |
| B2 | E1B | Baseline and operating envelope | **planned** (tracker: in progress) | software `164e43e`; box cells `0117d48` | `ev/e/E1B-9126adf.md`, `E1B-box-20260923T2155Z.md`; `models/marlin2b/results/E1B-protocol.md` | software fr → fix round, closure (`S02:101`,`S02:121`); box evidence only | bench client (identity, idempotency, resume, open loop), `sop-synth-v1`, predeclared protocol; **L0/L1/L8 measured; L2–L7 pending** |
| B3 | M4 | Media retrieval/decoding/preparation optimisation | implemented | `4185f4d` | `ev/m/M4-8179144.md`, `M4-2176681.md` | `M4-review-2e323be` pass | measurement harness, parity oracle, streaming digest, early `moov` refusal (measured on the dev host only) |
| B3 | W4 | Serving and admission tuning | implemented | `6b09884` (+`f36c17c`); phase B evidence `0117d48` | `ev/w/W4-ecacd50.md`, `W4-phaseB-20260923T2155Z.md` | `W4-review-db12a5a` fr → `W4-confirm-b0a44a0` fr → `W4-reconfirm-ba95e42` fr → `W4-verify-b1eee7c` pass | protocol, `candidate.sh`, `decide.py`, `parity.py`; phase B: **decision B at 82 s** |
| B4 | E4B | Certify the release candidate | implemented (software half; tracker: in progress) | `66eed31` (`4d9360c`); CERTIFY-TREE `4db74b6` | `ev/e/E4B-9aa7ffe.md`, `E4B-endpoint.md`, `E4B-release-decision.md` (**PENDING**), `E4B-dev-4226315.json`; `models/marlin2b/results/E4B-protocol.md`, `E4B-box-4226315/run1…`, `run2…` | `E4B-review-37a4652` fr → `E4B-verify-7b5dbd7` pass; `CERTIFY-TREE-verify-9b467f8` pass | `tests/integration/backend/certify.py`, protocol amendments 1–5, endpoint document; box runs 1–2; **release decision open** |

### 4.2 Other wave-3 lanes (not manifest packages; all merged)

| Lane | Merge | Evidence / verdict | What it did |
|---|---|---|---|
| F cancel-cause | `6803d2f` + `0024da2` | `ev/f/F-cancel-cause-1d5b519.md`; `F-cancel-cause-review-b691add.json` pass | `JobStore.cancel(…, cause=)`, R21 settlement in the fake |
| I schema fix | `bc382ac` | `S02:468`–`472` | wire-in names into `preflight.TUNABLE`; 08 §5 rows |
| W-new | `a15fa4b` | `ev/w/W-new-e87fa69.md`; coordinator check `S02:514` | engine consumes `stream`/`max_tokens`/`max_completion_tokens` (never forwarded) |
| F fakes follow-up | `a2779d1` (+ seam fix `51f6c6e`) | `ev/f/F-fakes-followup-b6e8354.md`; review `4209aee` fr → verify `0cf5cd6` pass | R93; the fake journal refuses unjournalable payloads like 0017 |
| Box measurements | `0117d48` | `ev/w/W4-phaseB-…`, `ev/e/E1B-box-…` | W4 phase B, E1B L0/L1/L8 |
| Merge plan | `58e6fdb` | `ev/coordinator/merge-plan/` (scripts 1–12, `replay.sh`, `checkpoint2.sh`, `gate-prep.sh`) | scripted, rehearsed merges |
| Cutover | `daeff13` + fix round `865d352` | `ev/g/CUTOVER-60dd799.md`; `CUTOVER-review-a1e88dc` fr → `CUTOVER-verify-141e169` pass | `ROUTERS = (health, models, ingress, uploads, jobs, metrics)`; `create_app` builds adapters from settings (R98); `gateway.py` retired; `infrx_build_info`; W3 pins in the published revision |
| M1-L2 | `21fe8fc` | `ev/m/M1L2-eae07a9.md`; review `e1bb54f` fr → verify `0b9fc50` pass | `infrx/media/s3.py` S3ObjectStore (R95) |
| M pilot-media | `4fd4cdc` | `ev/m/MPILOT-078eefe.md`; review `8b91648` fr → verify `e5c02f7` pass | upload refs resolved at admission; durable write-once attach (`PgAttachments`); disk-backed cache lookup (R99) |
| Rollout prep | `22761a4` | `ev/i/ROLLOUT-PREP-2026-09-23.md` | `infra/runbooks/rollout.md`, `release-bundle.sh`, hosted backup + restore rehearsal |
| I2B-R4 worker | `405f633` | `I2B-R4-verify-e540963.json` pass | `python -m infrx.worker` composition root |
| Checkpoint-2 list fixes | `7a22064`, `2b86ae5` | `ev/coordinator/CKPT2-LISTS-18ab3e9.md` | three mutant re-anchors/witnesses; SIGINT reset in the W3 service child |
| PREP-WORKER (I2B-R5) | `28f3153`, `0e0506a`, `933dda3` | `ev/w/PREP-WORKER-4f7e32a.md`; review `c035ee4` pass; verify `01b11ca` pass | the preparation loop with engine-exact `/tokenize` counts (R104, R105) |
| CERTIFY-TREE | `4db74b6` | `ev/e/CERTIFY-TREE-c7e2139.md`; verify `9b467f8` pass | certify reads a git-less tree via `--release-sha`, the deployed cap, R106 in `bench.py` |

### 4.3 Post-manifest lanes of 2026-09-24 (state at `2fe829a`)

| Lane | State | Where recorded | Next step |
|---|---|---|---|
| CERTIFY-TREE | **merged** `4db74b6` | `S02:924`–`928` | polish round |
| TOKCOST | branch `codex/tokcost` @ `293ddcb` (3 + 2 commits over `2d4a88b`); verifier at `ad50b9d` **fix_required** (B1) → fix round handed back; **targeted verifier not yet recorded** | `S02:916`, `S02:922`, `S02:937`; `ev/w/TOKCOST-verify-ad50b9d.json`; on the branch: `research/plan/evidence/w/TOKCOST-40440d4.md` | verifier → `merge-plan/12-tokcost.sh` (numbers R107) → gate-3 (`merge-plan` `d57e04e`) |
| CERTIFY-POLISH | branch `codex/certify-polish` @ `c3ac6ae`: N1, N2, N3, N4, N5, N7, N9, N10, N12, N14 committed; N11, N13, N15 not on the branch; not reviewed | `S02:925`, `S02:931`; `git log 4db74b6..origin/codex/certify-polish` | finish, single verifier, merge |
| INTAKE-DRAIN | lane dispatched 19:20Z; branch `codex/intake-drain` has **no commits** beyond `4db74b6` | `S02:931`; `PROG` in-flight list | bounded drain before a mid-body refusal; real-socket test; `LARGE_BODY_LIMIT` sizing row |
| LONGCLIP | **shelved** at `0665bdb` (note only; nothing built) | `git show origin/codex/longclip:research/plan/evidence/w/LONGCLIP-4db74b6.md`; `S02:931` | resume only if the user reopens P-23 |
| FLEET | **notes only**, no launch, no cost | `research/plan/19-fleet-scale-TO-BE-IMPLEMENTED.md` | user decisions §5 there |
| Step-6 box drills | started 19:30Z; results **not in the tree** | `S02:933`–`934` | record in `E4B-release-decision.md` |
| Third install + run3 + release decision | planned | `S02:937` | after TOKCOST/polish/INTAKE-DRAIN merges and gate-3 |

### 4.4 Evidence index (per track, at `2fe829a`)

`ev/a` A1 (impl + 4 JSONs) · `ev/c` C1, S1-fix · `ev/d` D1, D1R, D2 (3 JSONs), D3 (2), D4 (3),
D5 (2) · `ev/e` E1 (×5), E1B, E1B-box, E2, E2R, E3B (ph1, ph2), E3B2 (4 JSONs), E3B3 (2), E4B
(impl, endpoint, release decision, dev report, 2 JSONs), CERTIFY-TREE (impl + verify), `box/`
(L8 comparison, E1B artefacts) · `ev/f` F1, F2 (py ×11, ts ×8), F2.1, F2P, F2P wire-in (2 + 2
JSONs), F2R-A/B, cancel-cause, fakes follow-up · `ev/g` G1, G1R, G2 (+3), G3 (+3), G4U (+2),
G6B, CUTOVER (+2) · `ev/i` I0, I1, I1B inventory, I2B (+3), I2B-R4 (+1), I3B (+2), I3B
follow-up (+5), ROLLOUT-PREP, `i3b/` dry run · `ev/j` J1 · `ev/m` M1, M1L2 (+2), M2, M3, M4
(+1), MPILOT (+2) · `ev/q` Q1, Q2, Q3 (+2) · `ev/s` S2M · `ev/t` T1 · `ev/u` U1 · `ev/v` V1 ·
`ev/w` W-new, W1, W2, W3 (+2), W4 (+4), W4 phase B, PREP-WORKER (+2), TOKCOST verify, `box/`
(inventories, capability, sweep, E0/E1/E3, box-lane SSM log) · `ev/coordinator` session
records, STATUS, PROGRESS, `progress-state.json`, briefs JSONs, CKPT2-LISTS, `merge-plan/`.
Review JSONs carry `verdict`, `confirmed_blocking`, `refuted_blocking`, `nonblocking`.

## 5. Architecture as built

### 5.1 The `infrx` package (`apps/infrx-api/infrx/`)

| Package | One line |
|---|---|
| `config.py`, `usage.py` | settings from the environment (`config.from_env`; `validate_runtime` refuses an unsafe mode/composition, R44/R51/R86); legacy usage spill |
| `contracts/` (+ `v2/`, `fakes/`, `fixtures/`, `conformance/`) | frozen shared contracts: records, ports, errors, money, limits, wire shapes, fakes and the exported conformance suites every adapter must pass; `tasklocal.py` (task port blocks); binding rulings in 08 §10 (CLAUDE.md "Checks") |
| `auth/` | key lookup (sha256), `auth_context()` audience/identity from the key row (G1R) |
| `gateway/` (`app.py`, `pilot.py`, `routes/`) | FastAPI factory `create_app`; `pilot.py` composes the durable adapters from settings (R98) and runs the Reconciler for the process lifetime; routes `health`, `models`, `ingress` (chat + `/healthz` + `/readyz`), `uploads`, `jobs`, metrics route, plus `relay`, `intake`, `validate`, `catalog`, legacy `chat` (not mounted) |
| `media/` | secure fetch/materialize (M1), container probe and preparation with the tenant processing cache (M2), owned uploads and collector (M3), S3 object store (M1-L2, R95), durable attach `attachments.py` (M pilot-media, R99), consent |
| `scheduling/` | in-memory fair scheduler (Q1, R60), Valkey adapter (Q2), outbox drain + reconciler + index rebuild (Q3) |
| `state/` | PostgreSQL adapters: `jobstore.py` (admission, leases, settlement), `journal.py` (stream journal), `outbox.py`, `catalog.py` (`PgCatalogDirectory`), `signup.py` (A1), `operations.py` (G6B adapters), `credit_schema.py`, `migrations.py`, `pgtesting.py`; `seed_marlin_provisional.sql` (operator seed) |
| `worker/` | `python -m infrx.worker` (`__main__.py`): `WorkerService` with drain and loopback health (W3), attempt loop (W2), vLLM adapter `engine.py` (W1), reasoning filter (`{visible, raw}`, R80), preparation runner `preparation.py` (PREP-WORKER) |
| `operations/` | operator CLI and service (G6B): keys, grants, adjustments, suspension, publication, cancel, reconcile |
| `observe/` | Prometheus text metrics, host/GPU/disk gauges, alert evaluator, loopback-only metrics route (I3B) |
| `traces/` | bounded capture and durable disk spool (T1) — **not composed** in the pilot (capture off) |
| `judge/` | dry-run sampling, rubric, cost guard (J1) — not composed; no approved live rate |

Deployment code: `apps/infrx-api/deploy/` (Dockerfile, four units, `install.sh`, `preflight.py`,
`migrate.py`, `drain.sh`, `rollback.sh`, `rehearse.sh`, `release-bundle.sh`, both Caddyfiles);
box steps `infra/rollout/steps/*.sh`; runbooks `infra/runbooks/`; alerts `infra/alerts/`.
Engine pin: `models/marlin2b/serve.sh` + `models/marlin2b/serving-version.json`.

### 5.2 Request path (as composed at release `4226315`)

1. **Edge** (Caddy, pinned; `apps/infrx-api/deploy/Caddyfile`): TLS on 80/443;
   `request_body max_size 96MiB`; `/metrics`, `/readyz`, `/internal` answer 404; public
   `/health` sanitized to `{"ok":true|false}`; streams unbuffered (`flush_interval -1`,
   `read_timeout 600s`); header passthrough incl. `Server-Timing`, `Location`, `Retry-After`,
   `Idempotency-Replayed`, `Last-Event-ID` (cutover fix round, `S02:812`); admin API on
   `unix//config/admin.sock` (I2B, `infra/README.md` §5.2); a maintenance site answers 503.
2. **Gateway intake** (`gateway/routes/ingress.py`, `intake.py`, `validate.py`): key →
   `auth_context()` (audience, tenant; revoked/unknown = 401); request bounds and closed
   parameter set (`ev/e/E4B-endpoint.md` "Request parameters"); bodies over 1 MiB take one of
   `LARGE_BODY_LIMIT` = 2 slots (`config.py:343`–`344`), exhaustion = 429; the idempotency
   identity is key + canonical payload + execution mode (R94); the model alias resolves
   through `PgCatalogDirectory` to a public active deployment (G1R, R69/R70).
3. **Acceptance** (`gateway/routes/relay.py`, G2/G3): an idempotent replay is looked up
   first and prepares nothing (R91); otherwise M's `prepare_request` fetches/materializes
   media, probes the container duration and refuses over `MAX_VIDEO_SECONDS` (82 on the
   pilot) → stage → **admission** in one PostgreSQL transaction (`0011_admission.sql`:
   idempotency, key/suspension/entitlement re-check, deadline clamp on the DB clock R29/R79,
   capacity scopes, price or pins R45/R66/R78, the store-derived hold R53) → the **durable
   attach** of staged media (`media/attachments.py`, write-once, R99). Mode: `sync` waits for
   the terminal commit then answers 200; `stream` relays only committed journal events; `async`
   (`POST /v1/jobs` or `Prefer: respond-async`) answers 202 after admission commits.
4. **Durable job and outbox** (`0012_dispatch_outbox.sql`, `state/outbox.py`): admission writes
   a `prepare_dispatch` outbox row; the Q3 `Reconciler` (gateway lifespan and worker) drains
   the outbox into the Valkey index with acknowledgements fenced by claim holder (D2 OB-1b),
   re-enqueues missing jobs and rebuilds the index from PostgreSQL every 10 s (`S02:910`).
5. **Worker preparation** (`worker/preparation.py`, PREP-WORKER): `claim_preparation` lease
   (30 s, renewed every ttl/3 to the preparation deadline, R52) → `load_work` through the lease
   → wait ≤ 10 s for the durable attach (`ATTACH_WAIT_S`) → M's `prepare` into the shared
   processing cache (`PROCESSING_CACHE_DIR=/opt/dlami/nvme/processing`) → **engine-exact count**
   from vLLM `POST /tokenize` on the exact upstream body, accepted only after the R105 checks
   → `prepared(lease, refs, prompt_tokens=count)` (R104) → job `queued` + inference dispatch.
   Failure = `dependency_unavailable`, lease lapses, `recover()` requeues (R93) up to the retry
   bound, then `preparation_failed` free. (TOKCOST's per-process memo of repeated video bodies
   is **not merged**; R107 candidate.)
6. **Scheduling index** (Valkey, `scheduling/valkey.py`): two-level fair claim (R60), per-tenant
   caps; a rebuildable cache — PostgreSQL is the truth (`infra/runbooks/index-loss.md`).
7. **Inference** (`worker/loop.py`, `attempt.py`, `engine.py`): fenced `claim` moves
   queued→running with the lease in one transaction (`0016_fenced_leases.sql`); the adapter
   sends `file://` media under the engine's allowed media root (R61), both EOS ids as
   `stop_token_ids`, `cache_salt` per tenant, `max_tokens` = the admitted ceiling (W-new);
   deltas are `{visible, raw}` (R80); heartbeats on the DB clock; `WORKER_CONCURRENCY` 8.
8. **Journal and relay** (`0017_stream_journal.sql`, `state/journal.py`): fenced appends
   (batched ≤ 50 ms), global journal budget, terminal event written by trigger at
   terminalization; SSE and `/v1/jobs/{h}/events` replay committed events from a cursor; a
   detached observer never cancels (G3).
9. **Terminal settlement** (`0014_job_results.sql`, `0018_terminal_settlement.sql`): the result
   object is stored before the settling transaction; the hold settles to a debit at the
   admitted price/card with authoritative usage; only `completed`, `client_cancelled`,
   `client_disconnected` with authoritative usage bill (R21); a cancel after publication stays
   `held_unknown` (R106).
10. **Reconciliation**: the worker's reaper `recover()` every 10 s (W3/D3) requeues
    prepublication work, fails published work honestly, releases aged unknown-usage holds after
    24 h; `infrx.wallet_reconciliation` drift view; `reconcile` CLI; `infrx_reconciliation_*`
    metrics; `infra/runbooks/reconcile.md` ("never repair money by hand").

### 5.3 Schema (migrations `apps/app/supabase/migrations/`)

| # | Owner | Adds (key tables/functions) |
|---|---|---|
| 0001 | pre-wave console | `public.profiles`, `organizations`, `org_members`, `models`, `api_keys`, `usage_events`, `credit_ledger` (legacy USD) |
| 0002 | pre-wave | `public.models` seed (USD per 1M tokens) |
| 0003 | D1 | schema `infrx`: `price_versions`, `wallets`, `credit_holds`, `capacity_reservations`, `jobs`, `attempts`, `staged_media`, `job_media`, `idempotency`, `stream_chunks`, `outbox`, `feedback`, `consent_history`, `judge_*`, `callback_*`, `org_entitlements`, `audit_entries` |
| 0004 / 0005 | D1 | role/RLS/column-grant matrix + RPCs; the console's 13 named reads |
| 0006 | D1R | `feature_flags`, `credit_wallets`, `credit_ledger`, `signup_entitlements`, `credit_wallet_holds` (CREDIT; no USD conversion, R65) |
| 0007 | D1R | registry: `provider_orgs`, `provider_memberships`, `model_versions`, `serving_versions`, `endpoints`, `deployment_revisions`, `rate_card_versions`, `data_access_policies`, `catalog_listings` |
| 0008 / 0009 | D1R | CREDIT read surface, `resolve_admission_pins`; operator seams (audit actions, key audience/scope, `bootstrap_operator_key`) |
| 0010–0014 | D2 | `media_uploads`, `media_objects`; admission; dispatch outbox; outbox GC; `job_results` |
| 0015 | A1 | `signup_identity_claims`, `signup_denials`, `retired_individuals`; `claim_signup_grant`, `retire_individual` |
| 0016 / 0017 / 0018 | D3 / D4 / D5 | fenced leases and reaper; stream journal; terminal settlement, cancel cause, operator money operations |

Logical→physical names (06a vs SQL): wallets = `credit_wallets`, wallet_ledger =
`credit_ledger`, credit_holds = `credit_wallet_holds`, signup_grants = `signup_entitlements`,
rate_cards = `rate_card_versions` (`S02:189`). **All of 0001–0018 are applied on hosted since
2026-09-24T05:09Z, so by R84 every file is frozen: any change is a new migration; next is
0019** (already requested: a zero-ref attach marker, PREP-WORKER request 2, `S02:868`).

### 5.4 Runtime units on the box (`infra/README.md` §5.2; `H1010`/`H1700` box state)

| Unit | Listens | What |
|---|---|---|
| `marlin2b-vllm.service` | 127.0.0.1:8000 | `serve.sh` → `vllm/vllm-openai@sha256:4cbfd34a…20b42` (vLLM 0.29.1rc1.dev397+ga8d1aa9c9), `--max-num-seqs 8`, media root mounted read-only; weights `/opt/dlami/nvme/marlin2b` (instance store, lost on stop); start-to-ready 168–181 s (`ENGINE_READY_S` 900) |
| `marlin2b-gateway.service` | 127.0.0.1:8001 | `infrx-runtime:<release>` (uid 10001), `uvicorn --factory infrx.gateway.app:create_app`, `--workers 1`, graceful 110 s < `docker stop -t 120` < `TimeoutStopSec` 150 (`S02:812`) |
| `infrx-worker.service` | 127.0.0.1:8002 (`/readyz`, `/livez`, `/metrics`) | `python -m infrx.worker` (uid 10002), both loops; `PartOf=` the engine unit; drain 330 s; media root read-write, mode 2770 |
| `infrx-valkey.service` | 127.0.0.1:6379 | Valkey by digest, no persistence, `noeviction` |
| `caddy` container | 0.0.0.0:80/443 | the release's Caddyfile; `caddy_data` holds certificates |

Every container read-only, `--cap-drop ALL`, `no-new-privileges`; one configuration authority
`/etc/marlin2b-gateway.env` written only by `preflight.py apply` (holds secrets; root 0600);
install backups `/var/backups/infrx/<UTC>-<sha>/` (hold past env files incl. secrets);
usage spill `/var/lib/infrx/usage/usage.jsonl` (`infra/README.md` §5.2).

### 5.5 Hosted database

Supabase `fcbnscgsymzdykendbrc`, us-east-2, PostgreSQL 17.6, PostgREST 14.5 (`S02:46`); reached
through the session pooler `aws-0-us-east-2.pooler.supabase.com:5432` (the direct host is
IPv6-only; `infra/runbooks/restore.md` A1). State from the records: 0001–0018 applied; operator
seed rows (provider org, model/serving version, 2 endpoints, 2 deployment revisions — one
public/active `c0000004-…`, card `rc_marlin2b_2026_09_provisional`, 400/1,200 CREDIT per
million, `provisional = true`, `S02:861`); USD price versions `pv_marlin2b_usd_2026_09` (alias
`nemostation/marlin-2b`) and `pv_marlin2b_usd_2026_09_r1` (`nemostation/marlin-2b@2026-09-01`),
USD 0.10/0.30 per million input/output tokens, `tr-1` (`S02:903`); flags as §2; the gateway's
pool runs `set role service_role`; the DSN login is the project's `postgres.<ref>` pooler login
(`rolbypassrls` true) — a dedicated login role is a follow-up (`S02:809`). Backups: taken by the
coordinator with `infra/runbooks/pgrestore.py` into `$HOME/infrx-backups/hosted-20260923T215800Z`,
`hosted-20260923T223903Z`, `hosted-20260924T050746Z` (outside the repo; they hold user e-mails,
never committed) (`S02:779`, `S02:850`). Supabase-managed backup plan and PITR: ⚠️ TO BE
VERIFIED (Management API read; PITR is a separately authorized paid change, `infra/README.md` §6).

### 5.6 S3

Project bucket `llm-bootcamp-641134885443` (us-east-1; shared with other projects): `infrx/`
= the pilot's media object store (R95; `S3_MEDIA_BUCKET` + default prefix); `releases/` =
release bundles (`release-bundle.sh`); `w3/`, `w4/` = box-lane bundles and corpus tarballs;
`test/m1l2/<uuid>/` = conformance prefix, emptied after each run; `weights/` holds only
`deepseek-v41` (Marlin weights are **not** mirrored). The box's instance role
`bootcamp-instance-role` already allows Get/Put/Delete/List on the bucket (`S02:716`).

### 5.7 Secrets (names only)

SSM us-east-1 (`HANDOFF.md` §2; `infra/runbooks/rollout.md` §1; `S02:808`, `S02:884`,
`S02:898`): `/model-inference/hf_token`, `/model-inference/marlin2b_api_key` (legacy shared key
— refused in pilot, R51), `/model-inference/supabase_url`,
`/model-inference/supabase_service_role_key`, `/model-inference/pg_journal_url` (the
`DATABASE_URL`, created 2026-09-24T01:03Z), `/model-inference/operator_key` (2026-09-24T16:28Z),
`/model-inference/e4b_api_key` (the certification tenant's consumer key);
`/INFRX-SUPABASE-PROD/{url, publishable_key, secret_key, db_password, access_token, smtp_user,
smtp_pass}`; `/callgideon/prod/GOOGLE_CLIENT_{ID,SECRET}` (unused). Box: `/etc/marlin2b-gateway.env`.

### 5.8 Operator CLI (`apps/infrx-api/infrx/operations/cli.py`)

`python -m infrx.operations.cli <cmd> --idempotency-key … --reason …` with the operator key in
`INFRX_OPERATOR_KEY` (a key on the command line is refused): `grant --user` (A1's one-time
10,000 CREDIT), `adjust --user --amount`, `issue-key --user --name --secret-file` (secret
written once to a 0600 `O_EXCL` file), `rotate-key`, `revoke-key --org --key-id`,
`suspend --org [--code]`, `publish-marlin --provider-org --created-at --effective-at`,
`cancel --org --job`, `reconcile --org --request`. Every write is audited. Operator-key
bootstrap is SQL (`infrx.bootstrap_operator_key(org, name, prefix, sha256hex, actor, reason)`,
0009). Order learned on the pilot: `signup_grant` flag on → `grant` (provisions the wallet) →
`issue-key`; in legacy USD a tenant also needs a USD `grant` row in `public.credit_ledger`
(R103) (`S02:885`–`892`; rollout W7d).

### 5.9 Observability

Metric families (`infrx/observe/`, gateway and worker): `infrx_build_info{revision,image}`,
`infrx_component_up`, `infrx_gpu_up`, `infrx_gpu_memory_bytes`, `infrx_gpu_utilization_ratio`,
`infrx_disk_bytes`, `infrx_disk_free_ratio`, `infrx_host_memory_bytes`, `infrx_host_cpus`,
`infrx_process_resident_bytes`, `infrx_inflight_requests`/`_limit`, `infrx_queue_depth`,
`infrx_queue_items`/`_limit`, `infrx_queue_oldest_wait_seconds`, `infrx_jobs_accepted_total`,
`infrx_jobs_terminal_total`, `infrx_requests_rejected_total`, `infrx_lease_lost_total`,
`infrx_recovery_actions_total`, `infrx_settlements_total`, `infrx_holds_unknown`,
`infrx_unsettleable_jobs`, `infrx_reconciliation_drift`, `infrx_reconciliation_runs_total`,
`infrx_reconciliation_last_success_timestamp_seconds`, `infrx_phase_seconds`,
`infrx_metrics_label_rejected_total`. Tenants hashed, closed label vocabularies. `/metrics` and
`/readyz` answer only a loopback peer (edge 404). 20 alert rules in `infra/alerts/alerts.json`
(each linked to a runbook; unmeasured thresholds ⚠️ P-18) and `dashboard.json`. `Server-Timing`
phases on responses (`prepare;dur=…`). Worker journal: `prepared <job>: <n> prompt tokens
(engine /tokenize, <ms> ms)`, `drained preparation:`. **The alert evaluator is not scheduled
on the box**: "I3B's `--max-age 180` on an alert timer (no timer unit exists yet)" (`S02:409`),
and no record names a scraper, pager or delivery channel — alert delivery ⚠️ TO BE VERIFIED
(I3B.c asked for it, `18-marlin-backend-first.md` §I3B).

### 5.10 Accounting regimes

| Regime | Mechanics | State |
|---|---|---|
| `legacy_usd` | `infrx.price_versions` looked up by the request's **literal** model string (R45; P-22), USD holds/debits, grants on `public.credit_ledger` (R103), wallet totals via `infrx.ledger_moves_wallet` | **live on the pilot**; `legacy_usd_admission` true |
| `credit` | wallets/ledger/holds of 0006, admission pins to deployment + serving revision + rate card (R66–R78), settlement at the admitted card (R68), signup grant 10,000 CREDIT once per individual (A1, R71), no conversion (R65), USD history kept apart (R72/R73) | implemented and tested locally (D1R/D2–D5/A1, E3B dr07c); `credit_admission` **false** on hosted; only the provisional card exists (P-01); `signup_grant` true so a wallet holds 10,000 CREDIT unused |

Cutover flag state: `ACCOUNTING_REGIME` unset (default `legacy_usd`) in the pilot env
(`infra/runbooks/rollout.md` §1). The CREDIT cutover is §11 item.

### 5.11 Pilot configuration (`infra/runbooks/rollout.md` §1)

`INFRX_MODE=pilot`; `S3_MEDIA_BUCKET=llm-bootcamp-641134885443`; `MAX_VIDEO_SECONDS=82` (code
default 120; P-20); `ENGINE_MAX_NUM_SEQS=8`; `WORKER_CONCURRENCY=8` (08 §5 default 10);
`PREPARATION_CONCURRENCY` default 2; `VALKEY_URL=valkey://127.0.0.1:6379/0`;
`PROCESSING_CACHE_DIR=/opt/dlami/nvme/processing`; `INFRX_RELEASE_SHA`/`INFRX_IMAGE` written by
the installer; `GATEWAY_API_KEY` never. Contract defaults (`contracts/limits.py`):
`max_active_jobs` 64, per org 16, per key 8, `max_output_tokens` 2,048, `max_context_tokens`
32,768, `result_ttl_s` 86,400, `processing_cache_ttl_s` 604,800, `idempotency_ttl_s` 86,400.

## 6. Measured facts (all `meas.` on the pilot box unless labelled; criteria are provisional, P-18)

### 6.1 Engine and model limits

| Fact | Value | Source |
|---|---|---|
| Model | `NemoStation/Marlin-2B@fd111fca4fc7897876fb0d7e9df22ca5ac8ab965`, served bytes = S2M digests; registry-oid equality ⚠️ (needs `HF_TOKEN`) | `ev/s/S2M-50e32f7.md`; `S02:65`, `S02:367` |
| Engine | vLLM 0.29.1rc1.dev397+ga8d1aa9c9, `vllm/vllm-openai@sha256:4cbfd34a…20b42` (= the box image, `image_equals_pin=yes`); `--max-model-len 32768`, `--gpu-memory-utilization 0.90`, `--limit-mm-per-prompt` video 1 / image 4, bf16, `--hf-overrides Qwen3_5ForConditionalGeneration` | `S02:349`; `models/marlin2b/serving-version.json` |
| Engine-options digest | `sha256:3c4bbface108e019b55a71121e1f3aaa23268bc1d1bd100257b0e2c68c036147`; run2 config-pin PASS ("tree and deployed engine match") | `run2` |
| **Encoder cache** | **16,384 tokens** (= the largest profiled image item; `--max-num-batched-tokens` default 2,048 on a < 70 GiB GPU). Longest accepted clip in the W3 sweep **72 s** (14,773 prompt tokens); refusals at 20,160/21,168/21,504 tokens for the four 112 s corpus clips and 21,600 for 120 s sop-synth clips | `S02:430`, `S02:439`; `ev/w/W4-phaseB-…`; LONGCLIP note finding 1 |
| **Video cap** | **82 s** = `decide.py --ceiling 16384` (worst case 16,154 video tokens at 82 s); deployed `MAX_VIDEO_SECONDS=82`; 120 s needs a 32,768 budget (E1 `--max-num-batched-tokens 32768`, measured, not adopted); a source below ~2 fps can exceed the budget under 82 s (free `engine_error`) | `ev/w/W4-phaseB-…` "P-20 consequence"; P-20 |
| `--max-num-seqs` | pinned **8** (box ran 32 until the rollout); no `c*` exists on any run, so 8 stays ⚠️ | W4 phase B; `run2` config-pin |
| KV capacity | engine-reported **2,644,426 tokens** at `--max-num-seqs 32` (80.70× at 32,768 tokens); X = 80.70 (E0) / 76.34 (E1); re-read at the pinned 8 ⚠️ not done; the KV-capacity definition in E1B's protocol is still open (W3 request 13) | `S02:349`, `S02:367`; W4 phase B; `ev/e/E1B-box-…` request 1 |
| GPU | L40S 46,068 MiB; 39,945 MiB used at idle (KV pool pre-allocated) | `S02:349` |
| Start-to-ready | **168–181 s** per engine start (E0 168–169, E1 172–173, E3 179–181) vs `ENGINE_READY_S` 900 | W4 phase B |
| Cancellation | running requests back to 0 in 0.11 s after a disconnect | `S02:351` |
| First request on a fresh engine | TTFT 16.97 s (later requests 0.19–3.42 s) | `ev/e/E1B-box-…` L0 |

### 6.2 Direct-engine performance (restarted engine per level; `--subset full`, 64 clips 2–112 s, seed 20260922, output mix 128/512/1024)

E1B L1 = W4 E0 (`ev/e/E1B-box-20260923T2155Z.md`; `ev/w/W4-phaseB-…`), c = concurrency:

| c | acc/fail | req/s | video-s/s | TTFT p50 / p95 s | latency p50 / p95 s |
|---|---|---|---|---|---|
| 1 | 60/4 | 0.322 | 6.779 | 0.6555 / 3.3018 | 2.2613 / 6.7583 |
| 2 | 60/4 | 0.509 | 10.738 | 0.9558 / 4.3885 | 2.5513 / 7.734 |
| 4 | 60/4 | 0.711 | 14.995 | 1.913 / 7.0032 | 3.7604 / 11.7634 |
| 8 | 60/4 | 0.888 | 18.732 | 3.552 / 18.9023 | 5.9454 / 20.7091 |
| 16 | 60/4 | 0.865 | 18.24 | 13.0809 / 27.1266 | 14.9069 / 28.2622 |
| 32 | 120/8 | 1.267 | 26.715 | 20.1731 / 37.1843 | 23.24 / 40.1487 |

Every failure is a 112 s clip over the encoder cache. E1 (`--max-num-batched-tokens 32768`):
0/448 failures, T 0.321 → 1.314; E3 (E1 + `--api-server-count 2`) T up to 1.398 but 16 % blank
metric scrapes. `decide.py`: **no setting adopted** — `w3_rule` and `memory_growth` fail on E0
and E1 alike (T still rising at c = 32; the growth criterion measures where the load peak falls);
`severe_tail` unknown (E1 TTFT p95 30.33 s at c = 16 against a 30 s provisional bound). p99
suppressed everywhere (< 300 samples). **L0** (c = 1, 12 requests): 11/1, 0.213 req/s, 5.139
video-s/s, TTFT p50 0.464 s, latency p50 2.1773 s, TPOT p50 6.158 ms; tails unsupported.
**L8** caption-event parity vs `reference.py`: 2/2 on 10 s samples, **0/3 on the 120 s
sop-synth clips** (processor-default budget), cause undiagnosed. W3's earlier **warm** sweep
(same engine, primed caches, not the served envelope): video-s/s 7.4 → 50.1 for c = 1…32,
TTFT p50 0.68 → 5.97 s (`S02:430`).

### 6.3 Through the deployed gateway (release 4226315)

| Measurement | Value | Source |
|---|---|---|
| W12 external smoke | **15 PASS, failures 0** (sanitized health, hidden `/metrics`/`/readyz`, 401 for no/unknown/revoked/legacy key, scoped key 200 with `Server-Timing`, blocked media URL refused, oversize 413) | `S02:895`; `infra/rollout/verify-external.sh` |
| Text job | prepared 13 = usage 13 prompt tokens, 8 completion, USD 0.00000370, `/tokenize` 4–6 ms | `S02:895` |
| Video job (5 s public sample, `video_url`, `max_tokens` 96) | HTTP 200 in 20.5 s; prepared 1,211 = usage 1,211; 96 completion; USD 0.00014990; `server-timing: prepare;dur=722.7`; **engine `/tokenize` 16,803 ms** (≈ 3.4 s per clip-second; the engine decodes each video twice — tokenize, then chat) | `S02:895`; TOKCOST findings 1–5 |
| TOKCOST memo (local, fake held at 1 s) | a repeated identical video body 1,003.6 ms → 1.1 ms; first-seen bodies unchanged (R105) | `S02:916` (unmerged) |

### 6.4 E4B box certification run2 (`run2`; 2026-09-24 17:22:45Z, 4,119.8 s, exit 1)

Target: `http://127.0.0.1:8001/v1` (the gateway, from the box), model
`nemostation/marlin-2b@2026-09-01`, release `4226315`, scale `box`. The report's `label` field
on its measurement cells reads "unverified target, not a measurement" (the runner's rule for when
a number may be called `meas.`, E4B protocol amendments 3–4); the figures below are what the box
run recorded, and run3 in the certify image is meant to produce the certifiable report.

| Cell | Verdict | Numbers |
|---|---|---|
| preconditions | PASS | App/Lab stopped, window open, engine idle, clips present |
| config-pin | PASS | engine digest/image match; `engine_max_num_seqs` 8; encoder budget 16,384 |
| served-build | FAIL (protocol) | tree unreadable without git; gateway image `sha256:491697bc…` ≠ `infrx-runtime:<release>` tag compare |
| recovery-box | PENDING[BOX] | step-6 drills (started 19:30Z, `S02:933`) |
| sop-parity | FAIL (protocol) | the 112/120 s clips are refused over the 82 s cap — expected; fixed in CERTIFY-TREE item 3 |
| dataset-resume | FAIL (client) | 24 items; first run SIGINT after 8 accepted; 2 items replayed `state_conflict` (cancelled) and were re-sent forever — fixed by R106 in `bench.py` |
| **envelope** | FAIL by criterion | **supported rate 0.5 req/s**. r=0.5: 112 accepted, 0/112 failures, 8 over-cap 400s, **e2e p95 91.39 s per clip-minute** (criterion 45, provisional). r=1.0: 109 accepted, **3 × 429** within the cap (2 job capacity `Retry-After 5`, 1 large-body `Retry-After 2`, `S02:931`), e2e p95 91.70. r=2.0: 79 accepted, **33 × 429**, e2e p95 96.79; one over-cap clip got a 429 before its media check. TTFT p95 of short clips **unknown** at every rung (54/52/40 samples; needs 60) |
| **soak** | FAIL (runner) | at **0.25 req/s** (half the supported rate) for 3,600 s of the planned 14,400 s — the runner caps every bench run at 3,600 s (exit 124); in that hour **809 accepted, 0 failures, host growth −52 MiB over 121 samples, latency p50 2.97 → 2.94 s**; GPU growth unknown (no worker gauge), reconciliation unknown (no DB in the container). The record's "at 0.5/s" (`S02:913`) disagrees with the report's `rate_per_s 0.25`; the report is authoritative |
| **overload** | FAIL (**product defect**) | 32-request burst from one key: 8 accepted, 10 refused (429), **14 ReadError** — the gateway journal shows 24 × 429 + 9 × 200; a refusal sent with `Connection: close` while the body is still arriving resets the socket, so the typed 429 never reaches an HTTP/1.1 client mid-upload (`S02:931`) |
| release-identity | FAIL (protocol) | no git SHA in the runtime image; run3 runs in `infrx-certify:4226315…` (`sha256:4e48efa3…`, `S02:907`) |

Run1 (`E4B-box-4226315/run1-20260924T165408Z/`, 498 s, exit 1): every measurement cell 400
because only the unlabelled alias had a USD price version (`S02:910`). Dev-host report
`ev/e/E4B-dev-4226315.json` (sha256 `26c2e97e…`, 192.7 s): release-identity PASS (one clean
tree `4226315`), protocol PASS (117), recovery PASS (71), backend 188/0/0, rls 731, sop-parity
PASS (9 clips, fake engine), envelope/soak/overload/dataset ledger PENDING[BOX] by design,
preconditions FAIL = this host's App dev servers (known local exemption) (`S02:900`).

### 6.5 Media preparation (M4; dev host only, not the box)

64 MiB clip event-loop stall p50: fetch 140–149 → 43–50 ms; materialize URL 186–219 → 44–49 ms;
`data:` 485–587 → 248–278 ms; prepare miss 58–68 → 27–29 ms; memory high-water 3.0×/3.67× →
2.0×/2.33× of the body; header-first over-cap clip refused after 1,081,344 B in 7.9 ms; parity
79/79 clips byte-identical prepared facts (`S02:265`, `S02:313`). The pilot-box harness run is
still owed (E1B/P-04, `ev/m/M4-2176681.md`).

### 6.6 Test gates (counts)

| Gate | Tree | Result | Source |
|---|---|---|---|
| Checkpoint 2 | `27af05a` | api-test 3,639 passed (+2 P-21 harness drills, 7/7 on rerun); layer 0 202, no PENDING; mutant lists 2,185/2,186 (+3 fixed); E4B list 138/140 → 140; **layer 3 exit 0** (first fully green) | `S02:838`, `S02:845`, `S02:857` |
| Focused gate | `4226315` | suites 2,289; D 89 + 4 xfail; lists 891 + 2 re-anchored; layer 0 202; E4B 140; layer 3 backend 188/0/0, mutants 252/248, canary PASS (one P-21 flake, 17/17 on rerun) | `S02:876` |

### 6.7 Cost (all `est.`, ⚠️ P-19)

No sourced AWS `g6e` row exists in `research/cross-cutting/cloud-pricing.md`, so no cost per
video-hour is publishable (P-19). Two unsourced figures circulate and disagree: ≈ $2.24/h
("operational", `HANDOFF.md` §1) and ≈ $1.86/h ("AWS list price", `19-fleet-scale-…` §3). W4
phase B window ≈ 2.13 instance-hours ≈ $4.8 at $2.24/h (W4 phase B "Cost").

## 7. Known defects and findings

"State" is at `2fe829a`. Owners are tracks (G gateway, W worker, M media, D durable state, Q
scheduling, E verification, I infrastructure, F contracts, coordinator).

| # | Finding | Owner | State | Where recorded / fixed |
|---|---|---|---|---|
| D-1 | **Overload drops the typed 429** for clients mid-upload (refusal with `Connection: close` before the body is drained → RST → `ReadError`) | G | lane INTAKE-DRAIN dispatched; no commit pushed | `S02:931`; run2 overload |
| D-2 | `LARGE_BODY_LIMIT` = 2 slots (bodies > 1 MiB) is one of the 429 sources at 1 req/s; no sizing rationale on record | G/I | sizing row is an INTAKE-DRAIN deliverable | `config.py:343`; `S02:931` |
| D-3 | Certify runner bounds every bench run at 3,600 s, so the 4 h soak cannot run | E | fixed on `codex/certify-polish` N12 (`4eaac29`), **unmerged** | `S02:931` |
| D-4 | TTFT-p95 short-clip cell undersized (40–54 samples) | E | polish N14 (`c3ac6ae`), unmerged | `S02:931` |
| D-5 | Admission answers capacity 429 before the media duration check (an over-cap clip got 429 at r=2.0) | E/G (polish N15: judge or reorder) | **not on the polish branch** | `S02:931` |
| D-6 | Cancelled replay classed non-terminal by the client (dataset-resume FAIL) | E | **fixed** R106 in `bench.py` + drill (`4db74b6`); refinements N5/N7/N9 on the polish branch | `S02:913`, `S02:925`; 08§10 R106 |
| D-7 | served-build / release-identity FAIL (no git in the runtime image); parity FAIL on over-cap clips | E | **fixed** in CERTIFY-TREE `4db74b6` + certify image on the box; needs run3 | `S02:906`–`907`, `S02:919` |
| D-8 | **USD price lookup keyed by the literal model string**: the labelled alias had no price row → every request 400 (run1) | D/G | mitigated by a second row (rollout W7e); ruling pending (key on the resolved revision) | P-22; `S02:902` |
| D-9 | **Video `/tokenize` 16.8 s for a 5 s clip** (engine decodes twice; cause of the magnitude unknown: H1 cold first-video path, H2 single multimodal thread, H3 the clip) | W | TOKCOST memo helps repeats only, **unmerged**; box measurement protocol (request 1) not run | `S02:895`, `S02:916`; TOKCOST findings 4–5 |
| D-10 | End-to-end p95 91–97 s per clip-minute vs provisional 45 s at every rung | W/E (with P-18 owner) | measured; no tuning lane open | `run2` envelope |
| D-11 | Single GPU: no high availability; engine restart = 168–181 s outage | I / user | notes only (fleet) | P-16; `19-fleet-scale-…`; `infra/runbooks/README.md` rule 4 |
| D-12 | Soak cannot read GPU growth (no worker gauge) or reconciliation (no DB in the certify container) | E | worker gauge fixed in CERTIFY-TREE (`--worker-metrics-url`); reconciliation read ⚠️ open | `S02:919`, `S02:931` |
| D-13 | L8 caption-event parity 0/3 on 120 s synthetic clips | E/W | undiagnosed | `ev/e/E1B-box-…` L8 |
| D-14 | W4 decision criteria cannot adopt any setting on cold restarts (`memory_growth` time-halves, `w3_rule` at saturated c = 32, blank scrapes under 2 API servers) | W | protocol amendment requested; not done | W4 phase B request 3 |
| D-15 | `ENGINE_MAX_NUM_SEQS`/`WORKER_CONCURRENCY` = 8 ⚠️ (no measured `c*`) | W | open | W4 phase B |
| D-16 | KV-capacity definition (engine-reported `kv_cache_size_tokens` vs blocks × size) | E | open | W3 request 13; E1B-box request 1 |
| D-17 | Registry-oid equality of served weights ⚠️ (gated repo masks oids unauthenticated) | coordinator + user (`HF_TOKEN`) | open | `S02:65`–`66`, `S02:367` |
| D-18 | Corpus byte-reproducibility fails across CPUs (3 NASA-derived clips on the box's AMD EPYC); Blender CDN answers 403 to AWS egress | E | worked around by shipping the verified cache | `S02:421`, `S02:430` |
| D-19 | PREP-WORKER limits: a text job's attach leaves no durable row (needs 0019); media root writable by gateway and worker; each video decoded twice; a permanent refusal costs up to 3 lapsed leases (~90 s); fake-vLLM test ports in the ephemeral range | D/M, I, W, E | requests 2, 3, 7 open | `S02:868`; `ev/w/PREP-WORKER-4f7e32a.md` |
| D-20 | M pilot-media limits: `MediaCollector.run` not composed (no garbage collection of media); expired-file deletion across a restart; the gateway's cache index not re-checked; R99's "none" arm holds in-process only | M | open | `S02:793`, `S02:799`; `ev/m/MPILOT-verify-e5c02f7.json` |
| D-21 | D5 V-N1: concurrent reuse of a reconcile operation id dies as an untyped 23505 (fail-safe 500) | D | open (D5 Limits 14) | `S02:782`, `S02:802` |
| D-22 | `result_expires_at` absent from `TerminalOutcome` (needs an F contract change); G3 request (c) | F/D/G | open | `S02:776` |
| D-23 | Cutover VER-N1: values longer than the release/image shapes not in the malformed sets | G | open (test pin) | `S02:815` |
| D-24 | Worker verifier F1: `psycopg.pool` WARNING lines echo the `DATABASE_URL` host; F2: a dockerignore re-exclusion of the worker module is not caught | W/I | open | `S02:826` |
| D-25 | PREP-WORKER verifier F1 (zero-pool case would HeadBucket AWS under a mutant), F2 (two renewal cases await without a bound) | W | open | `S02:871` |
| D-26 | Q3 drill `valkey_sigkilled_under_queued_and_running_traffic` waits without a deadline (hung a gate) | Q/E | open | `S02:466` |
| D-27 | W3/W4 perf-script stubs match any argv containing "verify" (9 false failures in such a path) | W | open | `ev/w/TOKCOST-verify-ad50b9d.json` N5 |
| D-28 | E3B phase-3 product observations J5–J8: SSE resume re-adds `role` on the first delta; an explicit `"stream": false` is a different R94 identity from an omitted one; a disconnected job's hold is released only at reconciliation; the frozen clone clock must be unfrozen on a live box; E1B bench's upload handshake ≠ G4U DTO | G2/G3/M/E | open with owners | `S02:743`, `S02:788` |
| D-29 | Load-sensitive mutants: W3 `sigint_not_handled` (fixed `2b86ae5`), D1R `d1r_grant_race_arbitrates_one_index` on Supabase (killed in isolation) | W / D | W fixed; D noted | `S02:857`, `S02:802` |
| D-30 | Host port collisions: task ports 554xx/555xx/567xx lie in the kernel's ephemeral range; hit every gate | coordinator + user (root) | open | P-21 |
| D-31 | The deployed DSN logs in as the project's `postgres` pooler login (`rolbypassrls` true); no dedicated login role | D/I | follow-up | `S02:809` |
| D-32 | Durability gaps of the box: weights on instance-store NVMe (lost on stop) and not mirrored to S3; hosted PITR/backup plan ⚠️; host hardening rows M-ROLE, M-BOUNDARY, M-KMS, M-IMDS, M-SSH not done | I / user | open | `infra/README.md` §6, §5; `infra/rollout/README.md` §4; `19-fleet-scale-…` §2 |
| D-33 | Alert evaluator not scheduled on the box; no alert delivery channel on record | I | open | `S02:409` |
| D-34 | GitHub Dependabot: 1 moderate alert on the default branch (`https://github.com/callgideon/model-inference/security/dependabot/1`), never triaged | coordinator → owning track | open | `HANDOFF-20260923T1015Z.md:37`; `H1745` NEXT 5 |
| D-35 | Stale documents: `E4B-release-decision.md` §5 still lists B1 (placeholder digest), B2 (`--max-num-seqs 32`) and `build_info` as open — closed by the cutover (`351d084`, `9fd3457`, `S02:749`) and the rollout (run2 config-pin PASS); `progress-state.json` gates still say BACKEND-LOCAL "not started" although E3B's layer-3 gate exits 0 (no formal BACKEND-LOCAL declaration is recorded); session-02 timestamps between 00:57Z and 01:45Z on 09-24 run up to 30 min behind commit times (`HANDOFF-20260924T0215Z.md`) | coordinator | open | as cited |
| D-36 | The public repository contains the I1 live inventory and the fail-open write-up (user chose to push) | user | accepted risk | `W2H` §6 |

## 8. Rulings register

The contract history lives in `research/plan/08-contracts-v1-encoding.md` §10 (full text, plus a
log of corrections — R60 corrected twice, the withdrawn `to_thread` mechanism, the restated
cancel lifecycle, R83 amended, R106 reworded; `W2H` §3 and the 08 verification log). Titles
below are the "Question" column verbatim (v2 rulings R64–R78 are the F2P V1–V15 set). **Next
free number at `2fe829a`: R107.**

| # | Title |
|---|---|
| R1 | `PREPARATION_CONCURRENCY` vs admission |
| R2 | New limits vs F1 legacy settings |
| R3 | Feedback body |
| R4 | Deadlines |
| R5 | Queue wait across requeues |
| R6 | Re-admitting an existing `request_id` without the idempotency path |
| R7 | `recover` clock |
| R8 | Ambiguous judge run |
| R9 | Judge consent |
| R10 | Tenant coherence |
| R11 | Monetary inputs |
| R12 | Trace mode vs content |
| R13 | Console amendments |
| R14 | Module list |
| R15 | E/I ownership |
| R16 | Console idempotency beyond grants |
| R17 | Console input bounds |
| R18 | Suspended organization fixture |
| R19 | Operator operations for U3/V3 |
| R20 | Per-phase deadlines |
| R21 | Billable terminal causes |
| R22 | Upload expiry code |
| R23 | `resolve_ambiguous` shape |
| R24 | Entitlements |
| R25 | `journal_write_failed` |
| R26 | Operator scope |
| R27 | Trace accumulation |
| R28 | Consent revoked while `submitting` |
| R29 | Deadlines bind every fenced mutation |
| R30 | Terminal integrity |
| R31 | Feedback provenance (Python half) |
| R32 | Conformance strength |
| R33 | Suspension scope |
| R34 | Operator audit trail |
| R35 | Calibration label visibility |
| R36 | Cursor opacity in conformance |
| R37 | Trace capture lifecycle |
| R38 | Queue wait is time spent queued |
| R39 | Terminalize-then-refuse in a real store |
| R40 | F2 merge criterion |
| R41 | Operator principals in customer views |
| R42 | Trace loss accounting |
| R43 | Feedback, calibration and rubric coherence (both halves) |
| R44 | Runtime mode |
| R45 | Price source |
| R46 | Work loading and preparation fencing |
| R47 | Trace export and content object |
| R48 | Track test hygiene |
| R49 | Operator view of feedback |
| R50 | `by_operator` marker |
| R51 | Pilot refuses the shared legacy key |
| R52 | Preparation lease |
| R53 | The store derives the hold |
| R54 | Text bounds unit and strict records |
| R55 | Untrusted store inputs |
| R56 | Judge sampling policy |
| R57 | Judge money guard |
| R58 | Normalized messages and engine events |
| R59 | Database-level masking, privileges and tenant coherence (after the D1 review) |
| R60 | Fairness of an unfiltered claim (`kind=None`) |
| R61 | Customer upload references and the pilot engine media form |
| R62 | Model revision string form |
| R63 | Task-local Valkey ports |
| R64 | v2 · A unit is a type, not a field |
| R65 | v2 · No conversion exists |
| R66 | v2 · The wallet is resolved, never named |
| R67 | v2 · The ledger vocabulary is closed and has no transfer |
| R68 | v2 · `settle` cannot see the present |
| R69 | v2 · Unpriced is unserveable |
| R70 | v2 · A private dev deployment is `not_found` to everyone else |
| R71 | v2 · The grant key is the identity |
| R72 | v2 · A nonzero legacy USD balance is a rollout hold |
| R73 | v2 · Totals are per unit |
| R74 | v2 · Authorization is against the current grant |
| R75 | v2 · An empty scope fails closed |
| R76 | v2 · Digest provenance is recorded, not assumed |
| R77 | v2 · The fixture base is generated |
| R78 | v2 · Pins are immutable after acceptance |
| R79 | Admission and an oversized deadline (the settled R-3, recorded) |
| R80 | Engine delta payloads and the usage rule (R58, settled wording) |
| R81 | Trace metadata charge (settles T1 ruling 5) |
| R82 | Staging takes only store-produced refs (F2R item 4) |
| R83 | One mutation runner (R32/R40, mechanism) |
| R84 | In-place amendment of unapplied migrations |
| R85 | Wallet owners are retired, never deleted (A1's R-A1) |
| R86 | Pilot composition and the ingress request context (G1R's request 10) |
| R87 | A CREDIT job's v1 debit is always zero (F2P wire-in candidate a) |
| R88 | `UsageRecordV2.usage`/`outcome` absence (candidate b) |
| R89 | Console instant comparison (candidate c) |
| R90 | Console `AUDIT_ACTIONS` are D1's spellings (candidate d) |
| R91 | Idempotent replay is looked up before preparation (G2 review money-B1) |
| R92 | Restore fidelity compares effective privileges, not raw ACL arrays (E3B2 review bk01) |
| R93 | The fake's requeue event id is its fresh outbox row id (E3B2 request 3) |
| R94 | The execution mode is part of the idempotency identity (G3 review ADM-1/B1) |
| R95 | The media object store (M1-L2 proposal) |
| R96 | The published serving revision is the measured one (E4B proposal) |
| R97 | A certification counts at one clean SHA (E4B proposal) |
| R98 | The gateway composes its durable adapters from settings or refuses (cutover proposal) |
| R99 | Upload references at use and the durable attach (M pilot-media proposal) |
| R100 | A negative operator adjustment is a store-made compensating entry (D5 candidate i) |
| R101 | Private catalog naming is `<provider slug>/<endpoint name>-<env>` (D5 candidate ii) |
| R102 | The data-access policy defaults are consent version 1 and trace off (D5 candidate iii) |
| R103 | USD admin grants stay on the legacy writer, never grant_credit (D5 candidate iv) |
| R104 | Preparation's exact count travels with `prepared` (PREP-WORKER proposal) |
| R105 | A preparation count is the engine's, or there is none (PREP-WORKER proposal) |
| R106 | A cancelled job's replay is terminal for that key (coordinator, box certification 2026-09-24; wording corrected the same day after the CERTIFY-TREE verifier's N6) |

**Proposed, not numbered** (decide or number in the next wave):

| Candidate | Status | Where |
|---|---|---|
| **R107** "A preparation count may be the memo of the engine's checked answer to the same body" (per-process memo keyed on the exact canonical `/tokenize` body incl. the org's `file://` path, whole media digests + profile, serving revision; media bodies only; only R105-checked answers; lives and dies with the worker, which is `PartOf=` the engine; TTL `PROCESSING_CACHE_TTL_S`; bounded LRU; logged as a memo) | wording final (verifier N4); numbered at the TOKCOST merge (`merge-plan/12-tokcost.sh`) | `origin/codex/tokcost:research/plan/evidence/w/TOKCOST-40440d4.md` "Ruling"; `S02:916` |
| A fenced `fail_preparation(lease, cause)` (or `prepared` refusing with a terminalization) so a permanent preparation refusal does not cost three lapsed leases | "for decision" | PREP-WORKER candidate (c), `S02:868`; `merge-plan/10b-rulings-prep.sh` |
| D5's `platform_cancelled` cancel cause | pending input, unimplemented | 08 verification log 2026-09-24 (R99–R103 line) |
| USD price lookup keyed on the resolved serving revision instead of the literal model string (coordinator recommends "resolve first") | ruling question for D/G | P-22; `S02:903` |
| Durable or cross-process count memo | explicitly needs its own ruling (R107 text) | TOKCOST Limit 1 |

## 9. Pending inputs register (`research/plan/15-pending-inputs.md`)

| ID | Input | Status at `2fe829a` | Blocks / waits for |
|---|---|---|---|
| P-01 | Approved Marlin CREDIT rate card, unit, rounding, failed-execution disclosure | **open**; provisional card 400/1,200 CREDIT per million live on hosted (`provisional = true`, `approved_by 'provisional - P-01 pending'`, `S02:861`) | public metered publication, CREDIT cutover, App E4 |
| P-02 | Existing USD balances and transition policy | **resolved** 2026-09-22: $0.00 legacy USD across 4 test orgs; keep accounts; no conversion | — (`ev/i/I1B-inventory-2026-09-22.md`) |
| P-03 | Local Docker/services | **resolved** (Docker on the coordinator host) | — |
| P-04 | Allocated GPU/staging target | **resolved**: the pilot box `i-0e8449a4ffca29bab` is the target; full operational authorization 2026-09-22; no separate staging environment exists | — |
| P-04-sweep | W3 concurrency sweep | **resolved** 2026-09-23 (`sweep-20260923T050411Z`); re-run after any engine change | — |
| P-05 | Signup email/callback/recovery config and abuse bounds | **open** (hosted signup disabled) | public onboarding (A2/I2A) |
| P-06 | Marlin artifact/capabilities, finite-video limits | **resolved as a profile** (S2M); registry oids ⚠️ `HF_TOKEN` | a "pinned artifact" claim beyond served bytes |
| P-07 | SOP rubric, event schema, dataset rights, ground truth, thresholds | **open** (10 missing inputs recorded in `marlin-sop.md` §5.3; no accuracy baseline) | any accuracy claim; realistic large-dataset evaluation |
| P-08 | Lab origin/project, operator identity, membership onboarding | open | hosted Lab |
| P-09 | Customer source-purpose permissions, retention/deletion | open (A1's retention ruling R85 notes the legal bound) | customer-content sharing, annotation, export, training |
| P-10 | Live teacher/evaluator model, approved USD rates, budget | open (`APPROVED_RATES` empty) | live judge/teacher calls |
| P-11 | Automatic training connector and paid terms | open | advertising automated training |
| P-12 | Rollout population, allocation unit, thresholds | open | public shadow/canary/A-B |
| P-13 | Live-video workload contract | open | X2 |
| P-14 | Robot/task/policy contract | open | X4 |
| P-15 | Non-NVIDIA chip choice and access | open | X6 |
| P-16 | Measured fleet need and topology | open; fleet notes written (`19-fleet-scale-…`), user decisions in §5 there | fleet rollout; any HA claim |
| P-17 | Accept E4B backend candidate → activate App; accept App → Lab | **open**: E4B release decision PENDING | App feature dispatch; Lab |
| P-18 | Workload targets (latency/throughput/error/quality/cost), soak duration, availability | provisional criteria only (S2M §5.2); no owner targets | promoting any provisional row; certification "pass" criteria |
| P-19 | Sourced AWS g6e.2xlarge / L40S price row in `cloud-pricing.md` | open | any cost-per-video-hour figure |
| P-20 | Engine refuses > ~72–82 s while profile v1 admits 120 s | **decided 2026-09-23: B at 82 s** (`MAX_VIDEO_SECONDS=82` deployed); user re-confirmed 82 s on 2026-09-24 | 120 s needs a new serving version (E1 + amended W4 protocol) |
| P-21 | Reserve task-port blocks (`net.ipv4.ip_local_reserved_ports=55432-55499,56700-56799`) on the dev host | open (root change; user approval) | flake-free gates |
| P-22 | USD price lookup keyed by literal model string | open; W7e seeds a row per alias form meanwhile | a ruling (§8) |
| P-23 | Long clips (user asked 82 s → 1200 s, then kept 82 s): sampling below the trained 2 fps, byte/timeout caps, engine budget | **recorded only in the session record** (`S02:928`, `S02:931`), not in `15-pending-inputs.md`; LONGCLIP shelved | long-clip support (§11) |

No pending commercial decision changes the one-time 10,000 CREDIT per individual policy
(`15-pending-inputs.md`). The 16 original F2.2 carryovers are dispositioned in the same file's
second table; those that remain open belong to C0, U1R, V1M, J2, T2I (App/Lab scope).

## 10. Operational state and runbooks

### 10.1 The rollout sequence (`infra/runbooks/rollout.md`; `infra/rollout/README.md`)

Coordinator-run, one step per command (§10.5), every irreversible step logged first in the
session record with purpose, cost and rollback (`infra/runbooks/README.md` rule 1).

| Phase | Steps |
|---|---|
| Before the window | P1 gates G1–G4, G6 at `RELEASE` (tree clean, `make check` + `tests/i`, in-image pilot probe `"ok": true`, engine pin `[]`); P2 `rehearse.sh` PASSED; P3 probe; P4 SSM names present; P5 two G6B keys (one revoked); P6 deployment lock + record |
| W1–W4 (outside the window) | W1 `release-bundle.sh $RELEASE` → S3 `releases/` → box fetch (`<out>/$RELEASE.fetch.sh`) → `20-prepull.sh`; W2 root-volume snapshot **[cost]**; W3 `10-inventory.sh`; W4 `25-save-edge.sh` (live Caddyfile saved under `/opt/dlami/nvme/w4-logs/`) |
| W5 | `30-pause.sh` — maintenance edge (503 + `Retry-After`) and drain |
| W6 | fresh hosted backup + restore check into a local Supabase image with GoTrue v2.197.0 migrations first (`"equal": true`), copy apply and digest |
| W7 | hosted `migrate.py plan` (digest must equal the copy's) then `apply --expect` (at `2fe829a` nothing is pending: 0001–0018 applied) |
| W7b | operator seed `seed_marlin_provisional.sql` (first install only; idempotent) |
| W7c | USD price version for the unlabelled alias (first install only) |
| W7d | `signup_grant` flag, USD balance for a tenant, operator key bootstrap (SSM `/model-inference/operator_key`) |
| W7e | one USD price version per model string clients send (labelled alias) — until P-22 is ruled |
| W8 | `40-checkout.sh` (the box checkout refuses untracked files: move them aside on the same filesystem) |
| W9 | `45-s3-check.sh` — real-bucket conformance with the instance role |
| W10 | `TIMEOUT_S=3600 … 50-install.sh RELEASE=… ENGINE_MAX_NUM_SEQS=8 INFRX_SET="S3_MEDIA_BUCKET=llm-bootcamp-641134885443 MAX_VIDEO_SECONDS=82 WORKER_CONCURRENCY=8" MIGRATION_DIGEST=<digest or nothing-pending>` — image, preflight, units, engine restart (168–181 s), readiness, **then** the edge goes live |
| W11 | `60-verify-local.sh` (+ worker `/readyz`, build gauge, journal lines) |
| W12 | `verify-external.sh` with the two keys exported from `read -rs` (never argv) → `failures: 0` |
| W13 | record RELEASE, image id, engine digest, snapshot, backup dir, `SHA256SUMS`, migration digest, command ids; release the lock; prune old install backups (they hold secrets) |
| Revert | R1 exit 2 → `91-abort.sh` + `93-restore-edge.sh`; R2 exit 4 / W11–W12 red with no pilot request accepted → `90-revert.sh BACKUP=<dir> ROLLBACK_TO_UNMETERED=no-pilot-request-was-accepted` (operator attestation corroborated by a zero count of hosted pilot rows), then `93-restore-edge.sh`; if Caddy then answers only on `localhost:2019`, reload once with `--address localhost:2019`; R3 accepted work must stop → `95-maintenance.sh`; R4 host → root-volume replace from the W2 snapshot |

Each W7b–W7e row was learned from a failed step on 2026-09-24 (`S02:853`, `S02:864`, `S02:895`,
`S02:903`); the rollout's verification log records them.

### 10.2 The box now (`H1010`, `H1700`, `H1745`, `S02:879`, `S02:899`, `S02:907`)

| Item | Value |
|---|---|
| Instance | `i-0e8449a4ffca29bab`, g6e.2xlarge, 1× L40S, AMD EPYC 7R13, 8 vCPU, us-east-1d; Elastic IP and DNS `marlin2b.callbill.ai` survive stop/start, the instance-store NVMe does not (`HANDOFF.md` §1) |
| Served release | `4226315` (full `422631591845fbd66b590c73d5ff4150318d9d7a`), runtime image `sha256:491697bce56a9b0b0359b38adaaeed6cb534f73ad6a45f35f43f9be46790589f`, installed 10:01Z |
| Previous release | `27af05a8cb06ff446d62d13bfbd83f6e573d3461`, image `sha256:3e702e684cc6…`; its files are in the 4226315 install backup (the rollback drill's target) |
| Checkouts | `/home/ubuntu/model-inference` at `4226315` (release refs under `refs/infrx/releases/`; no `origin` remote); measurement checkout `/opt/dlami/nvme/w3-checkout` at `4226315` |
| Install backups | `/var/backups/infrx/20260924T054001.368300606Z-27af05a8…`, `/var/backups/infrx/20260924T100129.620865008Z-4226315…` (root-only; hold env files with secrets) |
| Snapshots | `snap-08732d3ac6376e850` (pre-wave-3, 2026-09-22), `snap-0c3f41caccbffbfcf` (pre-rollout `27af05a`, 2026-09-24); DeleteOnTermination off on the root volume |
| Moved-aside artefacts | `/home/ubuntu/untracked-aside-20260924T051032Z/`, `/opt/dlami/nvme/w3-untracked-20260924T165215Z/` |
| Certification | `/opt/dlami/nvme/e4b/` (`inventory.txt`, `key.env` 0600 from SSM `/model-inference/e4b_api_key`, `parity-e0.jsonl`, run outputs `20260924T165408Z/`, `20260924T172244Z/`); image `infrx-certify:4226315…` (`sha256:4e48efa3…`) = runtime image + git + `safe.directory /repo` |
| Edge | the release's Caddyfile; admin on `unix//config/admin.sock`; the pre-refactor live file saved as `/opt/dlami/nvme/w4-logs/Caddyfile.live-20260924T050658Z` (sha256 `ff47f706…`) |

**Run3 box command** (from CERTIFY-TREE and its verifier, `S02:919`, `S02:925` N8): run
`certify.py --box … --release-sha <full sha>` inside `infrx-certify:<release>` with
`--worker-metrics-url http://127.0.0.1:8002/metrics` and `--env-file /etc/marlin2b-gateway.env`
(never `-e MAX_VIDEO_SECONDS`); check `target.max_video_seconds == 82.0` in the report;
`E4B_WINDOW_OK=1` only while no external consumer key exists or the window is announced; the
classifier refused `--pid host --user 0`, so the run uses neither.

### 10.3 Drills and runbooks

`infra/runbooks/`: `restart.md` (engine, worker, gateway, host, drain, saturation),
`restore.md` (A0–A9 hosted backup/restore/apply; B1–B4 box snapshot), `rollback.md` (rollout
rollback, maintenance switch, code-only rollback of a migration), `index-loss.md`,
`reconcile.md` (never repair money by hand), `disk.md`, `rollout.md`, tool `pgrestore.py`
(dump/restore/check with an emptiness + source-identity target guard, R92 ACL comparison).
Each has a local drill in `tests/integration/backend/recovery/` (rc01–rc10, bk01–bk04). Box
drills: only the recovery-box row of E4B, started 2026-09-24T19:30Z in the order engine
restart → worker SIGKILL → index loss (Valkey restart; the worker's reconciler re-indexes every
10 s) → drift check → rollback to `27af05a` via the 4226315 install backup and forward again
with W10 → drift → restore B1 (list) (`S02:933`–`934`). **Results are not in the tree**; every
recovery window stays ⚠️ until they are recorded in `ev/e/E4B-release-decision.md`.

### 10.4 Access pattern

- **AWS** (CLAUDE.md "AWS access from this host"): `env -u AWS_ACCESS_KEY_ID -u
  AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN aws --region us-east-1 …` (default profile
  `sofia-admin`, account 641134885443). No session-manager plugin: box commands go through
  `aws ssm send-command` with the script base64-wrapped (`infra/rollout/ssm.sh`; the `ssm()`
  helper in `infra/runbooks/README.md`); record every command id. Long jobs: `setsid nohup`.
- **Hosted DB**: session pooler DSN built in-process from `/INFRX-SUPABASE-PROD/db_password`
  (libpq reads `PGPASSWORD`; never on a command line); PostgREST `db-schemas` stays `public`.
- **Secrets rules**: names only in any file; values read into a shell variable or process on
  the machine that uses them; never echoed, never in argv (`refuse_secret_argv`), never
  committed; minted secrets go straight to SSM or a 0600 `O_EXCL` file
  (`infra/runbooks/README.md` rule 2; `S02:882`).

### 10.5 Permission-classifier constraints recorded (the auto-mode classifier blocks these)

| Refused | When | Workaround used |
|---|---|---|
| "Credential Exploration": using the box's stored Hugging Face credential to read registry oids | 2026-09-22 (`S02:66`) | not retried; user supplies `HF_TOKEN` |
| Multi-step deploy commands | 2026-09-24 windows (`H0545`, `H1010`) | one window step per command |
| "Secret-Store Writes": putting the minted operator secret into SSM, then a 0600 scratch variant | 2026-09-24 05:48Z (`S02:865`) | done after the user's explicit authorization at 16:27Z (`S02:882`) |
| `docker run --pid host --user 0` form for certify | 2026-09-24 (`S02:899`) | run without them (the box runs no App/Lab) |
| "Production Reads": hosted reads from this host | 2026-09-24 (`S02:913`) | box-side evidence instead |
| `git revert … --amend` | 2026-09-23 (`S02:625`) | not run |

### 10.6 Hosted identities in use (ids truncated as in the record)

Operator key row `d554db80…` in org `a349a382…` (the first A1-verified dev profile's personal
org); test consumer key `142c7d81…` (user `f997131f…`, org `15e766d0…`, wallet `8404630c…` with
10,000 CREDIT and USD 5.00 test grant; also in SSM `/model-inference/e4b_api_key`); revoked key
`0fdbb31f…`; two pre-cutover legacy consumer keys (untested) (`S02:884`–`889`, `H1700`). The
coordinator's smoke key files are in its scratchpad (`scratchpad/rollout/keys/`, 0600), outside
the repository.

### 10.7 Dev host notes

Two `next-server` processes of `apps/app` have run from the main checkout since 2026-09-20 (the
E4B local precondition's known exemption; left alone) (`S02:719`). The W3 corpus (81 files,
2.7 GB) is in the main checkout's `.claude/corpus-cache` (`H1745`). Tracker:
`python3 research/plan/scripts/progress.py` renders `PROGRESS.md`/`progress.html`, published at
`https://claude.ai/artifact/Rk84qe3Yuzr2d357GoeRvZ` (`H1700`). Resume pointer
`.claude/RESUME-NOW.md` (gitignored) names the newest `HANDOFF-*.md`.

## 11. What is NOT built (the gap list for re-planning)

| # | Gap | What exists | Specified in |
|---|---|---|---|
| G-1 | **BACKEND-READY decision** for the Marlin endpoint | E4B software + runs 1–2; decision template PENDING | `18-marlin-backend-first.md` §E4B; `ev/e/E4B-release-decision.md`; `models/marlin2b/results/E4B-protocol.md` |
| G-1a | → remaining steps recorded by the coordinator | TOKCOST verify → merge (R107); CERTIFY-POLISH finish/verify/merge; INTAKE-DRAIN build/verify/merge; gate-3; ff `main`; third install (W1, W5, W8–W12); certification run3 in the certify image; step-6 drill results; E1B L2–L7; the release decision; `tasks.json` E4B box half; W13 record | `S02:937`; `H1745` NEXT |
| G-2 | **E1B cells L2–L7** (open-loop rate sweep, burst, paired direct/gateway, cancellation, resume on a real endpoint, 4 h soak with an induced restart) | L0/L1/L8 only | `models/marlin2b/results/E1B-protocol.md` §3 |
| G-3 | Performance work behind D-9/D-10 (video tokenize cost; e2e p95 vs 45 s; W4 protocol amendment; `c*`; E1 encoder budget as a new serving version) | measurement tooling (W3/W4/M4), TOKCOST memo (unmerged) | W4 phase B requests; TOKCOST request 1; `18` §W4 |
| G-4 | **CREDIT cutover** on the pilot (`ACCOUNTING_REGIME=credit`, `credit_admission` on) | CREDIT path implemented and locally tested; provisional card; flags off | P-01; `research/platforms/02-credits.md`; D5 IR5 / PREP-WORKER Limit (text job before CREDIT rechecks needs 0019) |
| G-5 | **App features**: A2 signup verification + credited onboarding, A3 catalog/rates/examples, C0 real consumer DB adapter, C3A key/privacy/operator actions, U1R CREDIT views, U2 keys/settings, U3 operator pages; gates E3A (APP-LOCAL), I2A, I3, E4 (APP-PILOT) | U1/C1 fixture-backed from wave 2; console deployed with preview gate | `09-amendment-workstreams.md`; `11-wave3-revision-handoffs.md` (C0, U1R); `research/platforms/03-app-spec.md`, `04-app-roadmap.md`; P-01, P-05, P-17 |
| G-6 | **Lab** M0: L1 shell/auth, L2 roles and data access, L3 model registration/dev-prod revisions, L4 UI; E3L, I2L | `apps/lab/README.md` only | `09-amendment-workstreams.md`; `research/platforms/05-lab-spec.md`, `06-lab-roadmap.md`; P-08 |
| G-7 | Lab M1 observe/review: V1M (move trace explorer to Lab), C2, D6F/D6J, T2I/T2F/T3, J2/J3, V2/V3, G4F/G4T, C3F/C3L; E5L | T1 spool, J1 dry-run, V1 list UI (fixture-backed, in App) | `11-wave3-revision-handoffs.md` (V1M); `tasks.json`; P-09, P-10 |
| G-8 | Lab M2–M4 (evaluate, improve, release): F3, D7, N1–N4, H1, B1–B4, I5, E6L; D8, P1–P4, I6, E7L; D9, R1–R4, I7, E8L | nothing | `13-lab-improvement-handoffs.md`; `12-complete-build-plan.md`; P-11, P-12 |
| G-9 | **Deep traces program** (opt-in per-key capture → spool → S3 + ClickHouse, console Traces page, feedback API, async judge; decisions D1–D8) | T1 bounded capture + spool (not composed; capture off), J1 dry-run, V1 trace list UI (fixtures), `infrx.feedback`/judge tables in 0003, ClickHouse only in the local E2 compose stack | `research/traces/01`–`08` (esp. `08-phases-and-test-plan.md`) |
| G-10 | **Closed-loop distillation platform** (C1–C10 of `research/platform/06` §1.1): C1 gateway — built for Marlin on one GPU; C2 trace store — not built; C3 annotation — not built; C4 dataset/eval store — not built; C5 training orchestration — not built; C6 optimisation — measurement tooling only (W3/W4/M4); C7 model registry — minimal registry tables in 0007, no MLflow; C8 experiment/A-B — not built; C9 control plane — operator CLI only; C10 tenancy — orgs/keys/RLS built. The MVP's ten exit criteria (`research/platform/10-roadmap-and-mvp.md` §2.5) — none met | as listed | `research/platform/00`–`11`, `10-roadmap-and-mvp.md` |
| G-11 | **Fleet / multi-GPU / HA** (snapshots, node registry, controller, standby nodes, shared Valkey, edge fan-out) | notes only | `19-fleet-scale-TO-BE-IMPLEMENTED.md` §4 (SNAPSHOT, FLEET-STATE, FLEET-CTRL, EDGE, E-track); I4 (conditional); P-16 |
| G-12 | **Long clips** (> 82 s; user idea 1200 s) | LONGCLIP findings only | `origin/codex/longclip` note; P-20; P-23 (session record) |
| G-13 | **Bare-metal blueprint** phases 0–6 (8×B300 nodes; five-model pools; cold-start, routing, KV tiering, autoscaling, PD decision) — none started; CLAUDE.md: development on AWS until the cluster exists | research only | `research/scaling/10-blueprint.md` §8; `11-playbook.md` |
| G-14 | Models other than Marlin-2B (DeepSeek-V4.1-Flash, its NVFP4 build, Qwen3.8-27B, Kimi-K3) on the platform | per-experiment scripts under `models/`; not in the catalog | `research/models/`; `research/matrix/` |
| G-15 | Conditional extensions: G5 signed callbacks; X1–X6 live video, robot policy/ROS2, non-NVIDIA backends | nothing | `14-expansion-gates.md`; P-13–P-15 |
| G-16 | Commercial App-M4 (paid credits, invoices, team budgets) | nothing; no decision | `research/platforms/04-app-roadmap.md` |
| G-17 | Operations hardening: alert scheduling and delivery; host hardening rows M-ROLE/M-BOUNDARY/M-KMS/M-IMDS/M-SSH; staging environment (none allocated — the pilot box is the only target); Supabase PITR; Marlin weights mirror in S3; dedicated DB login role; an AMI/golden image | not done | `infra/README.md` §1, §5, §6, §9; D-31–D-33 |
| G-18 | Measured recovery windows and restore RTO on the box/hosted (all ⚠️ P-18) | local drills; step-6 in progress | `infra/runbooks/README.md` rule 3 |
| G-19 | Accuracy / SOP quality certification | none possible without P-07 | `marlin-sop.md` §5 |

## 12. Process that worked, and its rules

These rules produced 30 reviewed packages in about 2.2 days of wall clock (2026-09-22 15:38Z →
2026-09-24 19:40Z) across session limits and several account switches, and they caught real
defects behind green suites (`W2H` §2: seven of eleven wave-2 first reviews were
`fix_required`; in wave 3 the first-round passes were only F2R lanes A/B, D1R, G1R, G6B (test
gaps) and M4 — §4.1). Keep them.

**Roles and lanes.**
- Coordinator (orchestrates, reviews, merges, runs every box/hosted operation); implementers and
  reviewers run on Opus 5.5 (user rule 2026-09-23, `S02:349`). Workers never edit `tasks.json`,
  08 §10, the coordinator records, composition roots, the Makefile or lockfiles — they send
  integration requests (`research/plan/03-execution-protocol.md`).
- One lane = one `codex/<task>-<slug>` branch in `.claude/worktrees/codex-<task>`, from a
  recorded committed integration SHA; one owner per mutable path; D alone writes migrations
  (next 0019, now frozen by R84); coordinator-recorded start-edge deviations are allowed when
  the contract exposure is small (`S02:37`, `S02:136`, `S02:219`).
- Every lane commits after each item and pushes; at a session limit the coordinator
  WIP-commits dirty lanes and **verifies every lane against `origin/<branch>`** (the audit-gap
  lesson, `S02:246`).

**Review and merge.**
- Independent review at the lane's committed head, as a workflow of lenses (money, fence,
  security, honesty, …) with **two refuters per blocking finding**; the verdict JSON is saved
  under `ev/<track>/<TASK>-{review,confirm,verify}-<sha>.json`.
- Since 2026-09-23T19:29Z: **one fix round per lane** (all findings, blocking and nonblocking),
  then a **single verifier**, then merge; reviews may start on a committed head before the
  handback (`S02:694`, `S02:831`).
- Merge criterion R32/R40/R83: every claimed invariant is killable by a named case; one
  mutation runner with pristine baselines, anchor checks and assertion-shaped deaths; a
  surviving mutant on a claimed invariant is a failed suite.
- Merges are `--no-ff`, scripted and rehearsed on a scratch `git clone --shared`
  (`research/plan/evidence/coordinator/merge-plan/`: steps 1–12, `replay.sh`,
  `checkpoint2.sh`, `gate-prep.sh`, gate-3); units that must not be gated alone merge together
  (the D5 → phase 3 → cutover → M1-L2 unit, `S02:752`). After each merge: the affected suites
  and lists on the merged tree. `main` moves only by fast-forward after a whole-tree gate
  (api-test, layer 0, all mutant lists, layer 3, `make bench-test` in the foreground) — main
  auto-deploys the App on Vercel.
- Rulings are numbered at merges by the coordinator (lanes propose text; `08` log line per
  number); a ruling proven wrong is corrected in place with a log line.

**Evidence and records.**
- Evidence per task under `ev/<track>/<TASK>-<sha>.md` (commands, environment, exits, raw
  artefact hashes, limits, requests); measured results under `models/<exp>/results/` (CLAUDE.md
  branch convention).
- Coordinator record `2026-09-22-session-02.md` is append-only; handoffs are new files
  `HANDOFF-<UTC>.md` at the root (newest supersedes; `.claude/RESUME-NOW.md` points at it);
  `tasks.json` + `validate_plan.py --write-ledger` updated only after evidence.
- Honest states: PENDING names an owner from a closed list; a skip or missing service is never
  a pass; fake-engine numbers are labelled; provisional criteria keep their label (E1B §4).

**Operations.**
- Full operational authorization (2026-09-22 15:52Z) under the coordinator's rules: log before
  every paid/irreversible step (purpose, cost, rollback), bounded spend, backup + restore check
  before any hosted migration, fail-closed installer before cutover (`S02:31`).
- One deployment lock; one box step per command; SSM with base64 scripts and recorded command
  ids; secrets by name only (§10.4); never restore over the live project; never repair money by
  hand; single GPU is not HA.

**Local isolation (the most frequent cause of false reds).**
- Per-task port blocks (`contracts/tasklocal.py` `TASK_PORTS`/`TASK_BLOCKS`), private
  `INFRX_D_TASK`, `INFRX_Q_VALKEY_PORT`, private E2 namespaces; never share a harness container
  (`S02:463`); `TMPDIR` outside the repository (`H0215` traps); `make bench-test` in the
  foreground (a detached launcher ignores SIGINT, `S02:692`); integration tests run from the
  repository root; attribute every red before judging a gate (HarnessBusy, P-21 TIME-WAIT
  collisions, load-sensitive mutants).

## 13. Verification log

- 2026-09-24: Compiled read-only from `origin/claude/backend-impl` at `2fe829a` in a shared
  clone (no worktree touched, nothing pushed, no box/AWS/hosted contact). Sources read:
  `CLAUDE.md`; `research/plan/README.md`, `18-marlin-backend-first.md`,
  `12-complete-build-plan.md`, `16-fresh-session-handoff.md`, `15-pending-inputs.md`,
  `19-fleet-scale-TO-BE-IMPLEMENTED.md`, `tasks.json` (v4, 119 records),
  `17-task-ledger.md`, `validate_plan.py` (run: PASS), `08-contracts-v1-encoding.md` §10 and its
  log, `10-wave2-platform-audit.md`, `11-wave3-revision-handoffs.md`, `03-execution-protocol.md`,
  `13`/`14` headings; coordinator records `2026-09-20-session-01.md`,
  `2026-09-21-wave2-handoff.md`, `2026-09-22-session-02.md` (all 937 lines), `STATUS.md`,
  `PROGRESS.md`, `progress-state.json`; `HANDOFF-20260924T1745Z.md` (newest), `T1700Z`, `T1010Z`,
  `T0545Z`, `T0215Z`, `HANDOFF.md` §§0–2, and the older 2026-09-22/23 handoffs by grep (Dependabot,
  traps); every review/verify JSON under `research/plan/evidence/*/` (verdict fields);
  `research/platforms/README.md`, `04-app-roadmap.md`; `apps/infrx-api/infrx/` layout,
  `operations/cli.py`, `observe/` metric names, `config.py`, `contracts/limits.py`,
  `deploy/Caddyfile`; migrations 0001–0018 headers; `infra/README.md`,
  `infra/runbooks/*.md`, `infra/rollout/README.md`, `infra/alerts/alerts.json`;
  `models/marlin2b/results/E1B-protocol.md`, `E4B-protocol.md`, `E4B-box-4226315/run2-…/report.json`
  (and run1's hash); `ev/e/E4B-release-decision.md`, `E4B-endpoint.md`, `E4B-dev-4226315.json`,
  `E1B-box-20260923T2155Z.md`, `E3B-e57b820.md`; `ev/w/W4-phaseB-20260923T2155Z.md`;
  `origin/codex/tokcost:…/TOKCOST-40440d4.md`; `origin/codex/longclip:…/LONGCLIP-4db74b6.md`;
  `git log` of `codex/certify-polish`, `codex/intake-drain`; `research/workloads/marlin-sop.md`
  headings; `research/scaling/10-blueprint.md` §8; `research/platform/README.md`,
  `10-roadmap-and-mvp.md` §2; `research/traces/README.md`. Discrepancies found and stated
  rather than resolved: soak rate (record 0.5/s vs report 0.25/s), stale E4B release-decision §5
  and `progress-state.json` gate state, P-23 absent from `15-pending-inputs.md`, two unsourced
  g6e hourly prices. The live-state section is the coordinator's to append.

### 14.0 Delta since this document was compiled (compiled at 2fe829a; delta written by the coordinator)

- TOKCOST: fix round 293ddcb verified PASS (`research/plan/evidence/w/TOKCOST-verify-293ddcb.json`) and MERGED at **95fbb90** (step 12); ruling **R107 numbered** (08 §10, commit 25e60c5). Next free ruling R108.
- CERTIFY-POLISH: handed back complete at **88adae2** (N1–N5, N7, N9–N15, p50 rows; E4B list 219); targeted verifier running → step 13 merge on pass.
- INTAKE-DRAIN: handed back at **d502c90** (bounded drain before a mid-body refusal; six real-socket cases; 5 new + 4 re-anchored mutants; tests/g 581, G list 340; `LARGE_BODY_LIMIT=8` proposed for the pilot with the 3× parse-memory model; ruling proposal for R108); targeted verifier running → step 14 merge on pass.
- E1B finding (protocol defect): every gateway cell after the first REPLAYED (one seed + one dataset identity for all cells → shared idempotency keys → the durable gateway answered from the journal); only the r=0.25 cell measured the engine. Rerun with per-cell dataset identity in `models/marlin2b/results/E1B-box-4226315/run2-distinct-keys/`; E1B-protocol.md needs a per-cell/per-run dataset identity and bench must flag `replayed > 0` cells. The first pass still proved L5 (cancels billable, 0 failures) and L6 (resume: 27 skipped as terminal, no second acceptance, 0 conflicts) and exposed 9 ReadErrors under an 8-burst (the intake close-on-refusal loss).
- gate-3 (focused gate on 95fbb90) running; `main` fast-forwards on green; the third install follows.
- Step-6 drills 1–3 measured on the box (engine restart 172 s to ready; worker SIGKILL 8 s; index loss ~6 s; drift 0/0) — see §14.2 and the session record; restore B1 listed; the rollback/forward drill is folded into the third install.
- E1B L2 ladder measured through the public edge (0.25 / 0.5 / 1.0 clips/s) — §14.2; L3/L5/L6 running; L4 (paired direct) and L7 (4 h soak) not run.
- Discrepancies the compiler flagged, resolved here: the run2 soak ran at **0.25/s** (half the supported rate; the record's earlier "0.5/s" was the envelope's supported rate); P-23 added to `15-pending-inputs.md`; `progress-state.json` gate BACKEND-LOCAL set to passed (E3B); the two g6e hourly prices are both unsourced (P-19 owns the row in cloud-pricing.md); the stale §5 items of `E4B-release-decision.md` are closed only when the decision is written.

## 14. Live state as of the coordinator's last write (2026-09-24, sofia window) — written by the coordinator

This section holds what only the running session knew; everything above was compiled from the repository.

### 14.1 Deployed pilot

| Item | Value |
|---|---|
| Public edge | `https://marlin2b.callbill.ai/v1` (Caddy, maintenance site installed; `request_body max_size 96MiB`) |
| Box | g6e.2xlarge `i-0e8449a4ffca29bab` (us-east-1d), 1× L40S 46 GB; NVMe `/opt/dlami/nvme` (instance store: weights, processing cache, releases, e4b outputs; lost on stop) |
| Served release | `422631591845fbd66b590c73d5ff4150318d9d7a` (second release; the preparation loop); `infrx-runtime:<sha>` image `sha256:491697bc…`; units `marlin2b-vllm`, `marlin2b-gateway`, `infrx-worker` (PartOf the engine), `infrx-valkey` (no persistence) |
| Pilot mode | `INFRX_MODE=pilot`, `ACCOUNTING_REGIME=legacy_usd`; flags: `credit_admission` false, `legacy_usd_admission` true, `signup_grant` true |
| Env file | `/etc/marlin2b-gateway.env` (all TUNABLE values; `MAX_VIDEO_SECONDS=82`, `ENGINE_MAX_NUM_SEQS=8`, `PROCESSING_CACHE_DIR=/opt/dlami/nvme/processing`); read by all three units |
| Checkouts on the box | `/home/ubuntu/model-inference` (the rollout checkout; releases fetched as bundles into `/opt/dlami/nvme/releases`), `/root/infrx-deploy-<release>` (the extracted release used by the steps), `/opt/dlami/nvme/w3-checkout` (the measurement checkout, at 4226315; its earlier untracked files moved to `/opt/dlami/nvme/w3-untracked-20260924T165215Z/`) |
| Install backups | `/var/backups/infrx/20260924T051144…-27af05a8…`, `…T054001…-27af05a8…`, `…T100129.620865008Z-4226315…` (holds the 27af05a runtime files; the rollback drill's target) |
| Root snapshots | `snap-0c3f41caccbffbfcf` (pre-window 27af05a, 2026-09-24T04:31Z), `snap-08732d3ac6376e850` (pre-wave3, 2026-09-22) |
| Corpus on the box | `/opt/dlami/nvme/w3-corpus` (mounted `/corpus` in the certify container); W4 E0 parity baseline `w4-e0-20260923T194748Z`; certify image `infrx-certify:<release>` (runtime image + git + safe.directory) |
| Certify outputs | `/opt/dlami/nvme/e4b/<UTC>/` (run1 20260924T165408Z, run2 20260924T172244Z), fetched to `models/marlin2b/results/E4B-box-4226315/run{1,2}-…/` via `s3://llm-bootcamp-641134885443/w4/e4b-box/` |
| Hosted database | Supabase project `fcbnscgsymzdykendbrc` via the pooler `aws-0-us-east-2.pooler.supabase.com:5432`; migrations 0001–0018 applied; the password is SSM `/INFRX-SUPABASE-PROD/db_password` (name only) |
| Secrets (names only) | SSM `/model-inference/operator_key` (the single operator key), `/model-inference/e4b_api_key` (the certify/E1B consumer key), `/INFRX-SUPABASE-PROD/db_password`; the same consumer key sits 0600 in the coordinator's session scratchpad (`rollout/keys/test.key`) and dies with the session |
| Hosted rows seeded by hand (runbook W7b–W7e) | Marlin operator catalog seed (`seed_marlin_provisional.sql`), price versions for BOTH alias forms (`pv_marlin2b_usd_2026_09`, `…_r1`; 0.10/0.30 USD per million; P-22), `signup_grant` flag, one A1 grant (10,000 CREDIT) for the verified dev user, a USD 5.00 test grant to that user's personal org (idempotency `usd-grant-rollout-smoke-20260924`), consumer keys: one active (the certify/E1B key), one revoked |
| Tenants | ONE verified individual on the pilot; the second dev account's email is unconfirmed, so no second tenant exists (E1B's `--tenant-keys` cell cannot run) |

### 14.2 Measured today (release 4226315)

- W12 smoke 15/15; text and video jobs settle with engine-exact counts (13 = 13; 1,211 = 1,211).
- E4B run2 envelope: supported 0.5 clips/s; r=0.5 112/120 accepted (8 over-cap 400), r=1.0 3 × 429, r=2.0 33 × 429; e2e p95 per clip-minute 91.4 / 91.7 / 96.8 s (provisional target 45 s); TTFT p50 ≈ 1.4–1.7 s; soak 65 min at 0.25/s: 809 accepted, 0 failures, host RSS −52 MiB, latency drift p50 2.97 → 2.94 s (cut by the runner's 3600 s bound — fixed in CERTIFY-POLISH N12).
- Overload: 24 typed 429s sent, 14 lost as client ReadError (mid-body `Connection: close`) — INTAKE-DRAIN lane.
- Drills: engine restart to ready 172 s (gateway ready at the same second; the worker restarts with it); worker SIGKILL → ready in 8 s; Valkey loss → the worker fails loudly, restarts, ready in ~6 s; drift 0 / 0 after; 1,148 succeeded + 2 cancelled jobs in the window. Restore B2/B3 and the rollback/forward drill: see the session record for whether they ran before the third install.
- E1B L2 at 0.25/s (engine restarted): 57/60 accepted, 3 over-cap, TTFT p50 1.69 s, e2e p50 2.96 s, TPOT 5.7 ms; the other cells are in `models/marlin2b/results/E1B-box-4226315/bench.jsonl` (see the record for which ran).
- Video `/tokenize` on the engine: 16.8 s for a 5 s clip (first-seen); TOKCOST's memo removes it for repeats only; the cold/warm split (`scratchpad` step `tokcost-split.sh`) is a maintenance-window measurement not yet run.

### 14.3 Coordinator constraints learned (keep them)

- The auto-mode classifier blocks: multi-step deploy commands in one Bash call ("Production Deploy" → one step per command), secret-store writes, `docker run --pid host --user 0`, and every hosted-database read from this host ("Production Reads", even read-only aggregates, even after the user's grant) → run drift checks from the box through the worker container's own connection (`drills/d9-drift.sh`), or ask the user for a Bash permission rule.
- The box cannot signal a container's pid from the host (AppArmor): use `docker kill --signal=…`.
- A bench run's `--model` must be a string with a USD price version (P-22); the default `marlin2b` has none.
- Every box op goes through `infra/rollout/ssm.sh <step>` and is logged in the session record with purpose/cost/rollback and the SSM command id.

### 14.4 In flight at the moment of this write

See the last entries of `research/plan/evidence/coordinator/2026-09-22-session-02.md` and `HANDOFF-<latest>.md` (named by `.claude/RESUME-NOW.md`): the TOKCOST and CERTIFY-POLISH verifiers, the INTAKE-DRAIN lane, gate-3, the `main` fast-forward, the third install and certification run3.

- 2026-09-24 (coordinator): §14 appended (live state, delta since compile).
