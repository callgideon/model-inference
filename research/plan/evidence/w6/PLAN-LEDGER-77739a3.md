# W6 plan-ledger: evidence at 77739a3

The lane covers audit findings INT-01, INT-02, INT-03, INT-04, INT-06 (the README part only), INT-07, INT-10, INT-11 and the P-08 row. It ran in the order of its brief.

| | |
|---|---|
| Base | `08983639` |
| Head of the work | `77739a36` |
| Branch | `codex/w6-plan-ledger` |
| Worktree | `.claude/worktrees/codex-w6-plan-ledger` |
| Task-local key | none (plan files only; no docker) |

Nothing touched hosted Supabase, the pilot box, AWS, SSM, S3, Vercel or a secret. No migration file changed, no product code changed, and no manifest status changed. Commits on the branch: `60d57ce2`, `e67ec681`, `38b0dfd2`, `8835d5b4`, `1d38ec79`, `02cb863e`, `97ebb7d6`, `2aa91822`, `a623b1dc`, `a7f5d917`, `2f15ae2`, `77739a36`, then the evidence commit.

## The oracle: red first, then green

`research/plan/evidence/w6/PLAN-LEDGER-raw/oracle.py` makes one assertion for each brief item.

| When | Command | Exit | Result |
|---|---|---|---|
| Before any change, at base `08983639` | `python3 research/plan/evidence/w6/PLAN-LEDGER-raw/oracle.py` | 1 | **30 failing items** (`oracle-red-08983639.log`) |
| After P-08 (`a7f5d917`) | same | 0 | **0 failing items** (`oracle-green.log`) |

This lane writes no code, so there is no `test_mutants.py` of its own. Each "decision" here is a data value, and the oracle pins it. The one code change the lane needs is filed as WR-PL-1, with its own two cases.

## What was done, task by task

### INT-01: overlay header and the deployed block

- Header: `main` = 41693d5d…, `integration_head` = 08983639, and a new `base_note` saying that the base stays dff31efc.
- `deployed` is now one dated block. It replaces bda15866 / legacy_usd:
  - runtime 41693d5d, regime `credit`, config including `ACCOUNTING_REGIME=credit`;
  - `hosted_schema` 0001–0059 with both window digests, 88f9d412… (at 0056) and 9566fa25… (at 0059);
  - Lab 7ecbab0e, lab.callbill.ai on Vercel `infrx-lab`, and the lab-control edge;
  - `sources`: session-03 lines 525 and 589–595, and the 09 log.
- The E4C `next_action` is now the four remaining steps (members → main → E4C certify → one tester's checklist). This went through `updates/E4C-20261001T0300Z.json` and `apply-updates`.

### INT-03: the seven gate records

They are in the overlay under `gate_records` (E3L, E5L, E6L, E7L, E8L, E4-ON, COMPLETE-LOCAL). Each record has:

- the candidate SHA and its merge;
- cells: PASS, or NOT RUN / FAIL-by-design with a `class` and a `rerun`;
- the decision and when it was made;
- the rulings and the evidence paths.

They are copied from 09 and from the runs of record in `evidence/e/*` (`verdict.json`). The APP-LOCAL and APP-PILOT notes now carry the decisions as recorded: session-03 lines 442–443 and 435.

These records are in `gate_records`, not `gates`, which is the one deviation. It was measured on a scratch Model:

- `progress.py` accepts only manifest release-gate ids in `gates`, and E4-ON is not one, so it is a check error.
- It forecasts every undecided gate root. A COMPLETE-LOCAL entry turned BACKEND-LOCAL's accepted E3C milestone into "unknown: no remaining-effort estimate for E3B". It also added E3B and E3C to the E4C and E4 remaining paths.
- It gives every implemented Lab root a fake "forecast" date of now + 0.5 h.
- Its green rule needs every cell PASS, and R222 does not.

`gate_records_note` records this. WR-PL-1 renders the records.

### INT-04: closure notes

`tasks.json` has new `closure_note`s for E5L (290d70e9, R265), E6L (24a7a065 + j11 9938ad6c; j10 product WRs) and E8L (e65bbecf; k08 P-08). I2A and E3A get notes saying they are planned on purpose, with the decision deferred to E4C. Statuses are unchanged (an oracle checks this against the base). `validate_plan.py --write-ledger` exits 0 and the ledger is unchanged, because closure notes are not in it.

### INT-02: the 113 lane update files that were never ingested

**Dry run first.** `progress.apply_updates` ran on a deep copy of the live overlay (`int02-dryrun-scratch-copy.log`):

| Outcome | Count |
|---|---|
| rejected | 110 |
| → impossible transition queued → review | 80 |
| → malformed (confidence `medium-high`) | 5 |
| → unknown task ids, unknown activities, stale, wrong lane | the rest |
| applied | 3 |

Applying the 3 would have created **two phantom lanes** (`lab-sql-integration` blocked, `lab-improve-2` in review) that duplicate hand-merged W5 lanes. So apply-updates was not run on these files.

**Reconciliation** (`int02-reconcile.log`):

- Each of the 113 files maps, by `branch`, to exactly one overlay lane: 62 distinct lanes, all `complete`.
- Each maps to the first first-parent commit of 08983639 that contains its head: 59 distinct merges.
- One update head (E5L-20260930T0155Z, 290d70e9) sits on the first-parent chain itself. It uses the lane head's merge instead (f2ef6b2e, merge #67).
- `ingested[file]` = `{status: applied-by-hand, at, lane, merge, reason}`.
- 96 evidence paths that the hand merge had dropped are appended to their lanes. One path was not appended: `…/LAB-DEPLOY-PREP-24512389.md (Fix round section)` is an annotated path, not a file.
- 12 updates carry an `at` later than their lane's `updated` (the lane-clock skew of the 09 stamp correction). Each such reason says so.

**Then the single writer.** `progress.py apply-updates` applied only the lane's own update files (E4C, I2A, E3A, I3, and W5-EVAL-RUNNER's close). Its output is in `int02-apply-updates.log`.

