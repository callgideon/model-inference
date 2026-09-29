# LAB-APP-CONTROL: WR-E3L-J (the Lab's HTTP control port) + E3L-F3 (J01/J02 re-cut) + J01/J02 on the real L3 (`e2d6eb5`)

Lane lab-app-control (LW3), branch `codex/w5-lab-app-control`, worktree
`.claude/worktrees/codex-w5-lab-app-control`, base `adb84d11`. Code head of every check below:
`e2d6eb52`; this file and the update JSONs are the next commit. Tasklocal key `l4` (PostgreSQL
57503, `infrx-l4-postgres`, removed by pgharness at exit). Oracles: LAB-PUBLISH, LAB-ACCESS,
CONSOLE-FLOWS (L4's). Raw logs: `LAB-APP-CONTROL-raw-e2d6eb5/`.

**Result: J01/J02 GREEN on the real L3** (R186's factory over the real `PgControlStore`, through the
Lab's new HTTP adapter), with one step fake-only (J02's operator rejection: L3 has none, E3L-F4 below).

## Commits (one per step, none amended)

| Step | Commit | What |
|---|---|---|
| 1 | `22cabb27` | `control/http.ts` (HTTP `ControlPort` over `/lab/v1/control`), `control/server.ts` (`labControl(env)`), `controlPort()` = preview fake on its flag, else the adapter when `LAB_CONTROL_URL` is set, else `UNAVAILABLE`; `LAB_CONTROL_URL` declared in `infra/lab/app/lab.json` + README; L4-H01..H05; 31 mutants |
| 2 | `969f3a47` | E3L-F3: `FakeControl` follows L3; the view stops offering a provider rollback; J01/J02 re-cut into `tests/l/ui/journeys.ts` (one body, two worlds); `journey.test.ts` = the fake world |
| 2b | `87277cea` | step 2 went in with a `tsc` error in `journeys.ts` (a union-typed method index); fixed, not amended |
| 3 | `e2d6eb52` | `tests/l/ui/backend.py` + `stack.test.ts`: the same journeys through `httpControl` on the real control service |

## Step 1: WR-E3L-J, the HTTP adapter

- rollouts/http.ts's style exactly: the session's own access token (none, or a token read that
  throws: nothing is sent, `unavailable`), `?provider_org_id=<actor's>`, snake_case bodies,
  camelCase records (`camel` is imported from rollouts/http.ts, not copied), lists as `{data}`,
  401/403 `denied`, 404 `not_found`, 409 `conflict`, 422 `invalid`, anything else / transport /
  unparseable `unavailable`; one record the pages cannot read (closed enums for environment,
  visibility, state, smoke, proposal kind/state; nullable rate card / decided_at / p95) fails the
  whole answer closed. Smoke is `POST deployments/<encoded id>/smoke` with no body.
- `server.ts` `labControl(env)`: `LAB_CONTROL_URL` + `labConfig` (the session door), token =
  `sessionToken(config)` (evaluation/server.ts). The chooser lives in `control/port.ts` (there is
  no `lib/services/server.ts`; the brief's "server.ts" is the control package's own, as rollouts').
- Failing seam first: `node --test tests/l/ui/http.test.ts` before `http.ts` existed: exit 1,
  `ERR_MODULE_NOT_FOUND .../control/http.ts` (`raw/step1-red.log`).
- Cases L4-H01..H05 (`tests/l/ui/http.test.ts`); H05 is the wiring (module hooks for
  `next/headers`/`@supabase/ssr`, as tests/r/wiring.test.ts): the adapter only with
  `LAB_CONTROL_URL` and a Lab config, carrying the session's token; signed out → nothing sent; the
  preview stays on `LAB_CONTROL_PREVIEW=1` only.
- Mutants L4-X48..X78 (+ X16 re-anchored), each named case killed by assertion.

## Step 2: E3L-F3, the fake follows L3 (measured, not assumed)

Before re-cutting I probed the real L3 through the route on l4 (the step-3 backend, one-off
script) and found the three E3L-F3 points confirmed plus two more orderings the fake had wrong:

| # | Behaviour | real L3 (route) | old fake | fake now |
|---|---|---|---|---|
| F3.1 | provider `rollback` proposal | 422 `invalid` (dev or live prod revision) | accepted / `conflict` | `invalid` |
| F3.2 | register an unknown name / a digest outside the imported weights | 404 / 422 | registered | `not_found` / `invalid`; the registration is of the imported model (`modelId` = `acme/acme-7b`, not the form name) |
| F3.3 | repeated proposal | 201, same `proposal_id` (R214) | `conflict` | the open proposal |
| F3.4 (new) | the role vs the id | the role is judged in the caller's own workspace first: B's developer proposing on A's id, or A's viewer smoking an unknown id, is 403 `denied` | `not_found` first | role first, then own dev revision |
| F3.5 (new) | smoke on a prod revision | 404 `not_found` (`LabControl._dev`: only the provider's own dev revision) | `conflict` | `not_found` |

- Red first: with the operator doors added but the old rules, `journey.test.ts` failed 2/2 by
  assertion (J01: the approval published nothing under the imported model; J02: an unknown model
  registered `ok`) (`raw/step2-red.log`); V03 re-cut red against the old view
  (`raw/step2-view-red.log`).
- The view (`control/view.ts`) no longer offers `rollback` to a provider (V03 re-cut). `Action`
  keeps `"rollback"` so the deployments page (not owned) still typechecks; its `LABEL.rollback` and
  the heading "Publication and rollback requests" are now dead copy (wiring note below).
- The fake's operator side: `importModel(provider, modelId, weights)` (the operator's import and
  first priced public revision), `decide(id, approve)`, `rollback(provider, modelId)` (the listing
  returns to the previous prod revision), `discoverable(modelId)` → ids. The labelled preview
  starts with no imported model, so registering there answers `not_found`, as L3 would for a
  workspace without imported weights. `actions.test.ts` imports its two models first.
- J01/J02 are one body (`journeys.ts`) over a `JourneyWorld` (per-user port, the imported model's
  registration, operator approve / optional reject / rollback, App discovery). Nothing either world
  does not share (labels, ids, seed rows, counts) is asserted; App discovery (what a consumer's call
  is pinned to) is the listing's truth.

## Step 3: J01/J02 on the real L3

- `backend.py` (tests/r/backend.py's pattern): `INFRX_D_TASK=l4` only; fresh `<db>_labctl`, every
  migration, `tests/l/control/worlds.seed_pg` (NemoStation's Marlin listed at v1 over its imported
  weights; Other Lab; ADMIN/DEV/VIEWER/BOTH/DEV_B/CONSUMER_ONLY); then
  `infrx.lab.control.app.create_app()` itself from `INFRX_LAB_*` env (so
  `pilot.lab_operations` = `Operations(LabControl, PgControlStore)`, 0044's ControlReads, A3's
  registry/catalog, L2's `LabAccess(PgAccessStore)`). Stand-ins, none deciding an oracle: the
  session verifier (a bearer per user in place of GoTrue), the engine smoke (R203:
  `NoEngine.smoke` answers; WR-L3-2 still unwired), `/_test/approve` (the operator's
  `LabControl.approve` at a fresh card at the current listing version), `/_test/rollback`
  (`LabControl.rollback` to the previous listed revision), `/_test/discoverable` (`worlds.pin`:
  what admission pins for a consumer's call to the alias).
- **Command (exact):** `cd apps/lab && LAB_L4_REAL=1 INFRX_D_TASK=l4 node --test tests/l/ui/stack.test.ts`
  → exit 0, 4/4 (S01 = J01, S02 = J02, S03 a consumer-only session is 403 `denied` by the control
  service), 8 s (`raw/stack-final.log`). Without `LAB_L4_REAL` it skips visibly (the `pnpm test` glob).
- Red on the real route: attempt 1 failed S02 (`raw/stack-try1.log`): my first reject stand-in
  (the platform retiring the proposal) broke the provider's listings → **E3L-F4** below; the journey
  world then took `reject` as optional (the real world has none). Sensitivity check: with
  `http.ts`'s 409/422 mapping swapped, S01 and S02 both fail on the real route
  (`raw/stack-red-swapped-409-422.log`; file restored with `git checkout`).

## Checks (code head `e2d6eb52`)

| Command | Exit | Result |
|---|---|---|
| `make lab-test` | 0 | 254 tests: 244 pass, 0 fail, 10 skipped (the real-stack files, stack.test.ts among them) |
| `make lab-lint` | 0 | eslint clean |
| `make lab-typecheck` | 0 | `next typegen` + `tsc --noEmit` clean |
| `make lab-build` | 0 | built |
| `make lab-mutants` | 0 | every runner 0 survivors; **tests/l/ui: 21 cases all named, 85 mutants, 85 killed** (was 16 cases / 47) |
| `cd apps/lab && LAB_L4_REAL=1 INFRX_D_TASK=l4 node --test tests/l/ui/stack.test.ts` | 0 | 4/4 (J01/J02 on the real L3) |
| `cd apps/infrx-api && INFRX_D_TASK=l4 .venv/bin/python -m pytest -q tests/i/lab` | 0 | 19 passed (the declared-names check sees `LAB_CONTROL_URL` in lab.json and the README) |
| `INFRX_MUTANTS=all INFRX_D_TASK=l4 .venv/bin/python -m pytest -q tests/i/lab/test_mutants.py` | 0 | 28 passed (lab.json's anchors intact) |

No Python product code, migration, `tests/integration/**`, page, layout or other service was
changed. Changed paths: `apps/lab/lib/services/control/{http.ts,server.ts,port.ts,fake.ts,view.ts}`,
`apps/lab/tests/l/ui/{http.test.ts,journeys.ts,journey.test.ts,view.test.ts,actions.test.ts,run-mutants.mjs,backend.py,stack.test.ts}`,
`infra/lab/app/{lab.json,README.md}`, `research/plan/evidence/l/`, `research/plan/evidence/coordinator/updates/`.

Docker: only `infrx-l4-postgres` (created and removed by pgharness per run; none left). Foreign
leftovers seen and not touched: `infrx-d1-postgres`, `infrx-d2-postgres`, `infrx-d2-valkey`,
`infrx-t2f-{postgres,s3,clickhouse}`, `infrx-b3-postgres` (Created), `infrx-lab-c2-s3` (Exited),
`infrx-m5-s3`, `infrx-q3-valkey`, `gideon-migration-order-test-caae059890`, volumes
`infrx-e5l_{clickhouse,postgres,s3}-data`.

## Findings (for L3 / lab-sql; not this lane's code)

- **E3L-F4: L3 has no rejection of a publication proposal.** `Operations.proposals` reads
  `rejected` for a proposal neither `proposed_public` nor published, but no L3 operation gets it
  there, and the platform's retire (0007's service_role UPDATE, `worlds.retire`) of a
  `proposed_public` revision leaves a public+retired row that `DeploymentRevision` refuses: the
  provider's **proposals and deployments listings then 503** (`ValidationError`, reproduced
  in-process: `raw/e3l-f4-repro.log`); visibility is immutable (`deployment_revisions_guard`), so
  retire+private is refused too. J02's rejection step therefore runs on the fake only.
- **E3L-F5: the Lab's deployment records do not say what is listed.** A pending proposal's
  revision reads `prod · public · active` (smoke `passed`, no card) before any approval, and after
  an operator rollback the rolled-back revision still reads `prod · public · active` beside the
  restored one: only App discovery tells them apart. The pages therefore show "public" for an
  unapproved revision. Proposed: `Operations._deployment` maps `proposed_public` to a distinct state
  (or `visibility` from the alias's current listing), and the route's `Deployment` record gains it.

## Proposed rulings (unnumbered; R217–R221 are being numbered on #36/#37)

1. **(R221-to-be, E3L-F3) The Lab's control fake follows L3**: a rollback is never a provider
   proposal (`invalid`; the operator's listing decision); registration only of a workspace model
   with operator-imported weights, over one of its digests (`not_found` / `invalid`); a repeated
   proposal answers the open one (R214); the role is judged in the caller's own workspace before the
   id (`denied` confirms nothing); only the provider's own **dev** revision is smoked or proposed
   (else `not_found`). The Lab offers no provider rollback.
2. **J01/J02 are one body over two worlds** (`tests/l/ui/journeys.ts`): the fake (mutant suite) and
   the real control service (`stack.test.ts`, `LAB_L4_REAL=1`, key l4); App discovery is the
   listing's truth in both.

## Wiring requests

- **WR-LAC-1 (apps/lab/.env.example, not owned):** after the `LAB_RELEASES_API_URL=` line add
  `# Server-only: the Lab control service's base URL (/lab/v1/control); unset = the overview, models and deployments pages are unavailable.`
  and `LAB_CONTROL_URL=`. Proof: `tests/i/lab` (the declared-names check reads .env.example;
  the name is already declared in lab.json and the README).
- **WR-LAC-2 (`apps/lab/app/(provider)/deployments/page.tsx`, lab-app's):** drop `rollback` from
  `LABEL` (and `Action` in view.ts together) and retitle "Publication and rollback requests" →
  "Publication requests"; dead copy since the view offers no rollback. Proof: `make lab-typecheck`, L4-P01.
- **WR-LAC-3 (Makefile, none needed):** `lab-mutants` already runs `tests/l/ui/run-mutants.mjs`;
  `stack.test.ts` is outside the mutant suite (like tests/r's).
- **E3L-F4 / E3L-F5** to the L3 owner (Python, `infrx/lab/control/operations.py`) and lab-sql.

## Open issues

- J02's operator rejection is unexercised on the real L3 (E3L-F4).
- WR-L3-2 (the engine smoke adapter) is still a stand-in here (R203), as in E3L.
- No hosted enable: `LAB_CONTROL_URL` unset = unavailable, so nothing reachable from the launched
  App/API changes.

## Estimate (remaining for L4 / this lane)

- optimistic 0.5 h, likely 1 h, pessimistic 3 h; confidence medium-high.
- basis: code, fake and real journeys are green with 0 survivors; what remains is one verify round
  (47-234 min per session-03) and the two small wiring requests; E3L-F4/F5 are L3's, not counted.

Rulings: proposal 2 numbered R223 and proposal 1's two extra clauses (role judged in the caller's own workspace before the id; only the provider's own dev revision is smoked or proposed) added to R221 as a verification-log amendment in `research/plan/08-contracts-v1-encoding.md`, at the lab-app-control merge on `codex/w5-merge-38` (2026-09-29).
