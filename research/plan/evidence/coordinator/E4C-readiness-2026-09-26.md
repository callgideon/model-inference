# E4C dispatch-readiness brief (coordinator, 2026-09-26 ≈04:40Z, tip f76ddc58)

Read-only synthesis by an Opus agent for the coordinator after BACKEND-LOCAL was accepted (E3C final run on 04ae5e21, evidence `evidence/e3c/E3C-FINAL-27a6961.md`). Paths are repository-relative. Abbreviations: PS = `research/plan/evidence/coordinator/progress-state.json`, PI = `research/plan/15-pending-inputs.md`, OPS = `research/plan/consumer-v1/03-operations-and-verification.md`, RB = `models/marlin2b/results/E4C-runbook.md`, RO = `infra/runbooks/rollout.md`, RR = `infra/rollout/README.md`, RS = `infra/runbooks/restore.md`, S03 = `research/plan/evidence/coordinator/2026-09-24-session-03.md`.

## 1. What acceptance requires

- **Cells.** The six BACKEND-READY cells are all NOT RUN: BACKEND-JOURNEY, LOAD-CLOSEDLOOP, PERF-ENVELOPE, OPS-CONTINUOUS, CREDIT-CUTOVER, MARLIN-SOP (PS gates; `tasks.json` E4C test_ids).
- **The ten P-17 checks** must all hold (OPS:96-111): CREDIT regime with the P-01 card; P-18 committed before the run starts; the two-tenant journey; exact reconciliation at ≤ 50,000 CREDIT per cell; alert, expiry, restore and known-good rollback proven; RV-04, RV-08, RV-09, RV-10 fixed (all four still open in PS findings).
- **Command and where it runs.** One `certify.py --no-stack --box … --run-profile --key-inventory --overload-profile` on the box inside `infrx-certify:$RELEASE`, launched by `e4b-certify3.sh` (RB:120-152). `make backend-certify` with `--box` only preflights and validates; it never starts the live run (`tests/integration/gates.py:338-352`). The two-tenant journey runs from the coordinator host through the public edge (RB:167-265).

## 2. Ordered steps from the tip

- **a. Hosted state.** Hosted is at 0018 (`research/plan/20-platform-handoff-2026-09-24.md:73`); no later hosted apply is recorded (hosted operations held since S03:307). Missing on hosted: 0019–0023 (on the tip) plus 0024/0025 (D10-MERGE-2 lane). The freeze needs 0025 or newer (RB:75).
- **b. Before choosing RELEASE.** P-06: `inventory.sh` through SSM, then fill `models/marlin2b/serving-version.json` `processor_config_digest`/`preprocessor_config_digest` and the PINNED list in `infra/runbooks/artifacts.py` (RB:38) — edits the tree, so first. Extend the known-good schema proof to 0025 (both targets in `infra/rollout/known-good.json` stop at 0023; RR:51, PI:169). Gates G1–G6 (RR:17-26).
- **c. Window opening (RO:68-72):** W1 bundle + 20-prepull, W2 snapshot, W3 10-inventory, W4 25-save-edge, W5 30-pause.
- **d. Hosted migration apply (RO:73-74, 124-188):** W6 a fresh verified dump, restore check and copy apply (verified-dump-first rule RS:286-313; the dump is operator-run, RS:312). W7 the coordinator runs `migrate.py plan`, then `apply --expect $COPY_DIGEST`.
- **e. After W7, on hosted:** publish-card, the P-02 dry-run, then credit-transition (G8 steps, `evidence/g/G8-6a075c5.md:117-127`); then grant, adjust, key revocation and the 0.8 key inventory (RB:37-44). **Ordering defect:** RB puts 0.3/0.4 "before the window", but activation calls 0022's `infrx.set_feature_flag` (`infrx/operations/transition.py:206`), so it cannot run until after W7. Whether the certify tenant (key 142c7d81) already holds its 10,000 grant is not recorded.
- **f. Install (RO:79-84):** W8 40-checkout, W9 45-s3-check, W10 50-install, W11 60-verify-local, W12 verify-external with `failures: 0`, W13 record. `INFRX_SET` must add `ACCOUNTING_REGIME=credit` and `ACTIVE_RATE_CARD_VERSION`; RO:50 still says to leave the regime unset.
- **g. Observe (RB:55-61):** O3, then O4 without `P24_APPROVED`, then O5 and O6.
- **h. Freeze, fill, validate, certify (RB:63-152).** Soak 0.25 req/s × 14,400 s = 3,600 requests; poll with 78-e4b-report.sh. Runtime login: the launcher rewrites the env file's `DATABASE_URL` to port 6543 (`session-03-tools/rollout/e4b-certify3.sh:14-23`) and lacks the E4C flags. No committed step sets the `infrx_runtime`/`infrx_monitor` passwords or their SSM names (only `evidence/d/D10-HANDOFF-31c2112.md:138`); RV-09 needs this.
- **i. Journey and drills:** §5 journey, then §6 drills incl. the rollback drill with 85-known-good-box.sh (`infra/runbooks/rollback.md:51-88`) and drift.py.
- **j. Evidence and verdict (RB:297-313):** `models/marlin2b/results/E4C-box-<r7>/run<N>-<UTC>/`, `research/plan/evidence/e/E4C-<r7>/`, verdict `research/plan/evidence/e/E4C-<r7>.md` plus `gates.BACKEND-READY`.

