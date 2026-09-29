# LAB-CONTROL-2: E3L-F4 (operator rejection) + E3L-F5 (the record says what is listed) (`c1aac90`)

Lane lab-control-2 (LW6), branch `codex/w5-lab-control-2`, worktree
`.claude/worktrees/codex-w5-lab-control-2`, base `97b3689b`. Task L3 (stays implemented), E3L
update. Code head: `c1aac903` (checks below at `d407b006` unless marked; `c1aac903` changes only
two tests/d checks, rerun at it). This file and the update JSON are the next commit. Keys: `l3` (PostgreSQL 57502) for tests/l, `dlab` (57500) for tests/d, `l4` (57503) for
the optional J02 stack step. Raw logs: `LAB-CONTROL-2-raw/`.

## Commits (one per step, none amended)

| Step | Commit | What |
|---|---|---|
| 1 | `ce9575ce` | E3L-F4: `LabControl.reject` over `0052_lab_control_reject.sql`, the operator door on the control route, `PgControlStore` + `FakeControl` in step, the terminal-row read |
| 2 | `d407b006` | E3L-F5: `Operations._deployment`'s visibility is the alias's current listing (R207) |
| 3a | `c1aac903` | the two 0052 checks take their own proposal (the publish race commits P2 or P3; `make api-test` caught it) |
| 3 | (this) | evidence + update JSON |

## Step 1: E3L-F4, an operator rejects a publication proposal

