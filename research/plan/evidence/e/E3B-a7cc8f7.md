# E3B phase 1 — backend-only durability, security and protocol integration gate

## Task and status

- **Task:** E3B phase 1 (track E), [18 §E3B](../../18-marlin-backend-first.md) slices a/b/c as
  scoped by `.claude/handoff/wave3/E3B.md`: (1) the first whole-tree Docker layer 2 run since
  M2/F2R-B/E1B/I0/F2P/Q2/E2R merged, (2) the `tests/integration/backend/` skeleton, (3) the
  crash-boundary drill list, (4) intentional-defect detection, (5) the merged-tree `make check`.
- **Status: implemented. The gate is NOT passed.** `run.py --layer 3` exits **3 or 1, never 0**,
  on this base: every journey and every PostgreSQL-backed crash drill is PENDING with the task
  that unblocks it. The runnable part ran against real local services (E2's PostgreSQL/Valkey/
  MinIO stack plus a pinned PostgREST) and against the merged fakes; fake-only results are
  labelled as such and are not integration.
- **Oracles touched:** BACKEND-JOURNEY (pending), DUR-ADMIT, DUR-CAP, DUR-FENCE, DUR-OUTPUT,
  DUR-SETTLE, DUR-OUTBOX, DUR-RLS, MEDIA-SEC, API-MODES, API-STREAM, API-OPS (pending, G6B),
  CREDIT-SPEND.
- Nothing deployed, no hosted Supabase, no AWS, no pilot host, no GPU, no paid provider.
- **Owner/session:** Claude Opus 5.5 implementation session, worktree
  `.claude/worktrees/codex-e3b`.

## Source

| Field | Value |
|---|---|
| Base SHA | `9414e8e196b9a8963b04a89d555f58a4f4dc77b9` (`claude/backend-impl`) |
| Implementation SHA | **`a7cc8f7`** |
| Branch / worktree | `codex/e3b-backend-gate` in `.claude/worktrees/codex-e3b` |
| Commits | `a4f80fe` WIP skeleton (journeys, drills, schema backstops, PostgREST profile) · `e501c38` layer-3 backend stage + defect mutants e3bm01–09 · `f67a62f` PostgREST stopped at the end of the backend stage · `a7cc8f7` E2's teardown line kept intact (e2m36) · this report in its own commit |
| Integrated SHA | none (coordinator) |

## What the backend gate is

`tests/integration/run.py --layer 3` = everything `make integration` runs (preflight, services,
migrate, rls, engine, suites, mutants, canary, teardown) **plus** a `backend` stage right after
`rls`:

1. `backend/compose.yaml` starts **PostgREST 13.0.4** (digest-pinned) in its own project
   `infrx-e3b`, on E2's network, host port 55530 on loopback, ownership by the checkout label
   `ai.infrx.e3b.checkout` (a foreign one is refused, never touched). The queue mode is E2's
   Valkey through Q2's `ValkeyScheduler`, object storage is E2's MinIO, PostgreSQL is E2's with
   migrations 0001–0005.
2. `pytest tests/integration/backend --junitxml` runs; `run.classify` reads the XML per case.
   A skip is **pending** only when it says `PENDING[<ids>]`, and every id must be in the closed
   vocabulary `backend/stack.PENDING` (a typo fails). Verdict: FAIL if anything failed, a plain
   skip happened (a case that should have run did not), nothing ran or pytest exited non-zero;
   else PENDING if any case is pending; else PASS.
