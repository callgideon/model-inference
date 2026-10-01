# Carried-work register (state 2026-10-01)

Written by wave 6 lane plan-ledger (audit finding INT-07) at the wave base `08983639`. This is the tracked replacement for the carried list in the git-ignored `.claude/RESUME-NOW.md:6`. It has one row for each item in the five "Pending" lists of the [v1 audit](../evidence/coordinator/2026-10-01-v1-audit.md) and for each item that RESUME-NOW line names. Read it together with [09](09-path-to-internal-testing.md) (the launch path and its log) and [08](08-lab-internal-testing-rollout.md) (the runbook).

How to read the table:

- **Source.** `audit:N` means line N of the audit. Every other path is relative to `research/plan/` unless it starts with `apps/`, `infra/` or `tests/`. Line numbers are those at `08983639`.
- **Blocks testing.** Says whether the item blocks **internal testing**: one tester completing the checklist in 08 §8 on the launched Lab.
- **Owner.** A wave-6 lane name (for example `lab-release-tool`) means the item is scheduled in that lane (audit §7). `operator` means a person outside the sandbox. `planning` means the item is product work for the next planning session (audit §7, "Not a lane"). It is not scheduled.
- **No item is marked done** until its evidence is on the tip. When an item closes, append a log line below. Do not edit the row.

## 1. Launch steps and windows

| # | Item | Source | What it needs | Owner | Blocks testing |
|---|---|---|---|---|---|
| 1 | `members`: create provider org `infrx-internal` and the tester memberships on hosted | audit:51, audit:300, audit:387; `infra/lab/rollout/launch-v1.sh:190-199` | INFRA-01 first: the owner DSN comes from SSM by name and goes through psycopg (W6 `lab-release-tool`). Then the operator runs `TESTER_EMAILS=... infra/lab/rollout/lab-release.sh members` | operator (after `lab-release-tool`) | **yes**: 08 §8 step 1 needs a granted workspace |
| 2 | `main`: fast-forward origin/main from 41693d5d to the post-W6 tip (447 commits at audit time) | audit:52, audit:301; INT-08 / DT-01 | `lab-release.sh main`, after the wave-6 merges (audit §7 decision 8). Until it runs, every hotfix targets `claude/consumer-v1`, never main. Afterwards, set the overlay `main` field | operator | no |
| 3 | E4C certify window on the box at the served release, then the BACKEND-READY and APP-PILOT decisions. The E1B, E4, E3A and I3 cells are window-bound on the same run. Readiness findings RV-04, RV-08, RV-09 and RV-10 (open since dff31efc): closed by P-17 check 8 in the E4C window | audit:53, audit:302, audit:388; INFRA-02; overlay `findings` (`evidence/coordinator/2026-09-24-S3-reconciliation.md`) | `certify-window.sh` takes RELEASE as an input, not the d3a99e01 literal (W6 `certify-release`). Then the operator's window: `e4c-certify.sh`, certify-window.sh | operator + coordinator | no (it gates the consumer backend decision, not the Lab checklist) |
| 4 | Input P-01: launch rate card. Decided, enactment pending | audit:53; `15-pending-inputs.md:160` | Enacted and verified in the E4C window (P-17 check) | coordinator at E4C | no |
| 5 | Input P-02: legacy USD balance transition. Decided, enactment pending | audit:53; `15-pending-inputs.md:161` | Enacted in the E4C window | coordinator at E4C | no |
| 6 | Input P-05: auth and onboarding values. Decided for the backend part; public onboarding stays open | audit:53; `15-pending-inputs.md:162` | E4C two-tenant cells; public onboarding is A2/I2A | coordinator / I2A | no |
| 7 | Input P-17: mechanical BACKEND-READY acceptance (ten checks) | audit:53; `15-pending-inputs.md:164` | The E4C run, then the decision | coordinator at E4C | no |
| 8 | Input P-24: E4C box profile; the canary timer in 72-observe-install.sh is documented BLOCKED | audit:53, audit:305; `15-pending-inputs.md` P-24 row | The E4C window; confirm whether `infrx-observe.timer` is installed on the box (`73-observe-status.sh`) | operator | no |
| 9 | Input P-25: TTLs and known-good. Alert delivery is documented BLOCKED | audit:53, audit:305; `15-pending-inputs.md` P-25 row | The E4C window; an SNS subscription for alert delivery | operator | no |
| 10 | One tester's pass of the internal-testing checklist, recorded as INTERNAL-TESTING accepted in 09 and in the coordinator log | audit:54, audit:303, audit:389; `consumer-v1/08-lab-internal-testing-rollout.md:246-262` | Item 1 first | operator + tester | **yes** (this is the acceptance itself) |
| 11 | After `main`: run `git fetch origin main` in every worktree, then `make check` on the merged SHA to record the first post-merge green run and its duration | audit:395 | Item 2 first | coordinator | no |
| 12 | Next hosted window (0060+): a KNOWN-GOOD re-proof through the newest migration (R151 condition 1), a reviewed EXPECTED_PENDING edit with its 0059 anchors (`hosted-migrate.sh:34,101,137`), an operator window reference, and a new THROUGH case (DT-13) | audit:304, audit:393 | Nothing exists yet. `hosted-migrate.sh:35` expects 0057–0059 and would refuse. W6 `lab-release-tool` documents the procedure | coordinator + operator | no |
| 13 | Infra tree cleanup before the next window (INFRA-03/05/06/07), so that the window tooling is one edit in one script | audit:306 | W6 `infra-libs` (INFRA-05/06), `lab-release-tool` (INFRA-03), `docs-state` (INFRA-07) | W6 lanes | no |

