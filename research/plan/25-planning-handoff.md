# Planning handoff — 2026-10-01 (after the v1 launch, the Lab's internal-testing deploy and the wave-6 clean-up)

For the next planning session. Every fact here is cited to a record in this repository; a fresh session reads the files in §2 before it plans. The one-page state is `research/plan/25-state-2026-10-01.md`; this document adds what is pending, the risks and the prompt.

## 1. What is built and live (by system)

| System | State of record | Where it is recorded |
|---|---|---|
| Consumer API + runtime (pilot box, single L40S) | runtime release **41693d5d** in the CREDIT regime; public edge marlin2b.callbill.ai (health 200); maintenance/resume through `drain.sh` of the installed release | session record 2026-09-24-session-03.md (the 2026-09-29 window; the 2026-09-30 windows' 95-maintenance/56-resume lines); `research/plan/25-state-2026-10-01.md` §"What runs where" |
| Hosted Supabase schema | **0001–0059** applied (window 2026-09-30: 0052–0056 digest `88f9d412…`, 0057–0059 digest `9566fa25…`); both rollback targets KNOWN-GOOD through 0059 (`infra/rollout/known-good.json`, KNOWN-GOOD-REPROOF-4) | 09-path-to-internal-testing.md log 2026-09-30; `apps/app/supabase/migrations/README.md` (the state of record is the plan, the files are immutable) |
| The App (consumer web) | builds from `main`; **main = db2f3445** since 2026-10-01 (the wave-6 tip; fast-forwarded from 41693d5d) — Vercel project `infrx-app`, team callgideon, production ● Ready after the move | session record 2026-10-01 (main fast-forward line); `vercel ls infrx-app` |
| The Lab on the box | Lab control unit ON from the checkout **7ecbab0e** (image `sha256:870aa2ea…`), readyz 200 on 127.0.0.1:8003; the edge lab-control.callbill.ai serves `/lab/v1/*` from it (a release list answers 401 without a token); the other Lab units installed inert; L7 roles skipped (WR-LDP-7) | 09 log 2026-10-01 01:26Z; runbook 08 §"State 2026-10-01"; `infra/rollout/README.md` §6 |
| The Lab app | **https://lab.callbill.ai** live (Vercel project `infrx-lab`, team callgideon, Root Directory `apps/lab`, deployed from the repo root); redeployed from the wave-6 tip on **LAB_API_URL** alone (the six LAB_*_URL names removed from the project) | session record 2026-10-01 (vercel lines); `infra/lab/rollout/lab-release.sh` web |
| Plan ledger | manifest 109/133 implemented (E1B flipped 2026-10-01); overlay `gate_records` E3L/E5L/E6L/E7L/E8L/E4-ON/COMPLETE-LOCAL; rulings through **R269** | `research/plan/tasks.json`; `research/plan/evidence/coordinator/progress-state.json`; `research/plan/08-contracts-v1-encoding.md` §10 |
| Wave 6 (clean-up) | **all sixteen lanes integrated** — merges #69–#84 (lab-A/B/D/E, plan-ledger, lab-release-tool, infra-libs, makefile-pins, docs-state, certify-release, pgrestore-tests, api-L1/L2/L3/L4/L5); the last five API lanes landed in fast mode (the operator's 2026-10-01 instruction: the lane's own tests, focused suites and mutant lists as the gate, no lens round); real `make api-lint` / `make api-typecheck` (pyright baseline 458); the Lab composition root is `infrx/lab/compose.py` | `research/plan/evidence/coordinator/2026-10-01-v1-audit.md` §7; `research/plan/evidence/w6/` |

## 2. Reading order for a fresh planning session

1. `research/plan/25-state-2026-10-01.md` — the one page.
2. `research/plan/evidence/coordinator/2026-10-01-v1-audit.md` — 73 findings, §7 decisions, the lane plan (what wave 6 did and did not do).
3. `research/plan/consumer-v1/10-carried-work-register.md` — every carried item with its source, need, owner and whether it blocks testing (rows 1–79 at this writing).
4. `research/plan/consumer-v1/08-lab-internal-testing-rollout.md` (the runbook that ran; §8 the tester checklist) and `09-path-to-internal-testing.md` (COMPLETE-LOCAL, the window logs, the state block).
5. `research/plan/08-contracts-v1-encoding.md` §10, rulings R260–R269 (the window rule R264/R269, E4-ON R262, E5L R265, R3 identities R266/R267, `@infrx/shared` R268).
6. `CLAUDE.md` (conventions, commands, the launch tooling) and `research/plan/evidence/coordinator/session-03-tools/LANE-RULES.md`.
7. The session record `research/plan/evidence/coordinator/2026-09-24-session-03.md`, last 60 lines (the launch days, verbatim).

## 3. Exactly what is pending

**Status addendum 2026-10-01T18:23Z:** item 1 (members) is DONE on hosted (org `infrx-internal`, one developer tester). Item 2, the E4C certify window, is RUNNING on the box from the coordinator session (LOGDIR `~/infrx-e4c/20261001T175557Z`, run 20261001T181109Z, RELEASE = the installed 41693d5d; preconditions, config pin and served build green; the measured cells in progress; the two outage drills answered skip; O4–O6 and the canary skipped by decision). Its result, the BACKEND-READY decision (P-17 checks 1, 5, 7 stay false by the known blocks) and the APP-PILOT decision follow in plan 09 and the session record.

**Operator-held (in order; these are the remaining launch scope, 23/30 → 30/30):**

1. `TESTER_EMAILS="…" infra/lab/rollout/lab-release.sh members` — provider org `infrx-internal` + tester memberships on hosted (register row 1; blocks the checklist).
2. The **E4C certify window** on the live release: `DRY_RUN=1 /tmp/e4c` then `/tmp/e4c` from a terminal (the wrapper sets RELEASE=origin/main, CORPUS_CACHE, VIDEO_FILE and TENANT2_USER from `~/e4c/tenant2.user`; hours; resumable with `--step <s> --logdir <d>`). Inputs P-01/P-02/P-05/P-17/P-24/P-25 are decided (15-pending-inputs.md §Decisions 2026-09-25) and enacted by the window.
3. Then the coordinator records: the **BACKEND-READY** decision (P-17's ten mechanical checks), the I2A (live half done; gate evidence from the window), E3A (APP-LOCAL 17/17; decision deferred to after BACKEND-READY), I3 (runbooks merged; the operator run) and E4 flips, and the **APP-PILOT** decision.
4. One tester's pass of the internal-testing checklist (08 §8) → INTERNAL-TESTING accepted (register row 10).
5. A consumer window when the box's runtime should move past 41693d5d (30-pause … 50-install; a separate operator action — the App and the Lab already run the tip).

**The next hosted window (0060+)**: R269 — one reviewed commit editing hosted-migrate.sh's three window lines + the `hosted_migrate_*` anchors, after a KNOWN-GOOD re-proof through the newest migration (R151 condition 1); run through `lab-migrate.sh` from the tip; the window's commits land as `launch/window-<THROUGH>` (R264).

**Carried product work (register rows 14–46; none blocks the checklist):** j10's WR-B4-2 / WR-LAB2-4 / WR-B3-1 (evaluations surfaces 503), WR-LL2-5 (traces 404), WR-LDP-7 (per-role Lab logins; eval/judge/datasets OFF on the box), WR-LEM-SPAN, WR-LW9-6, WR-LW9-4 / WR-R3I-OPEN (the R3 CLI caller through `pilot.lab_optimizations`), WR-LR7, CMO-4, WR-C6-B1-FLAKE, k08 / i09 (a staging GPU), P-11 (external training), WR-V2-2 (content reads; the dead offer removed), WR-C3F-2, LAB-14, the real-stack Lab suites, `codex/longclip` (shelved, P-23).

**Wave-6 remainder (coordinator, W7):** the `launch-v1.sh` shim removal; the seven MinIO-gated and the lab_rollout stack mutants rerun on their stacks (api-L1 evidence 'Merge'); the ruff per-file baseline (155 findings in 98 files) worked down; PEP 695 for lab_auth's two generics; a full `make check` on the final tip recorded (the per-merge focused proofs are in the session record).

## 4. Risks that remain (from the audit)

- pyright: no whole-tree run (OOM on this host); per-package baseline only once api-L5 lands.
- The recovery mutant list's four D-form problems (i3bm114/117/33/54) and the pre-existing `test_e4c_rb09` red (infra/runbooks/rollout.md's stale W7 range) — register rows 78–79.
- The stale `e3bm62` ROUTERS anchor (lands with api-L2).
- The live box's checkout predates `box-lib.sh`: 45-lab-site/90-lab-revert carry an inline fallback; the next `lab-release.sh box` run's L0 advances it (register row 77).
- The two-tenant E4C cells depend on tenant 2's verification evidence (H4 stops otherwise; `operator-cli grant` records it).
- Vercel auth is the operator's CLI login on this host (the SSM token is invalid); the dashboard-only settings (Root Directory) are not scriptable from the sandbox.

## 5. The prompt for the planning session

```
You are planning infrx v1.1 (internal testing → the first external testers) for the model-inference repository.

GOAL: produce a numbered plan whose lanes take the consumer v1 and the Lab from "live for internal testing"
to "ready for the first external testers", starting from the state of record of 2026-10-01.

READ FIRST (in this order; cite by file:line):
  research/plan/25-state-2026-10-01.md
  research/plan/evidence/coordinator/2026-10-01-v1-audit.md (§7 = decisions; §1–§5 findings)
  research/plan/consumer-v1/10-carried-work-register.md (rows 1–79: source, need, owner, blocks)
  research/plan/consumer-v1/08-lab-internal-testing-rollout.md (§8 the tester checklist) and 09-path-to-internal-testing.md
  research/plan/08-contracts-v1-encoding.md §10 (rulings R260–R269)
  CLAUDE.md; research/plan/evidence/coordinator/session-03-tools/LANE-RULES.md
  research/plan/25-planning-handoff.md (this document's §3 and §4)

CONSTRAINTS (binding):
  - Rulings in 08 §10 stand; propose new ones unnumbered. R151/R201/R264/R269 govern hosted windows;
    applied migrations 0001–0059 are immutable (bytes hashed); the next migration is 0060.
  - CREDIT and USD are never mixed or converted; Lab paid work is USD with a named payer; provider_dev
    CREDIT starts at 0; the one-time 10,000 CREDIT signup grant is unchanged.
  - Anything reachable from the launched App/API ships behind a flag that defaults OFF; the E4 regression
    (make lab-local, R262/R257) runs before any hosted enable.
  - One owner per shared file per wave (Makefile, pyproject, package.json/lockfiles, tasks.json/overlay,
    CLAUDE.md/READMEs, lab-release.sh); everyone else files wiring requests.
  - Lanes are Opus implementers in their own worktrees (codex/<wave>-<lane>), tests first, mutants per
    decision; the coordinator merges with --no-ff batches, numbers rulings at merge, keeps the overlay
    (progress.py) and the session record append-only; secrets never on argv or in files.
  - The pilot box, hosted Supabase, AWS/SSM and Vercel are operator actions through
    infra/lab/rollout/lab-release.sh (preflight|box|web|members|main) and infra/rollout/certify-window.sh.

STATE FACTS A FRESH SESSION CANNOT DERIVE:
  - main = the wave-6 tip since 2026-10-01 (the App builds it); the box's consumer runtime is still installed
    at 41693d5d; the Lab control unit runs from the checkout 7ecbab0e; lab.callbill.ai runs the tip on LAB_API_URL.
  - The second E4C test tenant exists (its uuid is in ~/e4c/tenant2.user on the coordinator host; P-05 logged op).
  - Vercel: project infrx-lab in team callgideon (never humanbit), Root Directory apps/lab, deployed from the
    repo root; the CLI login is the operator's; the SSM token /callgideon/prod/VERCEL_TOKEN is invalid.
  - lab-control.callbill.ai serves /lab/v1/* from 127.0.0.1:8003; /readyz is loopback-only by design.
  - The E4C window and the BACKEND-READY/APP-PILOT decisions may still be pending when you start: plan around
    both outcomes (accepted / a fix loop), never assume them.

DELIVERABLES:
  1. A numbered plan (waves and lanes) with, per lane: owned paths (disjoint), the brief, oracles (tests, mutants,
     real-service suites on task-local keys), estimates (optimistic/likely/pessimistic + confidence + basis), and
     the rulings it needs.
  2. The ordered list of operator inputs/actions the plan depends on, each with what it unblocks.
  3. The register items you schedule, defer or close, by row number, with the reason.
  4. The first-external-testers acceptance definition (which gates, which cells, on which system).
  5. Open questions for the operator, each with your default if unanswered.
```

## Verification log

- 2026-10-01T07:12Z: written by the coordinator (the handoff lane was refused by the sandbox classifier); facts cross-checked against 25-state, the audit, the register and the session record; the tenant uuid deliberately not reproduced here (it lives in ~/e4c/tenant2.user).
- 2026-10-01T09:35Z: wave 6 closed — merges #79, #81–#84 integrated in fast mode (tip 2d710800); the wave-6 row and the W7 remainder updated.
- 2026-10-01T18:23Z: status addendum in §3 (members done; the E4C window running); every W6 lane branch confirmed an ancestor of main (ce9a873c).