3. PostgREST is removed at the end of the stage (its pool otherwise blocks the dirtying
   mutant's `DROP DATABASE`, measured in run 3 below) and again, defensively, before E2's
   teardown.

The default `make integration` (layers 1+2) is unchanged in behaviour: the backend cases are
collected by its `suites` stage too, where the PostgREST ones skip naming `--layer 3`.

## Requirement coverage

### E3B.a — journeys and provisioning (`backend/test_journey.py`)

| Case | Invariant | Status today |
|---|---|---|
| `test_backend_journey[{text,video_url,video_upload}-{sync,sse,async}]` (9) | Two tenants call the metered endpoint per input × mode; accepted identity, output replay, rate pins and exact usage reconcile; foreign handle 404; nothing leaks. First probes `stack.ingress_is_mounted()`; the day the pilot ingress is mounted the case **fails** ("write the body") instead of passing on an empty body | **PENDING** — ids per cell in the table below |
| `test_backend_journey__dataset_client_resume` | Resume a bounded dataset client: no duplicate accepted items or charges | PENDING[G1R,G3,D2,D5,G6B] |
| `test_two_tenants_are_provisioned_with_their_own_resolved_wallets_and_pins` | The fixture: distinct users/orgs/keys; each wallet RESOLVED from the credential (R66) and the other tenant's wallet `Forbidden`; both pin the same published deployment/rate card; balance granted through the store's `grant` hook (no wallet edit). **G6B call site** is `stack.provision_two_tenants`, today on the v2 fakes, `provisioned_by = "v2-fakes (G6B pending)"` | runs (fake) |
| `test_an_unknown_pending_id_is_refused` | The pending vocabulary is closed | runs |
| `test_postgrest_refuses_anon_on_the_tenant_tables` | Through real HTTP: `anon` on `/organizations` is 401/403 with SQLSTATE `42501`, not an empty 200 (R59-4) | runs (real PostgREST) |
| `test_postgrest_service_role_reads_every_tenant` | The service-role path sees every tenant, i.e. tenant safety on it is route-side (E2R Limits 4) | runs (real PostgREST) |
| `test_postgrest_member_session_sees_its_own_organization` | A member JWT reads its own org | **PENDING[I2B]** — observed `200`, rows `[]` (see Findings 2) |

Journey cells and their unblocking ids (every cell: G1R, G6B, D2, D5, W3):

| input \ mode | sync (+G2) | SSE (+G2, D4) | async (+G3, Q3) |
|---|---|---|---|
| text | G1R,G6B,D2,D5,W3,G2 | …,G2,D4 | …,G3,Q3 |
| video by URL | same as text (M2's fetch/probe/persist is merged) | same | same |
| video by upload (+M3, G4U) | …,G2,M3,G4U | …,G2,D4,M3,G4U | …,G3,Q3,M3,G4U |

### E3B.b — crash-boundary drills (`backend/test_drills.py`, `backend/test_schema.py`)

Drills 01–10 are written against the ports and parametrised `[fake]` (runs) / `[postgres]`
(PENDING on the D task that implements the RPC the drill crashes; the RPCs are
`infrx.unimplemented` stubs on this base). Every money-moving drill ends in
`assert_conserved`: per tenant, `ledger == granted − Σ settled debits`, `reserved == Σ holds
still reserved (non-terminal or held_unknown)`, `available ≥ 0`, all read through ports.

| Drill | Boundary / oracle | Exact invariant | `[fake]` | real store |
|---|---|---|---|---|
| `dr01` | acceptance, crash after commit before ack — DUR-ADMIT | idempotent retry = same accepted job (`replayed`), one job, one hold = `maximum_hold`, exactly one `prepare_dispatch` | runs | PENDING[D2] |
| `dr02` | refused admission — DUR-ADMIT/DUR-CAP/API-AUTH | revoked key, unpriced model, full key each refuse typed and leave no job/hold/dispatch/journal reservation | runs | PENDING[D2] |
| `dr03` | preparation worker lost — DUR-FENCE | reaper re-dispatches prepare; dead lease's `prepared` → `StaleLease`; queued once, one inference dispatch | runs | PENDING[D3] |
| `dr04` | dispatch: claim committed, answer lost — DUR-FENCE/DUR-OUTBOX | one requeue event (attempt 1), next claim is generation 2, two inference dispatches total | runs | PENDING[D3] |
| `dr05` | **stale append** — DUR-FENCE | same worker id, generation 1 after requeue cannot append; only generation 2's chunks readable | runs | PENDING[D3,D4] |
| `dr06` | output: lost after first committed chunk — DUR-OUTPUT | no regeneration, `lost_after_publication`, `held_unknown`, debit 0, late append refused | runs | PENDING[D4,D5] |
| `dr07` | **duplicate settlement** / lost terminal ack — DUR-SETTLE/CREDIT-SPEND | identical retry replays; different proposal `AlreadyTerminal`; late cancel returns the same outcome; one usage projection; one debit | runs | PENDING[D5] |
| `dr08` | **foreign result access** | beta gets `not_found` from status, replay and cancel of alpha's handle; alpha's job untouched | runs | PENDING[D4,G3] |
| `dr09` | cancel vs late completion — DUR-SETTLE/API-MODES | cancel wins, completion fenced, debit 0, hold released | runs | PENDING[D3,D5] |
| `dr10` | journal backpressure — API-STREAM/DUR-OUTPUT | oversized event → `journal_write_failed`, nothing (no prefix) stored | runs | PENDING[D4] |
| `dr11` | client disconnect mid-stream — API-MODES/API-STREAM | route behaviour | — | PENDING[G1R,G2] |
| `dr12` | saturation of the queue index — DUR-CAP | **real Valkey 8.1.10**, Q2 adapter: past `max_items` → `capacity_exhausted`, `retry_after_s == 1`, snapshot unchanged | — | runs |
| `dr13` | **queue rebuild after index loss** — DUR-OUTBOX | **real Valkey**: 4 accepted jobs queued, 1 run to completion, Valkey **SIGKILLed** (depth 0 asserted after restart), rebuild from the durable snapshot → the 3 queued jobs dispatch exactly once, the finished one never; conservation. Snapshot read from the fake JobStore (Q3 pending) | — | runs |
| `dr14` | malformed media — MEDIA-SEC | **M2's real probe**: garbage, truncated ISO, oversized EBML integer → `unsupported_media`, never an unhandled exception | — | runs |
| `dr15` | internal URLs — MEDIA-SEC | **M2's real fetcher**: loopback (E2's PG port), link-local metadata, IPv4-mapped IPv6 loopback, `localhost` refused; the transport saw **no request** | — | runs |
| `dr16` | **forced legacy fallback** (R51) | pilot with the shared gateway key refuses to start naming the setting, never its value | — | runs |
| `dr17` | **forced legacy fallback** (route) | in `pilot`, `/v1/chat/completions` must be the metered ingress | — | PENDING[G1R] — observed `infrx.gateway.routes.chat` (Findings 1) |
| `db01` | duplicate settlement, **real schema** — DUR-SETTLE/CREDIT-SPEND | on migrations 0001–0005 (E2's `infrx_e2`, D's fixture rows, rolled back): a settled debit and a re-settlement are refused, a second terminal event, a second usage debit and a second hold are refused, and the wallet total moved by exactly the one accepted debit (−0.00050000) and by nothing refused | — | runs |
| `db02` | foreign access, **real schema** — DUR-RLS | no cross-tenant `job_media` attach (composite FK), no cross-tenant outbox event (trigger), `anon`/`authenticated` read none of `infrx.{jobs,stream_chunks,credit_holds,outbox,job_media,idempotency}` | — | runs |

### E3B.c — intentional-defect detection

| Defect | Detected by | How |
|---|---|---|
| missing durable acceptance (no hold) | `e3bm01` → `dr01[fake]` fails | mutant on a copy of `infrx/contracts/fakes/state.py` |
| missing durable acceptance (no dispatch outbox) | `e3bm02` → `dr01[fake]` fails | mutant |
| stale append | `e3bm03` (generation fence removed) → `dr05[fake]` fails | mutant |
| duplicate settlement | `e3bm04` (cancel re-settles a terminal job) → `dr07[fake]` fails; `db03` (`jobs_guard` disabled) and `db04` (one-usage-debit index dropped) on the real schema | mutant + two live drills inside a rolled-back transaction |
| foreign result access | `e3bm05` (owner check dropped) → `dr08[fake]` fails; `db05` (`authenticated` granted `infrx.jobs`) | mutant + live drill |
| forced fallback to legacy unmetered ingress | `e3bm06` (`if missing or forbidden` → `if missing`) → `dr16` fails | mutant on a copy of `infrx/config.py` |
| the stage itself | `e3bm07` (pending counted as pass), `e3bm08` (plain skip ignored), `e3bm09` (plain skip counted as pending) → `test_stage.py` cases fail | mutants on a copy of `run.py` |
| control | `e3bc01` (comment in the fake store) must survive all `[fake]` drills | survived |

Mutant mechanics: `tests/integration/mutants.py` gained `API_TREE`; a mutant whose path is
under `apps/infrx-api/infrx/` copies that tree into the mutant's temp directory and points
`PYTHONPATH` at the copy (`backend/stack.py` only adds the checkout's `infrx` when nothing
else provides it). Every kill is `1 failed` on the named selector with no error (R40).

## Environment

| Item | Value |
|---|---|
| Host | `Linux 7.0.0-1010-aws x86_64`, 16 cores, local development box; no cloud resource touched |
| Python | 3.12.3 (`make api-env`: `uv sync --frozen`) |
| Node / pnpm | v22.23.1 / 9.15.9 (`apps/app`: `pnpm install --frozen-lockfile`, no tracked change) |
| Docker / Compose | 29.6.2 / 5.3.1 |
| PostgreSQL | `supabase/postgres@sha256:7768d0d1…` → `PostgreSQL 17.6` (E2 stack) |
| Valkey | `valkey/valkey@sha256:d2e18f34…` → `8.1.10` |
| ClickHouse | `clickhouse/clickhouse-server@sha256:87e0a5b7…` → `25.8.33.6` |
| S3-compatible | `quay.io/minio/minio@sha256:14cea493…` → MinIO |
| PostgREST (new) | `postgrest/postgrest@sha256:a312f4b2e48530a01fc26f5310d547d6c26d087858360e164522e415723a7732` (v13.0.4) → server header `postgrest/13.0.4` |
| Namespaces | E2: `infrx-e2-*`, 55500–55599, `infrx_e2`, Valkey `infrx_e2:{e3b-<uuid>}` for the drills (removed after each). E3B: `infrx-e3b-postgrest` on 127.0.0.1:55530 |
| Credentials | fixed local literals only (`infrx-e2-local`, a local JWT secret in `backend/compose.yaml`); classification **local only** |
| Seed | 20260921 |

## Commands and results

All UTC 2026-09-22 on the host above. Env var names only.

| # | Command | Exit | Result |
|---|---|---|---|
| 1 | `make api-env`; `cd apps/app && pnpm install --frozen-lockfile` | 0, 0 | venv created; `Done in 1.5s` |
| 2 | **deliverable 1:** `make integration INTEGRATION_ARGS="--canary --report <scratch>/l2-base.json"` at base `9414e8e` (18:47:56Z, 424.7 s) | **1** | preflight/services/migrate PASS; rls PASS `{"cases": 40, "failed": null}`; engine PASS 8; **suites FAIL**: `tests/integration` 92 passed, `make api-test` **69 failed, 2139 passed** (all 69 in `tests/d`), console 283/0, bench 67; mutants PASS `{"mutants": 73, "killed": 71, "controls_survived": 2, "not_killed": 0}`; canary PASS; teardown PASS. Cause of the 69, measured right after: `tests/d` raised `HarnessBusy: another run holds /tmp/infrx-d1-postgres-55432.lock (pid 828429 checkout …/codex-d1r)` — the D port was held by the D1R lane, the designed refusal, not a code defect |
| 3 | `run.py --layer 3 --canary` at `e501c38` (19:09:45Z) | 1 | backend PENDING (run 27, pending 23); then the mutants stage **crashed** in `_reprovision`: `There are 2 other sessions using the database` — PostgREST's pool on `infrx_e2`. Fixed in `f67a62f` (PostgREST stopped at the end of the backend stage) |
| 4 | `run.py --layer 3 --canary` at `f67a62f` (19:20:10Z, 449.7 s) | 1 | every stage PASS except backend **PENDING** and mutants FAIL on exactly one **stale** E2 mutant, `e2m36` (my edit had split the teardown line it targets); suites PASS: `tests/integration` 117 passed / 25 skipped, **`make api-test` 2208 passed**, console 283/0, bench 67; mutants 83 / 79 killed / 3 controls / problems `['e2m36']`. Fixed in `a7cc8f7`; `mutants.py --only e2m36` → killed |
| 5 | `run.py --layer 3 --canary` at `a7cc8f7` (19:28Z → 19:39:54Z, 420.6 s) | 1 | backend **PENDING** (27 run / 23 pending / 0 failed / 0 not-run); mutants **`{"mutants": 83, "killed": 80, "controls_survived": 3, "not_killed": 0, "pending": 0, "problems": null}`**; canary PASS; teardown PASS (`infrx-e3b-postgrest` then the four `infrx-e2-*`, `still_named_ours_but_not_ours: []`); **suites FAIL** again only on `make api-test` **69 failed, 2139 passed** in `tests/d` — the D port was again held by `codex-d1r` (lock holder read with `fuser` at 19:39:54Z: pid 1866028, cwd `…/codex-d1r/apps/infrx-api`) |
| 6 | `make integration INTEGRATION_ARGS="--layer 1 --canary"` at `a7cc8f7` (19:50:47Z → 19:56:12Z) | 2 (run exit 1) | preflight skip (layer 1); engine PASS 8; mutants PASS `{"mutants": 64, "killed": 62, "controls_survived": 2, "not_killed": 0, "pending": 0, "problems": null}` (all layer-1 mutants incl. e3bc01/e3bm01–09); canary PASS; **suites FAIL** only on `make api-test` 69 failed / 2139 passed — `tests/d` `HarnessBusy` again (codex-d1r); `tests/integration` 88 passed / 54 skipped (no stack: every layer-2 and backend case skips naming the command) |
| 7 | **`make -k check`** at `3ee3826` (= `a7cc8f7` + this report's WIP; no code difference) (20:01:21Z → 20:26:26Z) | **2** | `api-test` **69 failed, 2139 passed** — all 69 in `tests/d`, every one `HarnessBusy` (codex-d1r held 55432; 456 `HarnessBusy` lines in the log); `api-mutants` **83 failed, 1228 passed** — all 83 in `tests/d/test_migration_mutants.py`, same cause; `console-test` `# tests 283 / # pass 283 / # fail 0 / # skipped 0`; `console-lint` `✖ 2 problems (0 errors, 2 warnings)` (pre-existing, `apps/app/lib/contracts/`); `console-typecheck` `✓ Types generated successfully`, no diagnostic; `console-mutants` contracts `160 mutants: 160 killed … 0 survived, 0 stale, 0 runner errors`, V `40/40`, U `2 self-checks … 64 mutants, 64 killed, 0 not killed`, C `4 self-tests, 0 failed` + `104 mutants: 104 killed …`, v2 `5/5 self-tests` + `23/23`; `bench-test` **67 passed**. **Skips: none** — no `skipped` in either pytest summary line and `# skipped 0` |
| 8 | the D-port share of `make check`, when the port was free: `cd apps/infrx-api && INFRX_MUTANTS=all .venv/bin/python -m pytest -q tests/d` (20:27:26Z), then `… -q tests/d/test_migration_mutants.py` (20:28:43Z) | 0, 0 | **`109 passed in 76.20s`**; **`84 passed in 67.96s`** (first attempt each, no `HarnessBusy`) |
| 9 | **the gate:** `run.py --layer 3 --canary` at `3ee3826` (20:29:51Z → 20:38:06Z, 494.2 s) | **3** | every stage PASS except **backend PENDING** — the stage table below |
| 10 | `mutants.py --only e3bc01 … e3bm09` individually (at `e501c38`, stack kept) | 0 | e3bc01 SURVIVED; e3bm01–09 killed (each `1 failed, N deselected`) |

### The merged-tree layer-2 stage table (deliverable 1)

The first whole-tree run (command 2, base `9414e8e`, `exit 1`):

| Stage | Status | Measured |
|---|---|---|
| preflight | PASS | docker 29.6.2, four digest-pinned images present, nothing foreign |
| services | PASS | PostgreSQL 17.6, Valkey 8.1.10, ClickHouse 25.8.33.6, MinIO; `infrx_e2` template copy, 1 attempt |
| migrate | PASS | 0001–0005 by sha256 (unchanged from E2R's table), clock probe moved 3600.0 s / rest 0.0 / rolled-back 1800.0 → 0.0 |
| rls | PASS | 40 cases, `failed: null`, roles anon/authenticated/postgres/service_role |
| engine | PASS | `run_engine_conformance` over HTTP, 8 cases |
| suites | **FAIL** | `tests/integration` 92 passed; `make api-test` 69 failed / 2139 passed (**`tests/d` refused by `HarnessBusy`, D1R held 55432**); `make console-test` 283 / 0; `make bench-test` 67 |
| mutants | PASS | 73 / 71 killed / 2 controls / 0 not killed |
| canary | PASS | python exit 1 `4 failed, 3 passed`, named; console exit 1 `# fail 1`, named |
| teardown | PASS | four containers removed, nothing foreign |

No red stage belongs to a module lane: the only red is harness contention on the one D port
(08 §8, E2R Limits 2). Run 4 is the same tree plus this task's files and shows `make api-test`
**2208 passed** when the port was free.

### The gate: `run.py --layer 3 --canary`, command 9, exit 3

| Stage | Status | Measured |
|---|---|---|
| preflight | PASS | as above, nothing foreign |
| services | PASS | PostgreSQL 17.6, Valkey 8.1.10, ClickHouse 25.8.33.6, MinIO |
| migrate | PASS | 0001–0005 by sha256, clock probe as above |
| rls | PASS | 40 cases, `failed: null` |
| backend | **PENDING** | PostgREST `postgrest/13.0.4`; **run 27, pending 23, failed 0, not run 0**; detected `db03`, `db04`, `db05`; pending by id `D2 12 · D3 4 · D4 7 · D5 13 · G1R 12 · G2 7 · G3 5 · G4U 3 · G6B 10 · I2B 1 · M3 3 · Q3 3 · W3 9` |
| backend-teardown | PASS | `infrx-e3b-postgrest` removed (end of stage), nothing left at the end of the run |
| engine | PASS | 8 cases over HTTP |
| suites | PASS | `tests/integration` 117 passed / 25 skipped (the skips are all in `tests/integration/backend`: its pending cases and the PostgREST cases, whose service the backend stage has already removed); **`make api-test` 2208 passed**; `make console-test` 283 / 0; `make bench-test` 67 |
| mutants | PASS | `{"mutants": 83, "killed": 80, "controls_survived": 3, "not_killed": 0, "pending": 0, "problems": null}` |
| canary | PASS | python exit 1 `4 failed, 3 passed`, named; console exit 1 `# fail 1`, named |
| teardown | PASS | the four `infrx-e2-*` removed, `still_named_ours_but_not_ours: []` |

### Backend stage (runs 3–5 and 9, identical case outcomes)

`{"run": 27, "pending": 23, "failed": null, "not_run": null}`, detected (live drills)
`db03`, `db04`, `db05`; pending by unblocking id (a case counts once per id it names):
`D2 12 · D3 4 · D4 7 · D5 13 · G1R 12 · G2 7 · G3 5 · G4U 3 · G6B 10 · I2B 1 · M3 3 · Q3 3 · W3 9`.

The 27 run: `dr01`–`dr10 [fake]`, `dr12`, `dr13` (real Valkey), `dr14`, `dr15` (real M2),
`dr16`, the provisioning fixture, the vocabulary guard, 2 PostgREST cases, `db01`–`db05`
(real PostgreSQL), 3 stage cases. The 23 pending, each with its ids: the 9 journey cells and
the dataset resume (table above), `dr01`–`dr10 [postgres]` (D ids as tabled), `dr11`
(G1R,G2), `dr17` (G1R), the PostgREST member session (I2B).

## Failure drill

| Injection | Durable state before → after | Verdict |
|---|---|---|
| SIGKILL of `infrx-e2-valkey` (dr13) | 3 queued + 1 terminal accepted jobs; index depth 0 after restart (asserted); rebuild returns 3; the three dispatch exactly once, the terminal one never; ledger/reserved conserved | recovered, no accepted job lost, no duplicate executable attempt (index half only; the PostgreSQL snapshot is Q3) |
| crash after commit on `admit`, `claim`, `complete` (dr01/04/07, fake) | one job/hold/dispatch; one requeue at generation 2; one settlement and one usage projection | idempotent, conserved |
| `jobs_guard` disabled / one-usage index dropped / `authenticated` granted `infrx.jobs` (db03–05, real PG, inside the fixture transaction) | `rewrite a settled debit`, `a second usage debit`, `authenticated read infrx.jobs` each reported by the backstop function | DETECTED; the transaction is rolled back, nothing committed |
| the six source defects + three stage defects (e3bm01–09) | temp copies only | all killed by the named case; control e3bc01 survived |

Cleanup: every Valkey key of the drills' namespace is deleted by the `valkey_index` fixture
(E2's own `test_valkey_accepts_namespaced_keys…` asserts the shared prefix is empty and
failed once before that fixture existed); every DB drill runs in one transaction that is
rolled back; `infrx-e3b-postgrest` is removed by the stage and by the run's teardown.

## Artifacts

Raw outputs in the session scratch area (not committed): `<scratch>/e3b/l2-base.{log,json}`
(run 2), `l3-e501c38.log` (run 3; no JSON, the run crashed), `l3-f67a62f.{log,json}` (run 4),
`final-a7cc8f7.log` + `l3-a7cc8f7.json` + `l1-a7cc8f7.json` (runs 5, 6), `check-3ee3826.log`
+ `final2.log` + `d-1.log` (commands 7, 8), `l3-3ee3826-r1.{json,log}` (command 9, the gate).

Owned-file sha256 at `a7cc8f7`:

```
6a1efedfd45a58c38a56683b6bdcea16535e0ef73a2cb01cf406772e9284fc7c  tests/integration/backend/compose.yaml
71745a925368770f89686668dcb95aa999770363162c65bcc4c61b2fe76404eb  tests/integration/backend/stack.py
38acb793c360bf8cb023c2373e1b7353cd5ff5ad6e7b7461c0ec34d9bd61bced  tests/integration/backend/test_drills.py
f3abe43723f57da4f5c9533045fb6d18fcc242b6c2c83adeec92c893680062b4  tests/integration/backend/test_journey.py
f8f65ccbc49817eb92c55f9128fd6aee8d97937a2018d68303cb0e73d6512269  tests/integration/backend/test_schema.py
e5e6d8f865f510faabaca9e30bdf16ce140593b6089727dfce5d90270ba6977b  tests/integration/backend/test_stage.py
17124c556e9cd630fd7670ecc756655f09607f8736f5ad6c3d8e968e09428fe8  tests/integration/run.py
c589c1951fbd18ddf05b3a03006051eda3247fe0c4a543d00e489ca73fdda1ca  tests/integration/mutants.py
```

## Changes

`git diff --stat 9414e8e..a7cc8f7`: 8 files, 1346 insertions, 6 deletions.

```
tests/integration/backend/{compose.yaml,stack.py,test_journey.py,test_drills.py,
                           test_schema.py,test_stage.py}        (new)
tests/integration/run.py      (additive: --layer 3, backend stage, classify/verdict,
                               backend teardown; E2's stages and lines kept - e2m36 re-killed)
tests/integration/mutants.py  (additive: API_TREE copy for module-code mutants, e3bc01, e3bm01-09)
research/plan/evidence/e/E3B-a7cc8f7.md                        (this report)
```

`mutants.py` is not in the brief's owned-path list; it is E's own file and the defect list of
deliverable 4 lives there, so the additions are declared here for the coordinator. No module
code, contract, config, SQL, composition root, Makefile, manifest or lockfile was touched. No
migration; no deploy/rollback implication. Rolling back = reverting the four commits.

## Findings for other lanes (integration requests)

1. **G1R / coordinator (composition root) — forced legacy ingress in pilot.** Failing
   (pending) case: `tests/integration/backend/test_drills.py::test_e3b_dr17_a_pilot_gateway_does_not_serve_chat_through_the_legacy_route`
   — `create_app` with `INFRX_MODE=pilot` and valid pilot settings starts and serves
   `/v1/chat/completions` from `infrx.gateway.routes.chat` (the legacy F1 route: no durable
   admission, no hold). `validate_runtime` accepts pilot while `app.ROUTERS` still mounts the
   legacy route. Request: at cutover mount `ingress` instead of `chat` in `ROUTERS`, and until
   then make `validate_runtime` refuse `pilot` when the metered ingress is not the mounted
   router, so a pilot configuration can never serve the unmetered path. When that lands, `dr17`
   stops being pending and every journey case fails until its body is written (by design).
2. **I2B / C (input, not a defect in the backend path).** `test_postgrest_member_session_sees_its_own_organization`:
   a member JWT (HS256, `role=authenticated`, `sub=<owner_alpha>`) through PostgREST 13.0.4 to
   the pinned `supabase/postgres` 17.6.1.173 returns **200 with `[]`** — `auth.uid()` reads only
   the legacy claim GUC, which PostgREST 13 does not set (E2R's measurement, now reproduced
   end-to-end over HTTP). The deployed PostgREST/auth pairing must be confirmed before any RLS
   claim about browser sessions.
3. **D / coordinator — one D port for many lanes.** Runs 2 and 5 lost `tests/d` (69 cases)
   to `HarnessBusy` while `codex-d1r` held 55432; run 4 on the same tests had 2208 passed. A
   whole-tree check needs a D-port-free window or a second allocated port (E2R Limits 2).
4. **W3 / I2B — nothing calls `JobStore.recover()`.** `dr03`, `dr04`, `dr06`, `dr13` depend on
   the reaper that re-dispatches lost preparation, requeues lost claims and fails lost
   publications; the drills call it explicitly. A timer/worker must own it (also W2 review).
5. **Q3 — the rebuild snapshot.** `dr13` builds the non-terminal snapshot from the fake
   JobStore; the PostgreSQL query + reconciler that produce it are Q3's (Q2 Limits).
6. **E (this lane) / coordinator — `mutants.py`'s mutation stage has no guard for a
   `HarnessError` inside `_reprovision`** (run 3 lost the whole JSON report to it). Pre-existing
   E2 behaviour; now avoided by stopping PostgREST, not fixed.
7. **F2P wire-in / D1R.** The provisioning fixture holds v2 identities (CREDIT wallets, pins)
   but grants and drills use the v1 fake JobStore's USD-shaped pilot numbers; no conversion is
   attempted (R64/R65). The drills switch to CREDIT when the v2 JobStore/wire-in lands.

## Limits

1. **Not integrated; the gate is not passed.** 23 of 50 backend cases are pending; the 10
   `[fake]` drills are fake-only (implemented). Real-store coverage today: Valkey (dr12, dr13),
   the migrated PostgreSQL schema (db01–db05), PostgREST (2 cases), M2's probe/fetcher, the
   composition root's startup refusal. No journey ran; no durable RPC was crashed.
2. **No GPU, no engine, no latency/throughput.** P-04 and W3; E1B/E4B.
3. **The DB drills assert the schema's backstops, not D2–D5's operations.** A correct
   operation that never tries a duplicate is not proven by them.
4. **`dr13`'s durable snapshot is the fake's**, so DUR-OUTBOX is proven for the index half.
5. **The PostgREST member case is environment-bound** (Findings 2): pending on I2B, not a
   failure of the backend path, which uses the service role.
6. **A layer-3 run cannot be fully green while any lane holds the D port**; commands 2, 5, 6
   and 7 lost `tests/d` to it, commands 4, 8 and 9 found a free window. `make check` is
   therefore evidenced as command 7 (every non-D target) plus command 8 (the D share), not as
   one green invocation.
8. **Object storage is up but unused by product code.** `infrx.media.store` has only
   `InMemoryObjectStore`; no S3-backed `ObjectStore` exists on this base, so no case exercises
   E2's MinIO beyond E2's own smoke test. The upload journey cells name M3/G4U.
7. `make check` does not run `tests/integration` (it is `make integration`'s job, E2R).

## Handback

- **Next unblocked:** E3B phase 2 as each id lands — G1R (unpends `dr17` and every journey
  case, which then fail until written), D2/D3/D4/D5 (swap `rig("postgres")` to the real adapter
  factory; the drills are already written against the ports), G6B (replace the body of
  `stack.provision_two_tenants`), Q3 (feed `dr13`'s snapshot from PostgreSQL), M3/G4U (upload
  cells), I2B (member session case).
- **Coordinator wiring:** none required for phase 1. `make integration` behaviour is unchanged;
  `run.py --layer 3` is the gate command (optional Makefile target
  `make backend-gate` = `run.py --layer 3 --canary`, not added: Makefile is coordinator-owned).

## Verification log

- 2026-09-22: Authored from the runs above; every count is quoted from command output or the
  JSON reports. Status **implemented, gate not passed**. Containers of other sessions seen on
  this host (`infrx-d1-postgres` of codex-d1r, `infrx-q2-valkey`,
  `gideon-migration-order-test-…`) were never touched; every `infrx-e2-*`/`infrx-e3b-*`
  resource this session created was removed.
- 2026-09-22 (same session, after the WIP evidence commit `3ee3826`): commands 6–9 added. The
  gate (command 9) exited **3** with every stage PASS except `backend` PENDING and
  `make api-test` 2208 passed in its `suites` stage; `make -k check` (command 7) passed every
  non-D target with no skips and lost only `tests/d` to the D1R lane's lock, and the D share ran
  green on its own (command 8). No code changed after `a7cc8f7`.