## 2. Lab product work carried from the post-launch program

| # | Item | Source | What it needs | Owner | Blocks testing |
|---|---|---|---|---|---|
| 14 | WR-B4-2: the experiments table plus B3's subscription and decision listing. E6L j10 depends on it; `/evaluations` answers 503 | audit:55; `evidence/b/B4-7683330.md:59` | A lab-sql slice + composition; E4-ON o07 and E6L j10 rerun when it lands (09:75) | planning (lab-sql / composition) | no (surface 503, not a checklist step) |
| 15 | WR-LAB2-4: `run_rows` and `checkpoint_rows` for PgRunLedger. j10 depends on it (09 names WR-LAB2-2 in the same row); `/pipelines` answers 503 | audit:55; `evidence/coordinator/COMPOSITION-4-b7db806.md:172` | A lab-sql slice; E4-ON reruns | planning (lab-sql) | no |
| 16 | WR-B3-1: persist the CheckpointLedger tables. j10 depends on it | audit:55; `evidence/b/B3-582c7e4.md:36` | A lab-sql migration (0060+, under a new R151 window) + B3 rebinding | planning (lab-sql) | no |
| 17 | WR-LL2-5: `pilot._lab_traces` is unmounted, so traces answer 404 | audit:55; `evidence/e/E4ON-fd0aba04.md:214` | A composition lane (pilot.py; after W6 `api-L1`'s split) | planning (composition) | no |
| 18 | WR-P4B-1: the P4.b teacher slice | audit:55; `evidence/e/E4ON-fd0aba04.md:15` | A pipelines/composition lane; E4-ON reruns | planning | no |
| 19 | WR-LDP-7: no per-role Lab logins (eval/judge/datasets), so `launch-v1.sh box` L7 is skipped and those worker roles stay OFF on the box | audit:56, audit:303; `consumer-v1/08-lab-internal-testing-rollout.md:142-152`; `infra/lab/rollout/launch-v1.sh:160` | A lab-sql grant migration (0060+, new R151 window with its reviewed EXPECTED_PENDING) + an E4-ON o03 run on the role logins | planning (lab-sql) + operator | no for the checklist; **yes** for eval/judge/datasets testing |
| 20 | WR-LEM-SPAN: the gateway does not enforce span/cap for a Lab finite-video case (R239: span advisory) | audit:57; `evidence/e/E6L-J11-9938ad6.md:150` | A gateway check; E6L j11 then loses its label | coordinator | no |
| 21 | WR-LW9-6 / F5: single-snapshot `PgReleaseStore.live_and_tally` + `pilot.releases()` | audit:58; `evidence/l/LAB-SQL-LW9-751ff6a.md:267-280` (exact diff) | Re-anchors every D9 fake and mutant; carried by coordinator decision. Until it lands, release progress may mis-sum under a concurrent settle | planning | no |
| 22 | WR-LW9-5: research text fix at COMPOSITION-7:119 (INT-10) | audit:59; `evidence/l/LAB-SQL-LW9-751ff6a.md:264` | **Applied in W6 plan-ledger** (COMPOSITION-7-252b65e.md:119 + its log line) | W6 `plan-ledger` | no |
| 23 | WR-LW9-4: a later R3 CLI or worker passes `identities=(base, variant)` + `PgLabVariants` (the condition attached to WR-R3I-OPEN) | audit:65; `evidence/l/LAB-SQL-LW9-751ff6a.md:262` | Applies when item 24 gets a caller | planning | no |
| 24 | WR-R3I-OPEN: `pilot.lab_optimizations` has no production caller (A11; audit §7 decision 5: keep and document it, do not delete) | audit:65, audit:152; `evidence/r/LAB-R3-IDENTITIES-cf9d30f.md:133` | A later R3 CLI/worker composes through it (with WR-LW9-4); W6 `api-L1` documents it | planning (+ W6 `api-L1` docstring) | no |
| 25 | WR-LR7 open issues: 0054's refusal of a mixed or legacy-USD release unit fails the whole releases listing (needs a per-row degrade ruling); the C7-RV-1/C7-RV-2 decide-path test gaps; the unit-login e2e passes since 0059 (WR-LR7-GRANT applied at merge) | audit:60; `evidence/e/E8L-e65bbec.md:360-367` | A ruling + a rollout lane. Not blocking because `rollout_routing` is OFF (P-12) | planning | no |
| 26 | CMO-4: a runner-level lost-ack retry case over the real `worker/attempt.py` retry | audit:61; `evidence/t/LAB-CAPTURE-2-4f87f54.md:137` (repro command there) | A worker lane. Required before any TRACE_PUMPS enable (default OFF), which also needs 0057 and an E4-ON run on the enable tree | planning (worker) | no |
| 27 | WR-C6-B1-FLAKE: the revocation case in `apps/infrx-api/tests/w/test_worker_lab_eval_pg.py` fails 1–2 of 17–20 runs (a killed attempt charged twice) | audit:62; `evidence/coordinator/COMPOSITION-6-a6cbba5.md:242` | Root cause it before LAB_EVAL_WORKER is turned on (possible double debit) | planning (B1 / worker main) | no while the eval worker is off |
| 28 | k08 (E8L OPT-PARITY) and E7L i09: NOT RUN pending a staging GPU target (P-08 staging half) | audit:63; `consumer-v1/09-path-to-internal-testing.md:23,35` | An allocated GPU window; rerun `make lab-rollout` / `make lab-improve` | operator (GPU allocation) | no |
| 29 | P-11: external training provider. E7L i07's training half is NOT RUN; the training and annotation roles refuse by name (WR-I6-3b) | audit:64; `tasks.json:3355` (I6 closure_note) | A selected provider and terms (15 P-11) | operator / product | no |
| 30 | WR-L3-2: the engine smoke adapter is still the R203 stand-in for E3L | audit:66; `tasks.json:2028` (L3), `tasks.json:2235` (E3L) | A real engine smoke adapter on L3 | planning | no |
| 31 | WR-E3L-2: ports (user-held) | audit:66; `tasks.json:2235` (E3L closure_note) | The operator's port reservation | operator | no |
| 32 | WR-LSQ-9-C: rerun the L4 real-L3 journey | audit:66; `tasks.json:2053` (L4 closure_note) | An L4 journey rerun on the real L3 | planning | no |
| 33 | WR-COMP-2: an L3 dev target for B1 | audit:66; `tasks.json:2810` (B1 closure_note) | A composition slice | planning | no |
| 34 | WR-R3-3: GPU optimization evidence through experiment branches | audit:66; `tasks.json:3245` (R3 closure_note) | Measured results on `marlin2b` experiment branches | planning (+ GPU) | no |
| 35 | WR-G4T-3: trace export composition | audit:66; `tasks.json:1813` (G4T closure_note) | A composition slice | planning | no |
| 36 | WR-N4-3: a durable import-job queue | audit:66; `tasks.json:2737` (N4 closure_note) | A datasets slice | planning | no |
| 37 | WR-P1-D8-C | audit:66; `tasks.json:2988` (P1 closure_note) | The composition half named in P1's note | planning | no |
| 38 | WR-J3-D8-Cb | audit:66; `tasks.json:1022` (J3 closure_note) | The composition half named in J3's note | planning | no |
| 39 | WR-I7-1b: the rollout pass loop (WR-R2-3) | audit:66; `tasks.json:3390` (I7 closure_note) | A rollout worker slice | planning | no |
| 40 | WR-R2-3: the rollout pass loop (with WR-I7-1b) | audit:66; `tasks.json:3390` | As item 39 | planning | no |
| 41 | WR-C4-UI: the provider-UI half of E7L i08 (follow-up, not a gate cell) | audit:66; `tasks.json:3481` (E7L closure_note) | A Lab UI lane | planning | no |
| 42 | WR-V2-2: content reads through C2 never landed (LAB-06). The dead "Show content" offer is removed in W6 (audit §7 decision 6) and this item stays for planning | audit:227; `tasks.json:2484` (V1M) | Wire C2 content reads, or keep them unavailable; wiring content reads must re-add a Lab caller for the C2 RPC (`lab_content_refs`, migration 0041; the Lab adapter `lib/services/content/refs.ts` was removed at merge #75) | W6 `lab-E` (removal) + planning | no |
| 43 | WR-C3F-2: `submitFeedback` (`apps/app/app/actions.ts`) is composed into no page | audit:234; `evidence/c/C3F-c84aa8c.md:77` (the wiring patch) | App composition (feedback flag 0028 off by default) | planning (App) | no |
| 44 | LAB-14: the two console-lint warnings come from pre-Lab frozen-contract files and need a deliberate decision | audit:234 | A decision (frozen contracts are untouched in W6; no W6 lane owns LAB-14) | planning | no |
| 45 | The real-stack Lab suites (14 skipped in `make lab-test`: LAB_B4_REAL, LAB_P4_REAL, LAB_R4_REAL, LAB_N_REAL, LAB_L4_REAL, LAB_V1M_REAL + `make lab-e2e`) need Docker and the task-local keys | audit:235 | A run on the keys (not run in the read-only audit) | coordinator | no |
| 46 | `codex/longclip`: the one unmerged lane branch (WIP 0665bdb2, evidence only), shelved under P-23 (INT-09) | `20-platform-handoff-2026-09-24.md:224`; audit INT-09 | Resume only if the user reopens P-23. Cherry-pick the evidence or delete the branch then | user (P-23) | no |

## 3. Wave-6 cleanup items, by audit pending line

These rows record where each pending item of the api, lab-app, infra and docs-tests dimensions is scheduled. Each closes with its W6 lane's evidence.

| # | Item | Source | W6 lane | Blocks testing |
|---|---|---|---|---|
| 47 | A1: the composition-root split (`infrx/lab/compose.py`, release read models, a `pilot.py` docstring index) | audit:145 | `api-L1` | no |
| 48 | A2: delete the dead D5 interim fallbacks in `routes/relay.py` | audit:146 | `api-L2` | no |
| 49 | A3: three stale ImportError guards become direct imports | audit:147 | `api-L1` | no |
| 50 | A4: retire the legacy chat chain (`routes/chat.py`, `usage.py`, `replay_usage.py`, USAGE_*, the always-zero gauges) | audit:148 | `api-L2` | no |
| 51 | A5: one `infrx/state/rpc.py` replacing the five `_call` copies + two row helpers | audit:149 | `api-L3` | no |
| 52 | A6, A7, A8: one `iso_z`; Gone→410 in `lab_auth.REFUSALS`; shared Lab route helpers | audit:150 | `api-L4` | no |
| 53 | A13, A14: ruff/pyright config, `make api-lint` / `make api-typecheck`, the genuine pyright errors | audit:151 | `api-L5` (+ `makefile-pins` targets; A13 also in `api-L3`/`api-L4`) | no |
| 54 | A11: the R3 composition stays, documented (see item 24) | audit:152 | `api-L1` | no |
| 55 | A12: mechanical splits of `worker/engine.py` and `traces/spool.py`; the `lab/workers/__main__.py` split after A1 | audit:153 | `api-L5` (the A1 follow-up is planning) | no |
| 56 | Whole-tree pyright exits 250 (OOM) on this host; run it per package | audit:154 | `api-L5` / `makefile-pins` | no |
| 57 | LAB-01: `make lab-typecheck` is red in a stale checkout; `@infrx/shared` becomes a `link:` dependency | audit:226 | `lab-A` | no |
| 58 | LAB-06 / WR-V2-2: remove the dead content offer (item 42) | audit:227 | `lab-E` | no |
| 59 | LAB-03: one `LAB_API_URL` (code half `lab-B`; deploy half `lab-release-tool`; the Vercel variable by the coordinator) | audit:228 | `lab-B`, `lab-release-tool` | no |
| 60 | LAB-04, LAB-05: one HTTP transport, one session-client module | audit:229 | `lab-B` | no |
| 61 | LAB-02: delete `packages/shared/console` (2,677 duplicated lines) | audit:230 | `lab-A` | no |
| 62 | LAB-07, LAB-08, LAB-09, LAB-10: one capability table, one Actor, common action helpers, one shapes module (LAB-10 in `lab-B`) | audit:231 | `lab-D`, `lab-B` | no |
| 63 | LAB-11: the Lab docs (apps/lab/README.md, CLAUDE.md:23 and its Commands, the dead Makefile branches, the preview switches) | audit:232 | `docs-state` (Makefile half `makefile-pins`) | no |
| 64 | LAB-12: four mutant runners import the shared harness; the package.json test glob | audit:233 | `lab-E` (the test glob: a wiring request to `lab-A`, the package.json owner) | no |
| 65 | INFRA-01: `members` reads the owner DSN by SSM name through psycopg (prerequisite of item 1) | audit:300 | `lab-release-tool` | **yes** (through item 1) |
| 66 | INFRA-02: certify-window.sh's RELEASE becomes an input (prerequisite of item 3) | audit:302 | `certify-release` | no |
| 67 | INFRA-03: `launch-v1.sh` becomes `lab-release.sh`; the spent window patches move to evidence | audit:306 | `lab-release-tool` | no |
| 68 | DT-02 (with DT-03/07/08/09/14/17): the tracked state file `research/plan/25-state-2026-10-01.md` and the README/CLAUDE/HANDOFF re-points | audit:390 | `docs-state` (`handoff` last) | no |
| 69 | DT-04 (with DT-05/06/12/15): the env-file owner assertion, the launch/lab-checkout cases, the pgrestore executed half, the 70-lab-status scrub test | audit:391 | `infra-libs`, `lab-release-tool`, `pgrestore-tests` | no |
| 70 | DT-10 (with DT-11/16): the Makefile pins and the measured `make check` wall-clock | audit:392 | `makefile-pins` | no |
| 71 | DT-13: a THROUGH case for the next window (item 12) | audit:393 | `lab-release-tool` | no |
| 72 | INT-01, INT-02, INT-03, INT-04, INT-06, INT-07, INT-10, INT-11: overlay, manifest and register hygiene | audit:67 | `plan-ledger` (this lane) | no |
| 73 | INT-05: dated state banners in the runbooks and READMEs | audit:67 (INT-01..INT-07 range) | `docs-state` | no |
| 74 | INT-09: `codex/longclip` (item 46) | audit:67 | `plan-ledger` (register row) | no |
| 75 | launch-v1.sh shim removal (W7): the deprecation shim forwarding to `lab-release.sh` goes | merge #71 (WR-W6-LRT-3); `infra/lab/rollout/launch-v1.sh` | W7 | no |
| 76 | LAB-03 deploy half: switch the env list to `LAB_API_URL` after lab-B merges; the coordinator sets the Vercel var; `vercel env rm` the six names | merge #71 (WR-W6-LRT-3); `evidence/w6/lab-release-tool-5c6c9e6.md` §7 | `lab-release-tool` follow-up after `lab-B` + coordinator | no |
| 77 | The live box's checkout 7ecbab0e predates `infra/rollout/box-lib.sh`: 45-lab-site and 90-lab-revert carry an inline `caddy_reload` fallback for it (fix round 6945a627); 72-observe-install is BLOCKED (exit 3) on that checkout by design. The next `lab-release.sh box` run's L0 (lab-checkout) advances the checkout and clears both | merge #72 (infra-libs); `evidence/w6/infra-libs-fa3addbd.md` Merge | operator (the next `lab-release.sh box`) | no |
| 78 | Pre-existing red `tests/integration/backend/recovery/test_runbooks.py::test_e4c_rb09_the_copy_is_seeded_with_hosted_s_own_applied_history`: `infra/runbooks/rollout.md` §W6/§W7's migration range text (`0001-<newest>`) is stale against the tree's newest migration 0059 | merge #76 (DS-RV-6); `evidence/w6/DOCS-STATE-a146fd0.md` OI-1 | unassigned (rollout.md has no wave-6 owner) | no |
| 79 | Pre-existing problems of the recovery mutant list (`mutants_i3b.py --layer all`) on the D form (`INFRX_I3B_PG=d INFRX_D_TASK=d3 INFRX_D1_IMAGE=supabase`): `i3bm114` SURVIVES (rc10b's 2 cases pass with `wait_http`'s sleep removed); `i3bm117` and `i3bm33` have no case (rc08b and rc06 need E2's compose stack); `i3bm54` is baseline-red (rb05 is red in the mutant copy). Same at the lane's base 2add8e0a and head 5a03bbaf | merge #77 (PGR-3); `evidence/w6/DT-06-5a03bba.md` Merge | unassigned (recovery harness; no wave-6 owner) | no |

## 4. RESUME-NOW:6 cross-check

`.claude/RESUME-NOW.md:6` (git-ignored, in the integration checkout; quoted at audit:394) lists these carried items: WR-LEM-SPAN (row 20), WR-LW9-5 (22), WR-LW9-6 (21), WR-LR7 e2e-on-unit-login (25), CMO-4 (26), WR-C6-B1-FLAKE (27), k08 P-08 GPU (28), j10's WR-B4-2 (14), WR-LAB2-4 (15) and WR-B3-1 (16), WR-LL2-5 (17), WR-R3I-OPEN (24) and P-11 (29). Each one has a row above. From now on, RESUME-NOW should point at this file. RESUME-NOW is outside git, so the coordinator makes that edit.

## Verification log

- 2026-10-01: Created by W6 plan-ledger at the base 08983639 (INT-07). It has 74 rows: every bullet of the audit's five pending lists, cited as `audit:N`, and every RESUME-NOW:6 item. The coverage oracle `research/plan/evidence/w6/PLAN-LEDGER-raw/oracle.py` (item INT-07) checks every WR, input, finding id and pending line against this file.
- 2026-10-01: Row 3 (E4C) gains readiness findings RV-04/08/09/10, closed by P-17 check 8 in the E4C window; blocks testing: no (merge #70, plan-ledger lens PL-3).
- 2026-10-01: Rows 1 and 2 name the tool (`TESTER_EMAILS=... infra/lab/rollout/lab-release.sh members`, `lab-release.sh main`); rows 75 (launch-v1.sh shim removal, W7) and 76 (LAB-03 deploy half after lab-B) added (merge #71, lab-release-tool WR-W6-LRT-3).
- 2026-10-01: Row 77 added (merge #72, infra-libs): the live box's pre-box-lib checkout 7ecbab0e (45/90 caddy_reload fallback, 72 BLOCKED(3)) is cleared by the next `lab-release.sh box` L0.
- 2026-10-01: Row 78 added (merge #76, docs-state DS-RV-6): the pre-existing test_e4c_rb09 failure (rollout.md's stale migration range), unowned in wave 6.
- 2026-10-01: Row 79 added (merge #77, pgrestore-tests PGR-3): the recovery mutant list's four pre-existing D-form problems (i3bm114 survives, i3bm117/i3bm33 no-cases, i3bm54 baseline-red); rb09 stays row 78.
- 2026-10-01: Row 42 (WR-V2-2) notes that wiring content reads must re-add a Lab caller for the C2 RPC: merge #75 (lab-E LAB-06) removed the Lab's only caller, `apps/lab/lib/services/content/refs.ts`; immutable migration 0041's header still names it.