### INT-07: the carried-work register

`research/plan/consumer-v1/10-carried-work-register.md` has 74 rows, in four parts:

1. Launch steps and windows (13 rows, including the six E4C inputs).
2. Lab product work (33 rows).
3. W6 cleanup items, by audit pending line (28 rows).
4. A cross-check against RESUME-NOW:6.

Every bullet of the five pending lists is cited as `audit:N`, and every WR, input and finding id in them appears in the register (oracle item INT-07). Two items block internal testing: `members`, through its prerequisite INFRA-01, and the tester checklist itself. WR-LDP-7 blocks eval, judge and datasets testing only.

### INT-06: the migrations README

`apps/app/supabase/migrations/README.md` is new (21 lines):

- the LOCAL-ONLY header is a merge-time marker;
- the state of record is the plan (session-03, 09, the overlay's `deployed.hosted_schema`);
- 0001–0059 are immutable;
- the procedure for 0060 onwards (R150, R151, R201, R264).

The README cannot change the plan digest, because every migration reader globs `[0-9][0-9][0-9][0-9]_*.sql` (`infrx/state/migrations.py:34`, `tests/integration/pgstate.py`, `tests/a/auth-surface.test.ts:157` by prefix). `git diff --quiet 08983639 -- 'apps/app/supabase/migrations/*.sql'` exits 0.

### INT-10: the COMPOSITION-7 sentence

`COMPOSITION-7-252b65e.md:119` now says that `assignments` is D9's per-(serving, pin) tally over terminal jobs (0058, WR-C7-TALLY). One log line is appended.

### INT-11: activated tasks, inputs and lane paths

- X1, X3 and X5 leave `activated`. No wave-map error results: they stay in LW1 *discovery*.
- Inputs P-08 (resolved: enacted), P-11 (open) and P-21 (resolved) are added. Each has `blocks: []`, so no forecast changes.
- Disjoint `owned_paths` for the window-bound lanes, each inside its task's manifest paths:
  - I2A: `apps/app/next.config.ts`, `apps/app/tests/i2a/`, `infra/app/`
  - E3A: `tests/integration/app/`, `app-e2e.sh`, `apps/app/tests/e2e/`
  - I3: `tests/integration/ops/`, `apps/app/tests/i3/`, `infra/runbooks/`, `infra/alerts/`
- W5-EVAL-RUNNER is set to `complete`, because B1 and B2 are implemented.
- A support lane `W6-PLAN-LEDGER` is added so that this handback ingests.

### P-08

`15-pending-inputs.md` has a new decisions-table row, **P-08 ENACTED 2026-09-29 – 2026-10-01** (session-03:524, 589–595). Membership onboarding is still the `members` step. One log line is appended.

## Checks

| Command | Head | Exit | Result |
|---|---|---|---|
| `python3 research/plan/scripts/progress.py check` | base 08983639 | 0 | 0 errors, **9 warnings**: 4 stale, 4 overlap, 113 unapplied |
| `python3 research/plan/scripts/progress.py check` | 77739a36 | 0 | **0 errors, 3 warnings** (below) |
| `python3 research/plan/scripts/progress.py apply-updates` (×2) | e67ec681..a623b1dc | 0 | applied 1, then 4; overlay rev 343 → 351 |
| `python3 research/plan/scripts/progress.py` (render) | every step | 0 | PROGRESS.md / progress.html regenerated; PROGRESS.md now shows lab.callbill.ai |
| `python3 research/plan/scripts/validate_plan.py --write-ledger` | 8835d5b4 | 0 | PASS, ledger current (unchanged) |
| `python3 research/plan/scripts/validate_plan.py` | a7f5d917 | 0 | PASS: 961 local Markdown links across 431 documents |
| `cd research/plan/scripts && python3 -m unittest test_progress test_validate_plan` | 77739a36 | 1 | 49 tests, **1 error, pre-existing**: `test_validate_plan.ConsumerClosureTests.test_app_cannot_dispatch_early` raises `KeyError: 'dispatch_after_gate'` (A2). The same error occurs at base 08983639 (`git archive` copy) |
| `cd research/plan/scripts && python3 -m unittest test_mutants` | 77739a36 | 0 | 4 OK |
| `make console-test` | 77739a36 | 0 | 650 tests: 591 pass, 0 fail, 59 skipped |
| `make console-lint` | 77739a36 | 0 | 0 errors, 2 warnings (pre-existing, LAB-14) |
| `make console-typecheck` | 77739a36 | 0 | typegen + tsc clean |

The 3 remaining warnings are stale estimates on lanes I2A, E3A and I3, estimated on 2026-09-26. They are window-bound: their remaining work runs in the E4C certify window. The lane has no new information to re-estimate them, and inventing an estimate would be worse than a stale one. The coordinator re-estimates them when the E4C window is scheduled.

No dedup was made, so there are no line-count pairs. The added files are the register (109 lines) and the migrations README (21 lines).

## Wiring requests

1. **WR-PL-1 → coordinator (owner of `research/plan/scripts/`).** Apply `research/plan/evidence/w6/PLAN-LEDGER-raw/WR-PL-1-progress-gate-records.patch` (sha256 `9b474149672fe7ed…`; `git apply --check -p1` is clean at 2f15ae2). It does three things:
   - `validate` errors on a gate-record cell with an unknown verdict, or a non-PASS cell without a class and a rerun;
   - the Verification section gets a "Local gate records (R222)" table in the HTML and in the MD;
   - two `GateRecords` cases.

   Proof, run on a scratch copy of the branch with the patch applied: `test_progress.GateRecords` 2/2 OK; `test_progress` OK; `test_mutants` 4 OK; `progress.py check` 0 errors; both outputs contain the table. The records are never forecast.
2. **WR-PL-2 → coordinator (`research/plan/consumer-v1/README.md`).**
   - After line 12, add two numbered index entries in the file's existing style: entry 9 links `09-path-to-internal-testing.md` ("Path to v1 internal testing"), and entry 10 links `10-carried-work-register.md` ("Carried-work register": one row per carried WR, input and open item, state 2026-10-01, with source, owner and whether it blocks internal testing).
   - Add the log line `- 2026-10-01: 10 added (W6 plan-ledger, INT-07): the carried-work register.`
3. **WR-PL-3 → `infra-libs` + coordinator (the rest of INT-06; comments only).**
   - `apps/infrx-api/tests/i/lab/test_lab_rollout_steps.py:416` (`infra-libs` owns it): change the docstring's `(hosted at 0051 since 2026-09-29: EXPECTED_PENDING 0052-0056, …)` to `(hosted at 0059 since 2026-09-30T08:01Z; the next window's EXPECTED_PENDING is 0060+, …)`.
   - `infra/rollout/hosted-migrate.sh:35` (`infra-libs` touches helper sourcing only): append `; applied 2026-09-30T08:01Z — the next window is 0060+ (a reviewed edit)` to the comment. The `EXPECTED_PENDING` value stays as it is (R151 condition 2).
4. **WR-PL-4 → coordinator.** Point `.claude/RESUME-NOW.md` (outside git) at `research/plan/consumer-v1/10-carried-work-register.md`. Also create overlay support lanes for the other 16 W6 lanes before their `W6-<LANE>` update files are applied; otherwise apply-updates rejects them as unknown task ids. `W6-PLAN-LEDGER` already exists.

## Open items

- `progress.py write_state` serializes with indent 2, while the committed overlay used indent 1 (the coordinator's hand edits). Each `apply-updates` therefore rewrites about 29,000 lines. This lane restored indent 1 after each apply, so its diff is content only. Pick one format; WR-PL-1 does not cover this.
- The pre-existing `test_app_cannot_dispatch_early` KeyError: the test deletes A2's `dispatch_after_gate`, which the 2026-09-25 user decision already removed.
- Nothing in the manifest is flipped. I2A and E3A stay planned on purpose (closure notes).

## Estimate (remaining, this lane)

- Hours: optimistic 0.3, likely 1, pessimistic 2.
- Confidence: medium.
- Basis: the brief's items are done and the oracle is green. What remains is one review or fix round, and the coordinator's merge of an overlay that other merges also touch (the `ingested` and `activity_log` keys are the conflict surface). Applying WR-PL-1 is about 0.3 h.

## Log

- 2026-10-01: written at 77739a36 by the W6 plan-ledger implementer.