- **SQL, `0052_lab_control_reject.sql`** (additive: two functions and their grants, no table,
  constraint or earlier function touched; 0001-0051 unchanged; local-only):
  - `infrx.lab_control_reject {deployment_revision_id, actor, reason}`: row lock, absent is
    `not_found`, any state but `proposed_public` is `state_conflict` (never twice, never a listed
    or dev revision), a reason outside 1-500 characters (after trim) is `invalid_request`; then
    `proposed_public -> retired` (0007's guard allows exactly that move), audited in the same
    transaction as `lab_transition` before `{state: proposed_public}` after `{state: retired,
    reason}` under the actor (an unattributed one is refused by `lab_control_audit`). Listings
    are not touched; the dev source stays `ready_private`, so 0047 lets it be proposed again.
  - `infrx.lab_control_operator {user_id}` -> `{operator: profiles.is_operator}` (false for an
    unknown user): the control service's check that a Lab session is a platform operator's.
  - Doors, 0025's pattern: `revoke all ... from public, anon, authenticated`; `grant execute ...
    to service_role, infrx_lab_control` (the factory's own login already holds publish/rollback).
- **Why "retired" still 503'd, and the fix.** 0007 keeps `visibility` immutable, so a rejected
  (or platform-retired) proposal's row stays `public`+`retired`, which the frozen
  `DeploymentRevision` contract refuses. `PgControlStore` now builds every revision through one
  `_revision(row)` that reads a retired row back as the terminal private row it is (it serves
  and lists nothing): `deployment`, `provider_deployments` and every RPC answer. No contract,
  migration 0001-0051 or A3 code changed.
- **Service / adapters.** `ControlStore` gains `reject` and `operator`; `LabControl.reject(operator,
  id, *, reason)` passes the operator's principal. `Operations.operator(user_id)` ->
  `OperatorSession(principal="operator:<user_id>")` (0025's naming) or `Forbidden`;
  `Operations.reject(operator, proposal_id, reason)`: only one of the Lab's proposals (a
  `lab_propose` of the revision's provider) else `not_found`, then the rejected `Proposal`.
  `proposals` reads a rejected proposal's `decided_at` from its rejection event (none after a
  platform retire). `FakeControl` states the same rules (`operators`, `reject`).
- **Route (R186's factory module, `routes/lab_control.py`).** `POST
  /lab/v1/control/proposals/{id}/reject {reason}`: R175's session (`lab_auth.authenticate`), then
  `operations().operator(user)` BEFORE the body is read (403 `denied` for a provider
  administrator or a consumer, whatever the body), then `Rejection` (reason 1-500, else 422),
  then 404/409 from L3; 200 = the `rejected` Proposal record. No `provider_org_id`, no
  membership: the operator is not a provider member. Mounted with the other control routes,
  so it stays behind LAB_CONTROL (off) on the gateway and is served by the control unit.
- **Failing seam first** (`raw/step1-red.log`, before any implementation): the three new cases
  failed 5/6: `Operations` has no `operator` (fake, PG); the route answered 404 (no route);
  and on PG the platform retire reproduced E3L-F4 exactly - `ValidationError ... a public
  deployment is in [...], not retired` from the provider's listings.
- **tests/d (dlab).** `test_l3sql_control.py`: RPCS gains the two doors (browser sessions
  refused, `check_browser_roles_reach_nothing`); `check_an_operator_rejects_only_an_open_proposal`;
  `check_the_operator_door_is_the_profiles_bit_on_the_control_login` (the bit, and both doors on
  `infrx_lab_control` and service_role). `test_l3sql_units.py`:
  `test_reject__sends_the_operator_and_reason_and_reads_the_terminal_row_private`.
  `code_mutants_l3sql.py`: 7 SQL mutants of 0052 + 4 Python.
- One mutant was dropped as equivalent, recorded here: granting the 0052 doors to
  `authenticated` survives because browser roles hold no USAGE on schema `infrx` (0004), so the
  probe is refused 42501 either way; the grant list's browser exclusion is belt and braces. The
  first l3sql run (`raw/step1-l3sql-mutants.log`, 2 failed) also showed the login check raising
  instead of asserting; it now returns the SQLSTATE and asserts.

## Step 2: E3L-F5, the Lab's Deployment record says what is listed

- **Reading picked: visibility from the current listing (R207)**, not new states. Evidence: the
  Lab's own fake already models visibility as "listed" (approval flips the previous live
  revision private, the operator's rollback swaps them back); J01 asserts the discovered
  revision reads `prod/public/active` and E3L's l03/l04 assert the listed seed and the approved
  proposal read `public`; no journey or cell reads a pending or superseded revision as public.
  New state values would have changed the route's closed `Literal` and the Lab's closed enums
  (http.ts fails a whole answer closed on an unknown value): a cross-lane break for no gain.
- `Operations._deployment`: `visibility = "public"` iff the serving's alias's newest listing
  names the revision; otherwise `private`. `state` stays liveness (R189): a rolled-back revision
  is `active` (admitted jobs keep their pins), a rejected one `retired`.
- Red first (`raw/step2-red.log`): a pending proposal read `('public', 'active')` (fake and PG).
- New case `test_operations__a_revision_reads_public_only_while_it_is_the_listing`: pending ->
  private; approved -> public (seed private); operator rollback -> seed public, the rolled-back
  one private/active; each step's public revision is what App discovery pins.

## Checks (code head `d407b006`; `c1aac903` where marked)

| Command | Exit | Result |
|---|---|---|
| `cd apps/infrx-api && INFRX_D_TASK=l3 uv run --frozen pytest -q tests/l` | 0 | 95 passed, 2 skipped (the mutant list's PG subset in-process; L2's) (`raw/step2-tests-l.log`) |
| `INFRX_MUTANTS=all INFRX_D_TASK=l3 uv run --frozen pytest -q tests/l/control/test_mutants.py` | 0 | 117 passed: 83 fake + 29 PG mutants killed, list checks; every case named (`raw/step2-mutants.log`; step 1: 115, `raw/step1-mutants.log`) |
| `INFRX_D_TASK=dlab uv run --frozen pytest -q tests/d/test_code_mutants_l3sql.py tests/d/test_l3sql_{control,units,reads}.py tests/d/test_upgrade_lab.py` (at `c1aac903`) | 0 | 106 passed: l3sql mutants 77 (every SQL/READS/CODE mutant killed), control 14, reads 5, units 3, upgrade 7 (`raw/step3-dlab.log`; step 1: `raw/step1-dlab.log`) |
| `INFRX_D_TASK=l3 uv run --frozen pytest -q tests/d/test_l3sql_{control,units,reads}.py` (at `c1aac903`) | 0 | 22 passed (`raw/step3-l3sql-on-l3.log`); also green alone (`-k "operator or reject"`) and with P2 committed as the race winner (one-off script, both checks answered) |
| `.venv/bin/python -m pytest -q tests/integration/lab_operate/test_e3l_runner.py tests/integration/lab_operate/test_mutants.py` (repo root) | 0 | 17 passed, 1 skipped: E3L layer 1 and its list's anchors intact (`raw/step2-e3l-layer1.log`); no scenario edited |
| `.venv/bin/python -m pytest -q tests/integration/test_harness.py` | 1 | 46 passed, 1 failed: `test_nothing_in_this_directory_points_at_production` flags `backend/test_certify.py` and `ops/test_create_test_user.py`, both unchanged on the base (pre-existing, not this lane); the migration pin (0052 added) passes |
| `cd apps/infrx-api && INFRX_D_TASK=l3 uv run --frozen pytest -q` (`make api-test`, at `d407b006`) | 1 | 6675 passed, 9 failed, 177 skipped, 9 xfailed in 1:38:33 (`raw/api-test.log`). 2 failed = this lane's new 0052 checks depending on the publish race's winner: fixed in `c1aac903` and rerun green (rows above). 7 failed = `tests/d/test_outbox_relay.py[valkey]`, `HarnessBusy: another run holds /tmp/infrx-d2-valkey-55463.lock` (a foreign lock file from 2026-09-22; not touched): environment, not this diff. The i8 lock was not hit. |
| `cd apps/lab && LAB_L4_REAL=1 INFRX_D_TASK=l4 node --test tests/l/ui/stack.test.ts` in a scratch worktree = `62d5130c` (batch #38) + `ce9575ce` + `d407b006` + WR-LC2-2 (the Lab fake UNCHANGED) | 0 | 4/4: S01 (J01), S02 (J02 **with the operator's rejection through the real route**: rejected, nothing published, the proposal reads `rejected`), S03 (`raw/WR-LC2-2-stack-with-reject.log`). The first attempt found l4 held by lab-observe-2 (HarnessBusy, waited ~50 min for its release). The scratch worktree was removed. |

Docker: only `infrx-l3-postgres`, `infrx-dlab-postgres` and (one run) `infrx-l4-postgres`, each
created and removed by pgharness; none left. Foreign containers seen and not touched: at start
`infrx-e6l-*`, `infrx-e5l2-*`, `infrx-e3l-*`, `infrx-e8l-postgres`, `infrx-b3-postgres` (Created),
`infrx-lab-c2-s3` (Exited), `infrx-d1-postgres`, `infrx-t2f-{postgres,s3,clickhouse}`,
`infrx-d2-{postgres,valkey}`, `infrx-m5-s3`, `infrx-q3-valkey`,
`gideon-migration-order-test-caae059890`; at the end `infrx-e5lrv*`, `infrx-lab-on-*`,
`infrx-lab-c2-s3`, `infrx-m5-s3`, `infrx-q3-valkey`, the gideon one; volumes `infrx-e5lrv_*`.

## Proposed rulings (unnumbered; rulings run through R223)

1. **Operator rejection of a publication proposal (E3L-F4).** A proposal leaves
   `proposed_public` only by the operator's approval (listed, `active`), the operator's rejection
   (`retired`: 0052 `lab_control_reject`, audited `lab_transition` with the operator's 1-500
   character reason, listings untouched) or the platform's retirement. The provider reads it
   `rejected` (decided_at = the rejection's instant; none for a platform retire), and its dev
   source stays `ready_private` and may be proposed again (R205/R214). The door is `POST
   /lab/v1/control/proposals/{id}/reject {reason}` on the Lab session (R175) of a platform
   operator (`profiles.is_operator`, 0052 `lab_control_operator`), checked before the body;
   the audited actor is `operator:<user_id>`.
2. **A retired revision is read back private (E3L-F4).** 0007's visibility is immutable and the
   contract forbids public+retired, so the Lab's store reads a retired revision as the terminal
   private row it is; no Lab listing ever 503s on a retired revision.
3. **The control record's visibility is the listing's truth (E3L-F5, refines R189 with R207).**
   `Deployment.visibility` is `public` only while the revision is its alias's current catalog
   listing; a pending, rejected, retired, rolled-back or replaced revision and every dev
   revision read `private`. `state` stays liveness (`active` | `retired`): a superseded revision
   is `active` (admitted pins). No new state value; the Lab's closed enums are unchanged.

## Wiring requests

- **WR-LC2-1 (lab-app owner, `apps/lab/lib/services/control/fake.ts`, on `codex/w5-lab-app-control`
  62d5130c / batch #38): the fake follows ruling 3 and L3's proposal shape.** A proposal IS a
  new prod revision with the proposal's id, unlisted (`private`, unpriced) until approved;
  approval lists THAT revision (previous public -> private); rejection retires it; the
  operator's rollback only restores a revision that was listed (priced). Exact diff:
  `raw/WR-LC2-1-lab-fake.diff`. Proof: with it applied on 62d5130c, `node --test
  tests/l/ui/journey.test.ts tests/l/ui/view.test.ts tests/l/ui/pages.test.ts
  tests/l/ui/http.test.ts` = 16/16 (`raw/WR-LC2-1-lab-journeys.log`); the mutant anchors L4-X27,
  X28, X30, X84 in fake.ts are unchanged lines. `make lab-mutants` / `lab-typecheck` NOT RUN
  here (no Lab node_modules outside the Lab lane): the owner runs them.
- **WR-LC2-2 (lab-app owner, `apps/lab/tests/l/ui/stack.test.ts` + `backend.py`): J02's
  rejection on the real L3.** The stack world's `reject` posts to the new route with an
  operator's session; backend.py adds `"ops": w.OPS_USER` (seeded by `tests/l/control/worlds.
  seed_pg` with `is_operator`) to its session users. Exact diff: `raw/WR-LC2-2-lab-stack.diff`.
  Proof: with it (and nothing else from the Lab changed), J02's rejection runs on the real L3,
  4/4 (`raw/WR-LC2-2-stack-with-reject.log`).
- **Makefile: none.** `tests/l/control/test_mutants.py` and `tests/d/test_code_mutants_l3sql.py`
  already run in `make api-mutants`/`api-test`.
- Coordinator: 0052 needs no hosted action (local-only, R151); `test_upgrade_lab` covers it
  (additive functions, re-runnable).

## Open issues

- Other readers that build a `DeploymentRevision` from a raw row (A3's `PgRegistry` idempotency
  read of an existing id, `PgCatalogDirectory` beyond resolution) would still refuse a
  public+retired row; none reads a rejected proposal on a known path (resolution filters
  `active`), but ruling 2 is the Lab store's alone. A3/lab-sql may want the same projection.
- E3L layer 2 (the scenarios on the running stack, `make lab-operate`, key `e3l`) is NOT RUN
  here (not this lane's key); no scenario expectation changes: l03/l04 read the listed
  revisions as public, which ruling 3 keeps. Rerun: `make lab-operate`.
- No Lab page offers the operator's rejection (the operator is outside the Lab UI); the door is
  for an operator console or the runbook (I2L).

## Estimate (remaining for this lane)

- optimistic 0.5 h, likely 1 h, pessimistic 3 h; confidence medium-high (this lane's code is
  done; remaining = one verify round + the Lab owner's WR-LC2-1/2).
- basis: both findings implemented with red-first seams, 0 survivors across the L3 (117) and
  l3sql (77) lists, E3L layer 1 green; what remains is one verify round (47-234 min per
  session-03) and the lab-app owner applying WR-LC2-1/2.

Rulings: numbered R231 (1), R232 (2), R233 (3) in `research/plan/08-contracts-v1-encoding.md` §10 at the lab-control-2 merge on `codex/w5-merge-43` (2026-09-29); R226–R230 numbered on codex/w5-merge-42.
