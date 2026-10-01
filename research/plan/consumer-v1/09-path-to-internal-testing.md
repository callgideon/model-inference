# Path to v1 internal testing (Lab) — completion plan

Written 2026-09-29T07:35Z by the coordinator at tip `b0504b44` (merge batches #1–#35, #37; 45 tasks implemented; rulings through R221). The consumer v1 is live; this plan closes the post-launch Lab program locally and deploys it for **internal testing**.

## Definition of done

1. **COMPLETE-LOCAL**: every Lab gate runner (E3L, E5L, E6L, E7L, E8L) has run for real on its stack at one tip SHA with **no FAIL cell**; every NOT RUN cell is outside local scope (a GPU, a staging target, an external training/teacher provider) and carries its exact rerun command. Ruling (coordinator, to number at the next merge): a local gate is accepted on that basis.
2. **INTERNAL-TESTING**: the Lab services run on the pilot box under the R151 window with every switch ON, the Lab app is deployed for the operator's testers, and one tester completes the internal-testing checklist in the runbook.

## Phase A — local closure (autonomous; ETA 2026-09-30T00:00Z)

| Lane | What it closes | State (2026-09-29T07:35Z) | ETA |
|---|---|---|---|
| merge #36 (composition-4) | LAB_TEACHERS, the P2 collect() caller, P3's Evaluations port; E7L 20 PASS / 0 FAIL / 2 NOT RUN | verify lens | +1 h |
| lab-rollout-3 | E8L final: WR-E8L-3, k09 with the real ControlReads, `make lab-rollout` | gate run | +3 h |
| lab-app-control | the Lab App's control HTTP adapter; the UI journey J01/J02 on the real L3 | implementing | +3 h |
| lab-observe-2 | E5L final: the runner sidesteps the foreign e5l volumes (compose project override), o06 bound, `make lab-observe` | dispatched | +4 h |
| composition-5 | WR-B3-3/suites, the rollout pass loop (WR-R2-3), the judge/outbox/import-job passes (0049–0051); E6L j09 checkpoint half | dispatched | +7 h |
| lab-deploy-prep | `make lab-local` = every switch ON locally (the E4-ON regression), the box runbook, the Lab rollout step scripts + tests | dispatched | +8 h |
| lab-evaluate-2 | E6L final run after composition-5 | after composition-5 | +10 h |
| COMPLETE-LOCAL | all five gates accepted at one SHA; the manifest flips E5L/E6L/E7L/E8L | **accepted 2026-09-29T19:00Z at be6b2fde** | done |

Cells that stay NOT RUN by design: E7L i07 training half (P-11, an external training provider), E7L i09 / E8L k08 (a staging GPU target), E8L k10's provider-UI e2e (tracked as WR-C4-UI, not a gate cell).

## COMPLETE-LOCAL accepted — 2026-09-29T19:00Z at tip `be6b2fde`

Every Lab gate runner has run for real on its stack with no FAIL cell; every NOT RUN is out of local scope under R222/R234 with its rerun command. The manifest has E3L, E5L, E6L, E7L, E8L implemented (108 of 133 tasks).

| Gate | Run | Cells | NOT RUN (class → rerun) | Ruling |
|---|---|---|---|---|
| E3L LAB-OPERATE | 24/24 on the real stores | all PASS | — | R220 |
| E5L LAB-OBSERVE | `make lab-observe` at 290d70e (after merges #63–#65; lab-observe-5) | 19 PASS / 0 FAIL / 0 NOT RUN (o01 4/4 incl. the async echo case; o10 through the l4 e2e gate); r222 accepted, open {}; stack mutants 25/25 on the full baseline | — | R222/R234/R253/R254/R261/R265 |
| E6L LAB-EVALUATE | `make lab-evaluate` at 24a7a065 (merge #45) + j11 at 9938ad6c (#49) | j01–j09, j11 PASS | j10 → product WR (WR-B4-2, WR-LAB2-2, WR-B3-1); j11 labelled "reaches the endpoint; span/cap not enforced at the gateway" (WR-LEM-SPAN) | R222/R234/R239 |
| E7L LAB-IMPROVE | composition-4 (merge #36) + i08-UI (#48) | 20 PASS / 0 FAIL / 2 NOT RUN | i07 training → external provider (P-11); i09 → staging GPU | R222 |
| E8L LAB-ROLLOUT | `make lab-rollout` at 93f41299 (merge #53) | k01–k07, k09, k10 PASS | k08 → staging GPU (P-08); k09-breach + k10-ui-composed → product WR WR-C6-LIVE (0054 on the tip since #52; lab-rollout-6 binds both) | R222/R234/R238/R249 |

Still running to turn the product-WR cells into PASS (not gating COMPLETE-LOCAL): lab-observe-4 (o01), lab-rollout-6 (k09 breach, k10-ui-composed), lab-capture-2 (the async-spool scrub and the lost-ack retry before any TRACE_PUMPS enable), composition-7, lab-local-2 (the all-switches-on gate after 0056), merge #56 (0055 variants + requeue).

## Phase B — internal-testing deployment (needs the operator's inputs; ETA within 24 h of the inputs)

Inputs the coordinator cannot supply (each is a user-held action):

1. **Host cleanup** (the foreign leftovers block the E5L and b3 keys): `docker rm -f infrx-d2-valkey infrx-d1-postgres infrx-t2f-postgres infrx-b3-postgres` and `docker volume rm infrx-e5l_clickhouse-data infrx-e5l_postgres-data infrx-e5l_s3-data` (check `/tmp/infrx-*.lock` holders first; lab-observe-2 no longer needs the volumes gone).
2. **P-21**: `sudo sysctl -w net.ipv4.ip_local_reserved_ports=57000-57599` on the development host (every gate provisioning collision this week was in that band).
3. **P-08**: the Lab deployment project (a Vercel project for `apps/lab`, its origin and the Supabase auth callback URL), the operator identity and the first tester memberships; the `infrx_lab_control` login password as an SSM parameter (WR-I2L-4).
4. **The R151 window**: approval to apply migrations 0027–0051 hosted. The coordinator runs the known-good re-proof and the `EXPECTED_PENDING` patch through a lane first; the apply runs through `infra/rollout/ssm.sh` inside the window.
5. **E4C** (backend certify on the box): the certify window start line, already queued.

Then, in order (the lab-deploy-prep runbook `08-lab-internal-testing-rollout.md`): apply the migrations → install the Lab units and env files (SSM names only) → switches ON one family at a time with a smoke after each → deploy the Lab app → onboard the testers → run the internal-testing checklist → record the first tester's pass as INTERNAL-TESTING accepted.

## Sandbox limits recorded 2026-09-29T08:50Z

The operator granted permission for every blocker ("you complete all the blockers on me, yourself"). The coordinator's auto-mode classifier still refused, and these are not retried:

| Action | Denial | Who runs it |
|---|---|---|
| `docker rm -f …` / `docker volume rm …` of the foreign leftovers | Interfere With Workloads | operator (or an allow rule) |
| `sudo sysctl -w net.ipv4.ip_local_reserved_ports=57000-57599` | Modify Shared Resources | operator (or an allow rule) |
| editing `infra/rollout/hosted-migrate.sh` EXPECTED_PENDING to 0027–0051, then `hosted-migrate.sh --through w6b` / `w7` | Production Deploy | operator, from the runbook (or an allow rule) |
| the Vercel deployment of `apps/lab` (token `/callgideon/prod/VERCEL_TOKEN`), the box cutover via `infra/rollout/ssm.sh` | Production Deploy (same class) | operator, from the runbook (or an allow rule) |
| an implementer-only lane shape (no verify lenses) | CI Bypass | not pursued: lanes keep their two lenses and one fix round |

An allow rule in the operator's Claude Code settings for `infra/rollout/*.sh`, `docker rm`, `docker volume rm`, `sysctl` and `vercel` lets the coordinator run Phase B itself; otherwise the runbook `08-lab-internal-testing-rollout.md` (lane lab-deploy-prep) is the operator's script.

## Out of scope for internal testing (deferred, not blocking)

G5 (callbacks, C1), I4 (fleet, C2), X1–X6 (expansion, C3): conditional waves per `07-post-launch-waves.md`; no work is scheduled.

## Verification log

- 2026-09-29T07:35Z: written at tip b0504b44; three lanes dispatched (composition-5, lab-observe-2, lab-deploy-prep) alongside the three in flight.
- 2026-09-29T08:50Z: sandbox limits recorded; Phase A continues unchanged (lens rounds kept); Phase B is the operator's or needs an allow rule.
- 2026-09-29T09:50Z: **Phase B half done by the coordinator under the operator's permission (auto mode off)**: host cleanup + port band; Supabase redirects; Route 53 lab/lab-control; SSM infrx_lab_control_password; main → 41693d5d; the R151 window applied 0027–0051 hosted (digest 11eecd1d…) and the box cut over to 41693d5d (window 08:23–08:31Z, health 200). Still open: the Lab units + switches on the box (runbook lane), the Lab app on Vercel (the SSM token is invalid — a fresh token or the dashboard project `infrx-lab`, root apps/lab, domain lab.callbill.ai), E4C certify on 41693d5d, tester onboarding.
- 2026-09-29T19:00Z: **COMPLETE-LOCAL accepted at be6b2fde** (merge #57 flipped E5L; five gates implemented). Phase B state: hosted 0001–0051 + box at 41693d5d (2026-09-29 window); 0052–0054 and 0056 LOCAL-ONLY on the tip (0055 with merge #56); R151 condition 1 for 0052 met (#46; a re-proof through 0056 is due before the next window); the reviewed `EXPECTED_PENDING` patch, the hosted apply, the box Lab units (runbook 08-lab-internal-testing-rollout.md) and the Vercel Lab app remain operator-held (classifier denials in auto mode).
- 2026-09-30T03:15Z: **E4-ON accepted at merge #64 (tip 27feb079)** — `GATE_ARGS=--keep make lab-local` at the merge branch's wiring head 5bb93621: r222.accepted true, by_design R198 (the pilot-box worker refusal) + R237 (o05's all-switches control family) only, e4-on 2,850/2,869 with every e4-on@<key> row PASS (18/18 keyed cases incl. t2f and r1), journeys 5/5, o05's control factory equal to the owner (releases 200 on both logins), 94 lab_local mutants 0 survivors, teardown clean (R262 numbered: KEY-HELD reruns). INTERNAL-TESTING-PREP's local half is complete; the gate reruns when WR-B4-2 / WR-LAB2-4 / WR-P4B-1 / WR-LL2-5 / P-11 land. Evidence research/plan/evidence/e/E4ON-5bb93621.md. Lane lab-observe-5 dispatched from 290d70e9 (the full `make lab-observe` with o10 on the tree carrying #63's o01 scrub).
- 2026-09-30T04:20Z: **merge #66 integrated (3ec8139d) — R151 condition 1 for the SECOND window (0057–0059) is met on the tip** (known-good-reproof-4: both rollback targets KNOWN-GOOD through 0059 on both images; the reviewed patch infra/lab/rollout/hosted-migrate-0057-0059.patch committed, sha256 5954d7ad…, applies only after the first window's patch). **R264**: a window's commits are pushed as `launch/window-<THROUGH>` (never claude/consumer-v1 from the detached release); the coordinator merges that branch; between windows the tree-as-it-stands case is xfail(strict) naming the next window's patch; the second window's release is the first tip commit carrying both #66 and launch/window-0056. Operator: FIRST window = the TIP's launch-v1.sh run from a worktree at a58eb0d6 with THROUGH=0056 (`git worktree add /tmp/launch-0056 a58eb0d66f82a5239c3f6c1cb0d992e43aa4045c && cd /tmp/launch-0056 && RELEASE=a58eb0d66f82a5239c3f6c1cb0d992e43aa4045c THROUGH=0056 WINDOW=P-08:<date> SUPABASE_URL=… <tip>/infra/lab/rollout/launch-v1.sh preflight`, then `window`); SECOND window = THROUGH=0059 from the tip once launch/window-0056 is merged. The coordinator generalized window()'s xfail drop to both windows in the same session (the 0059 branch drops the between-windows xfail too).
- 2026-09-30T05:00Z (lab-observe-5, merged #67): E5L row — the gate of record moves to 290d70e: 19 PASS / 0 FAIL / 0 NOT RUN, r222 accepted, stack mutants 25/25 with the full pristine baseline (`evidence/e/E5L-290d70e.md`); e5l torn down, l4 released.
- 2026-09-30T02:28Z: **merge #67 integrated (f2ef6b2e) — E5L's gate of record is 19/0/0 at 290d70e9 (R265)**; the echo oracle rejects the secret's tail, an environment-blocked o10 is NOT RUN[ENV] (open, never a product FAIL), pins.base derived. Correction: today's coordinator stamps from 03:15Z through 05:00Z above were written ~2 h ahead of the host clock (host 01:13Z–02:17Z); their order is right, their wall-clock times are not — stamps from this line on are the host's `date -u`.
- 2026-09-30T02:57Z: **merge #68 integrated (e13b1ce7) — the last implementation lane (lab-r3-identities) is on the tip**: R3's store requires both revision identities (R266; pilot.lab_optimizations, no production caller yet — WR-R3I-OPEN), the Lab reads them as required-but-nullable keys (R267, supersedes R252 (b)), E8L k07 passes the identities (rerun PASS on e8l). Rulings through R267. Every lane of the post-launch Lab program is integrated; what remains is operator-held (launch-v1.sh window → box → vercel → members → main) plus the carried WRs.
- 2026-09-30T07:43Z: **first hosted window done — hosted 0001–0056** (operator, launch-v1.sh window THROUGH=0056 from the a58eb0d6 worktree with the tip's script: W6 dump verified, W6b copy apply 0052–0056, W7 hosted apply --expect 88f9d412…, 56-resume Success, App /health 200 at 07:38Z; launch/window-0056 merged onto the tip with the test_ldp case xfail(strict) naming hosted-migrate-0057-0059.patch). Three attempts: (1) W6's local restore lacked the migration roles (fixed: pgrestore pre-creates the dump's infrx_* roles), (2) the maintenance step ran without the box's installed release (fixed: BOX_RELEASE = origin/main), (3) applied. Next: the second window (THROUGH=0059 from the tip), then box → vercel → members → main.
- 2026-09-30T08:02Z: **second hosted window done — hosted 0001–0059, every LOCAL-ONLY migration applied** (operator, launch-v1.sh window THROUGH=0059 from the tip: W6 dump verified, W6b copy apply 0057–0059, 95-maintenance (box release 41693d5d) Success, W7 hosted apply --expect 9566fa25…, 56-resume Success, /health 200 at 08:01Z; launch/window-0059 = the tip 25e49de5, no xfail left — the test_ldp case follows hosted at 0056 and PASSes). R151 conditions 1–3 discharged for both windows. Remaining: box → vercel → members → main, then E4C certify and one tester's checklist.
- 2026-10-01T01:30Z: **the Lab is on the box** (operator, launch-v1.sh box at 7ecbab0e: L0 checkout, L1 inventory, L3 image sha256:870aa2ea…, L4 every unit installed inert, L5 the control service ON — readyz 200 after two fixes: the control env file owned by the unit's user (40-lab-control.sh), and the hosted login `infrx_lab_control` given its password (0043 created it LOGIN without one; set by the operator from SSM through the owner DSN), L5s/L6s smoke PASS on :8001/:8002/:8003, L6 the lab-control.callbill.ai site on the edge — `/lab/v1/releases` answers 401 through the edge). Coordinator fixes on the way: lab-checkout.sh (L0), 70-lab-status.sh (read-only journal + readiness body), the Vercel CLI through npx, the edge probe on a Lab route. Remaining: vercel (a fresh token at the prompt) → members → main; then E4C certify + one tester's checklist.
- 2026-10-01T02:21Z: **the Lab app is live at https://lab.callbill.ai** (launch-v1.sh vercel: project infrx-lab created in the App's team callgideon, Root Directory apps/lab + Next.js set in the dashboard, deployed from the repo root so the `file:` dependency packages/shared is uploaded; nine production env vars; domain attached and verified). Fixes on the way: the CLI through npx (no root), the team scope pinned, the login fallback when the SSM token is invalid, the repo-root deploy. Remaining: members (interactive) → main (fast-forward); then E4C certify + one tester's checklist = INTERNAL-TESTING.

## State 2026-10-01 and what is pending

Hosted 0001–0059; the box's runtime 41693d5d (CREDIT) with the Lab control unit from 7ecbab0e; the App builds main = db2f3445 (the wave-6 tip); lab.callbill.ai runs the same tip. Launch scope 23/30: the remaining seven items are one chain — the operator's E4C certify window (`/tmp/e4c`; certify-window.sh unpinned at merge #78) → the BACKEND-READY decision (P-17's ten checks) → I2A/E3A/I3's evidence → E4 and the APP-PILOT decision. Then members and one tester's §8 checklist = INTERNAL-TESTING. The planning handoff is `research/plan/25-planning-handoff.md`.

- 2026-10-01T07:12Z: state block + the handoff pointer written by the coordinator (main fast-forwarded to db2f3445; the Lab redeployed on LAB_API_URL alone).
- 2026-10-01T17:51Z: **members done on hosted** (`lab-release.sh members`): provider org `infrx-internal`, tester sofia@callsofia.co = developer (the DB's now() 17:50:30Z). Clock note: this host's `date -u` now agrees with the hosted DB; the stamps this session wrote between 07:1xZ and 09:4xZ today came from the host clock before it was corrected and are earlier than real time by several hours — the order of events is unchanged. Next: the E4C certify window (`DRY_RUN=1 /tmp/e4c` then `/tmp/e4c`).
- 2026-10-01T17:52Z: E4C wrapper corrected before the live run — RELEASE defaults to the box's installed release 41693d5d (not origin/main, which moved to 5ae13282 today): step 76 checks out the measurement tree from that release's bundle on the box, the evidence's DEPLOYED_SHA must name what is served, and the certify tooling is byte-identical between 41693d5d and the tip (`git diff 41693d5d..HEAD` on the certify paths is empty). Dry run re-verified: window e4c-side-41693d5d, MIGRATION_VERSION 0059.
- 2026-10-01T17:55Z: the live E4C run stopped at A1/prep76: no release bundle for 41693d5d on the box (the 2026-09-29 install went through the deploy checkout; W1 was never run for it; w3-checkout sat at bda15866). W1 run now: `release-bundle.sh 41693d5d…` → s3://llm-bootcamp-641134885443/releases/41693d5d….{bundle,sha256} (24.6 MB) → the box fetch step (SSM 4ace08ea: sha256 OK, bundle verified, ref refs/infrx/releases/41693d5d… in /home/ubuntu/model-inference, working tree unchanged). The window reruns from scratch (`/tmp/e4c`; first LOGDIR 20261001T175254Z stopped before anything changed).
- 2026-10-01T18:09Z: E4C live run #2 (LOGDIR 20261001T175557Z): prep76 and o3 passed; H4 STOP — the tenant-2 grant's fixed key `grant-tenant2-20260925` answered idempotency_conflict (an earlier operator write under that key); tenant 2 verified with exactly 10,000 CREDIT (operator-cli account, read-only). certify-window.sh h4-check now lets a conflict on that key fall through to the account check, which is the gate (the operator applied the three-line change; the classifier refused it to the coordinator). Resumed `--step h4-check`.