## 3. Operator-held inputs (all nine rows DECIDED in PI:158-170; the tracker still marks each open)

| Input | Missing enactment | Coordinator-executable from this host? |
|---|---|---|
| P-01 | publish-card (a hosted write) | Technically yes (DB password from SSM); hosted-DB access was policy-denied earlier (S03:307) |
| P-02 | the read-only dry-run | same caveat; commit only the sha256 and key prefixes (production numbers are barred) |
| P-05 | operator confirms the second user's email (PI:162); coordinator then runs grant + issue-key | the email/auth read touches PII: operator |
| P-06 | inventory.sh through SSM + the digest commit | yes, now |
| P-17 | nothing before the run; decided at handback | n/a |
| P-18 | enacted by E4C-PREP; only the 0.1 timestamp check remains | yes; tracker row stale |
| P-24 | +40,000 adjust (PI:168) and revoking the two pre-cutover keys (RB:41) | no, operator-held |
| P-25 | alert destination; PITR read; daily verified dumps; known-good proof to 0025 | SNS topic refused by the tool policy (operator creates it or supplies a webhook SecureString); coordinator can add `sns:Publish` to the instance role and install python3-boto3 (observe.md:125-146); PITR read needs the operator's `SUPABASE_ACCESS_TOKEN` (RS:264-277); dumps contain PII (operator); known-good proof = a coordinator lane |
| P-26 | only the limitations text; the copy is on the tip (`apps/app/app/(console)/docs/content.ts:19`) | yes; tracker row stale |

Blockers the tracker does not list: `MEDIA_BASE_URL` (RB:182-186, operator-named https prefix serving the corpus clips); ratifying the `weights_sha256` derivation (`evidence/e/E4C-runbook-f2a245c.md:213`); the production-needle guard (still red at the tip; user-held).

## 4. Risks and irreversibles

- **Hosted 0019–0026** (0026 = D10-0026-FENCE, the fenced result write) are not purely additive: 0021 revokes grants and drops a policy (`evidence/i/I3-prep-bf29b92.md:197`), 0023 revokes `admit`. They are never reverted (RR:52); rollback is R3 maintenance or restoring the dump into a new project (RS:152-166). Beyond 0023 there is no rollback target until the proof is extended (RR:51).
- **CREDIT flags:** revert with `credit-transition --to legacy_usd` (PI:161); both rollback targets only ever served legacy_usd, so a rollback after activation is unproven.
- **Credits:** the tenant-2 grant of 10,000 cannot be reversed (PI:162); the +40,000 adjust reverses with a negative adjust (PI:168); the card is immutable (PI:160).
- **Public-facing:** W5 serves maintenance 503; the edge stays open during certify and the P4 burst goes through the public edge (RB:50-54); the legacy shared key returns 401 after install (RR:77-81). Revert with 93-restore-edge or 95-maintenance.
- **Evidence:** committing the ledger and drift numbers in report.json may be refused by the tool policy.

## 5. Dispatch plan

Can start now, in parallel: (1) P-06 through SSM + its commit (0.5–1 h); (2) a runbook-fix lane (2–3 h): W6's copy history seed 0001–0018 (RO:155-160, 184), the regime settings (RO:50), move RB 0.3/0.4 after W7, the E4C flags in the launcher, a runtime-login provisioning step; (3) operator asks: SNS topic or webhook, PITR token, the second user's email confirmation, `MEDIA_BASE_URL`, the key revocations, a verified dump; (4) IAM `sns:Publish` + boto3 on the box (0.25 h); (5) a decision on the needle guard.

Must wait for D10-MERGE-2: the known-good proof to 0025 (~2 h; the last proof lane took 128 min, S03:356); an E3C rerun on the new tip (11:55 wall) plus `make check` and G1–G6 (1–2 h); then freeze RELEASE.

Window estimate (once every input is in hand): W1–W5 0.5–1 h (est.); W6–W7 ~0.5 h (hosted timings unmeasured, RS:185-188); hosted CREDIT ops ~0.5 h plus operator latency; W8–W12 0.25–0.5 h (install ≈ 4 min, engine ready 168–181 s, RO:105/266); O3–O6 0.25 h; certify ~5 h (the last run took 4 h 51 m); journey 0.5–1 h; drills 1.5–2.5 h; evidence and decision 2–4 h. About 11–15 h wall plus operator waits.

## Verification log

- 2026-09-26 04:40Z: brief written from the agent's handback (tip f76ddc58); the coordinator verified the P-26 copy (`content.ts` REVOCATION_COPY) and the P-18 CRITERIA in `certify.py` before acting on §3.
- 2026-09-26 09:00Z (coordinator): SNS topic `arn:aws:sns:us-east-1:641134885443:infrx-pilot-alerts` created (P-25 alert destination; no subscription yet; the box role's sns:Publish grant refused by the tool policy → user-run, command in 15-pending-inputs). Push allowed on retry: origin at fde1527a. SSM password/monitor-DSN parameters and the P-06 inventory step remain user-run (classifier). Known-good proof reaches 0025 (KNOWN-GOOD-PROOF-2); 0026 (D10-0026-FENCE) will need KNOWN-GOOD-PROOF-3 (script prepared).

