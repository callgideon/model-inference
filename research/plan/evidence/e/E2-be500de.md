# E2 — Pinned integration services and fault harness

| Field | Value |
|---|---|
| Task and status | **E2**, track E (verification). **implemented** — every deliverable exists and ran against real local services; nothing is deployed, nothing live-verified, and the cases that need another track's code are listed as pending, never passed |
| Source | base SHA `8744418`, implementation SHA **`be500de`**, branch `codex/e2-integration-harness`, worktree `.claude/worktrees/codex-e2`; integration target `claude/infrx-impl` |
| Oracles | **F-CONTRACT** (the exported engine conformance suite against a real HTTP adapter) and **DUR-RLS** (35-case SQL role matrix on a Supabase-compatible PostgreSQL with the console migrations applied) |
| Commits | `8c86999` harness + fake vLLM + guards, `f1e7af3` layer-2 suite + console discovery + mutants + `secret_grams` fix, `be500de` three defects the harness found in itself |

## The one command

```bash
make api-env                                                   # once
apps/infrx-api/.venv/bin/python tests/integration/run.py       # add --canary
```

Full documentation in [`tests/integration/README.md`](../../../../tests/integration/README.md).
`make integration` is an **integration request** (root `Makefile` is coordinator-owned).

## Requirement coverage

| Test ID / case | Exact invariant demonstrated | Where |
|---|---|---|
| **F-CONTRACT** `api_stream__canonical_events_end_with_authoritative_usage` … `…health_and_drain_are_observable` (8 exported cases) | The same suite `infrx.contracts.fakes.FakeEngine` passes, run unmodified against `HttpEngine` — a `ports.Engine` adapter decoding SSE off a real socket. Progress/delta/usage envelopes, one authoritative usage event, split `<think>` delimiters, stall clock advances, malformed and missing usage never authoritative, an abrupt exit raising a non-`DomainError`, a cancellation reporting only work really done, drain observable. `skipped_hooks: []` — the one optional engine hook (`text`) is supplied, so no case is a skip | `test_fake_vllm.py::test_the_exported_engine_conformance_suite_passes_over_http`, and the `engine` stage of `run.py` |
| **F-CONTRACT** (wire shapes a conformance case cannot see) | A stall is *declared* (`: infrx-stall <s>`) and visible on the wire, ends the stream cleanly, and moves only the injected clock; a 200 stream that stops without `[DONE]` is `EngineProcessExited`, never a success; `_usage` refuses `"1200"`, `null`, `True`, `-1` and `1.0` and never coerces; cancellation is keyed by job and leaves another job streaming; a per-request fault header beats the process default | `test_fake_vllm.py` (13 cases) |
| **F-CONTRACT** every fault mode 04 requires exists | `prefill_stall`, `midstream_stall`, `malformed_usage`, `missing_usage`, `cancellation_race`, `abrupt_exit`, `split_reasoning_delimiters` are all selectable and all named in `EngineFault` | `test_every_fault_mode_04_requires_is_implemented` |
| **DUR-RLS** `E2-RLS-01`…`04` | An unauthenticated browser (`anon`) reads **zero** rows from organizations, api_keys, credit_ledger and even the model catalog | `test_services.py::test_the_role_matrix_holds_for_every_role` |
| **DUR-RLS** `E2-RLS-10`…`14` | A member of org alpha sees **no** row of org beta in organizations, api_keys, usage_events or credit_ledger, and still sees its own tenant's keys (both directions, so a missing grant cannot masquerade as a working policy) | same |
| **DUR-RLS** `E2-RLS-20`…`28` | A member cannot mint a key (`42501`), cannot revoke one (policy filters the update to **0 rows**), cannot promote itself to operator (the *column* grant refuses, not RLS), cannot insert ledger or usage rows, cannot delete a key, cannot join another tenant by writing `org_members`; it *can* rename itself (1 row) and cannot rename another tenant's profile (0 rows) | same |
| **DUR-RLS** `E2-RLS-30`…`34` | An owner mints a key for its own tenant (1 row), is refused for another tenant, is refused when `created_by` names someone else (no forged authorship), revokes its own key (1 row) and not another tenant's (0 rows) | same |
| **DUR-RLS** `E2-RLS-40`…`44` | `org_balance` / `org_usage_summary` refuse a foreign org with `42501` and return the exact seeded values for their own (`Decimal("23.746875")`, `requests = 12`); `anon` cannot reach either | same |
| **DUR-RLS** `E2-RLS-50`…`53` | A platform operator reads every tenant (R26) and every ledger row (6), may read any tenant's balance through the RPC, and still **cannot** write the ledger directly — an operator grant is an audited server action (R34), not a table write | same |
| **DUR-RLS** `E2-RLS-60`, `61` | `service_role` has `BYPASSRLS` (measured: `rolbypassrls = t`) and can read and meter any tenant, so tenant safety for the gateway is **route-side** — which is why `06` forbids trusting a caller-supplied org behind a service credential | same |
| **DUR-RLS** `E2-RLS-70` | `auth.uid()` is `current_setting('request.jwt.claim.sub')`: whoever sets the claim **is** the tenant, so it may only ever come from a verified JWT | same |
| **DUR-RLS** migrations preserve existing balances | `0001_init.sql` + `0002_seed_models.sql` apply to a fresh Supabase-compatible database; re-applying the idempotent `0002` leaves the exact ledger snapshot unchanged (`{'alpha-e2': 23.746875, 'beta-e2': 23.746875, …}`, exact `Decimal`s, never floats) | `run.py` `migrate` stage, `test_balances_are_exact_decimals_and_the_seed_is_reproducible` |
| **DUR-RLS** the role matrix runner can fail | Three deliberately wrong expectations come back `passed = False`, including one that errors with the **right** refusal checked against the **wrong** SQLSTATE — "it errored" is not the assertion | `test_a_check_that_should_fail_does_fail` |
| Fixtures are real, not hand-written rows | Seeding goes through `auth.users`, so `handle_new_user` creates the profile, the personal org and the owner membership; the suite asserts the trigger did it | `test_the_signup_trigger_and_not_the_harness_created_the_tenants` |
| Movable database clock, test-only | `infrx_e2_test.now()` moves ±, transaction-locally (measured offset `+3600.0 s`, and `< 1 s` outside the transaction); **no migration** creates the schema or references the GUC, checked against the migration files | `test_database_time_moves_only_inside_a_transaction_that_asks_for_it`, `test_the_movable_clock_cannot_exist_in_a_deployed_database` |
| Fault injection: process loss | `SIGKILL` inside a container produces a client failure, not a silent success, and the harness restores the service; a save-less Valkey comes back **empty**, which is what makes the DUR-OUTBOX rebuild drill cheap | `test_killing_a_container_is_survivable_and_the_harness_puts_it_back` |
| Fault injection: network | `pause` **hangs** the client (kernel accepts, nothing answers — the shape that finds a missing timeout: the 2 s client timeout fired in < 15 s); `disconnect` is a hard partition. Both revert | `test_a_paused_container_hangs_the_client_and_recovers_when_unpaused`, `test_a_network_partition_is_distinguishable_from_a_dead_process` |
| Fault injection: engine process | `FakeVllmServer.kill(SIGKILL)` → `httpx.HTTPError` on `/health`, `EngineProcessExited` on a stream, and `start()` brings it back | `test_killing_the_engine_process_is_a_transport_failure_and_it_restarts` |
| Cleanup scoped to the namespace | Two gates, not one: the name must carry `infrx-e2-` **and** docker must attribute the container to project `infrx-e2`. Proved against the live daemon with a decoy *inside* our namespace that the project did not create — reported, never removed | `test_cleanup_is_scoped_and_refuses_a_container_it_did_not_create`, `test_the_prefix_alone_is_not_enough_to_be_touchable`, `test_a_destructive_helper_refuses_anything_outside_the_namespace` |
| Every image pinned by digest; ports in range | All four `image:` references carry a 64-hex `@sha256:` and no tag; every published host port is in 55500–55599 (checked against `tasklocal.local_services("e2")`, not a copy) and bound to `127.0.0.1`; volumes are project-scoped with no host bind mount; teardown fails if a project volume survives | `test_every_image_is_pinned_by_digest`, `test_every_port_is_inside_the_range_tasklocal_grants_this_task`, `test_the_compose_file_publishes_exactly_those_ports_on_loopback`, `test_every_container_is_named_in_the_namespace_and_volumes_are_project_scoped` |
| No production credential is reachable | A grep over every owned file for `supabase.co`, `amazonaws.com`, `callbill.ai`, `llm-bootcamp`, `SUPABASE_SERVICE_ROLE_KEY`, `AWS_SECRET_ACCESS_KEY`, `GATEWAY_API_KEY` finds nothing (the needles are assembled from parts so the guard still scans itself) | `test_nothing_in_this_directory_points_at_production` |
| Nested console discovery | A console test **three** directories deep (`tests/e2e/discovery.test.ts`) is matched by exactly one declared pattern (`tests/**/*.test.ts`), read out of `package.json`; every `*.test.ts` E owns in that directory is matched; R48 hygiene (no `@/`, no `.tsx`) holds | `apps/app/tests/e2e/discovery.test.ts` (5 cases), plus F2's own root guard which now sees 131 console tests |
| Cross-module discovery | One command drives four runners and reports each one's own counts: this suite 41, `make api-test` 670, `make console-test` 131, `make bench-test` 40 | `run.py` `suites` stage |
| An intentional failure is **detected**, not skipped | `INFRX_E2_CANARY=fail` → pytest `3 failed` (one canary per Python module) and `node --test` `# fail 1`, both naming `E2 canary`; `run.py --canary` treats a green run as *the* failure | `run.py` `canary` stage |
| `secret_grams()` prefix fix (E1 round-6 note) | `fold("sk-marlin-")` is `sk-marlin`, which prefix-matched a key spelled `sk-marlin2b…`, so `marlin2b` counted as public boilerplate and the windows straddling it were dropped: **fail-open**, a `--label` carrying 8 characters of the secret body passed the argv gate. Measured before: 5 windows from index 7, `carries_key("rlin2bzz", key) == False`. After: 11 windows from index 1, refused. The real `sk-infrx-`/`sk-marlin-` formats keep exactly the windows they had | `models/marlin2b/tests/test_leaks.py::test_a_public_prefix_only_counts_with_its_separator` |

## Environment

| Item | Value |
|---|---|
| Host | `Linux 7.0.0-1010-aws x86_64`, local development box, **no cloud resource touched** |
| Python | 3.12.3 from the pinned `apps/infrx-api/.venv` (`make api-env`, `uv sync --frozen --all-extras`); `psycopg` 3.3.6, `valkey` 6.1.1, `clickhouse-connect`, `boto3` 1.43.98, `httpx` 0.28.1, `pytest` 8.4.2, no pytest plugins |
| Node / pnpm | `v22.23.1` / `9.15.9` |
| Docker | Engine `29.6.2`, Compose `5.3.1` |
| PostgreSQL | `supabase/postgres@sha256:7768d0d1d377250b718a9ad07f4661d008ebe6c96ecbbc4c08f3c5e53553e8fd` (tag `17.6.1.173`) → answered `PostgreSQL 17.6 on x86_64-pc-linux-gnu, compiled by gcc (GCC) 15.2.0, 64-bit` |
| Valkey | `valkey/valkey@sha256:d2e18f3410b6f616de1417f570fa55261af2898b9c5b2cfb6781ce2373ea43d1` (tag `8.1-alpine`) → `8.1.10` |
| ClickHouse | `clickhouse/clickhouse-server@sha256:87e0a5b72f5465b18eacca7c76850e7ff551c9795c50e451f5646299e5e24146` (tag `25.8.33.6-alpine`) → `25.8.33.6` |
| S3-compatible | `quay.io/minio/minio@sha256:14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e` (tag `RELEASE.2025-09-07T16-13-09Z`) → `MinIO` |
| Namespace | project `infrx-e2`; containers `infrx-e2-{postgres,valkey,clickhouse,s3}`; host ports 55532 / 55579 / 55523+55590 / 55500 / 55580 (fake vLLM), all on `127.0.0.1`; ClickHouse db `infrx_e2`; Valkey prefix `infrx_e2:`; objects `test/e2/` in bucket `infrx-e2`; SQL schema `infrx_e2_test`; run state `$TMPDIR/infrx-e2-state.json` |
| Credentials | fixed local literals in `compose.yaml` (`infrx-e2-local`, `infrx-e2-local-secret`). **No production credential is needed or present.** Classification: **local only** |
| Seed | `--seed 20260921` (default). **Corrected in round 2 (r1 B4):** the seed fixes the four `auth.users` ids, the two api-key ids and secrets and the 17 usage-event ids; organization ids and slugs come from `0001_init.sql`'s trigger (`gen_random_uuid()`) and are **not** seed-derived. `pgstate.seeded_ids()` is the single definition and `test_exactly_the_seeded_ids_are_in_the_database_recomputed_from_the_seed` recomputes it. |

### Why `supabase/postgres`, and why the database is `postgres` — **SUPERSEDED in round 2 (R-a)**

> The database is now **`infrx_e2`**, created `TEMPLATE postgres OWNER postgres`, so D1's
> `current_database() like 'infrx\_%'` clock gate works. The measurements below stand; what
> changed is the conclusion. See "Round 2" at the end of this report.

`08 §10` ("D1: four things the fakes cannot tell you", item 1) is confirmed: `0001_init.sql`
references `auth.users`, `auth.uid()`, `authenticated` and `service_role`. No D1 evidence
existed at the time of writing, so the choice was made here and is documented for D1 to adopt
or overrule. Measurements on this image:

| Probe | Result |
|---|---|
| `select rolname from pg_roles where rolname in ('anon','authenticated','service_role')` | all three present; `rolbypassrls` true for `service_role` only, `rolcanlogin` false for all three (so the matrix connects as `postgres` and `SET ROLE`, as PostgREST does) |
| tables in schema `auth` | `users, audit_log_entries, refresh_tokens, instances, schema_migrations` |
| functions in schema `auth` | `uid, role, email` — `uid()` is `current_setting('request.jwt.claim.sub')::uuid` |
| extensions | `plpgsql, uuid-ossp, pgcrypto, pg_stat_statements, supabase_vault` |
| both console migrations | apply unchanged, **no shim migration needed** |
| `create database infrx_e2` then `\i 0001_init.sql` | `ERROR: schema "auth" does not exist` — the entrypoint builds `auth` in `postgres` only |
| `create database infrx_e2 template postgres` | `ERROR: source database "postgres" is being accessed by other users … 2 other sessions` — the image runs `pg_net 0.20.4` and a `pg_cron scheduler` holding permanent sessions |

So the database is **`postgres`**, which is also what a real Supabase project's database is
called. `08 §8`'s `infrx_<task>` naming exists to stop two worktrees colliding; that is
achieved here by the container name, the host port and the disposable volume. **This is a
documented deviation from 08 §8 and an input for D1** (see integration requests).

## Commands

Environment variable *names* only; no value is recorded. All runs on 2026-09-21 UTC, no
network beyond the public registries during the one image pull.

| # | Command | Exit | Result |
|---|---|---|---|
| 1 | `docker pull supabase/postgres:17.6.1.173` / `valkey/valkey:8.1-alpine` / `clickhouse/clickhouse-server:25.8.33.6-alpine` / `quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z`, then `docker images --digests` | 0 | the four digests in the Environment table |
| 2 | `apps/infrx-api/.venv/bin/python tests/integration/run.py --canary --report …` (06:50:54Z → 06:52:01Z, 66.4 s) | **0** | every stage `PASS`: see the stage table below |
| 3 | `make api-test` | 0 | `670 passed, 2 warnings in 28.67s` — unchanged from F2.1's 670, so no regression |
| 4 | `make console-test` | 0 | `# pass 131 / # fail 0 / # skipped 0`. The F2.1 integration entry recorded 126; the 5 added cases are the ones in `apps/app/tests/e2e/discovery.test.ts`, and the pre-existing count is unchanged |
| 5 | `make console-lint` | 0 | `✖ 2 problems (0 errors, 2 warnings)` — both warnings pre-exist in `lib/contracts/`, which E does not own |
| 6 | `make console-typecheck` (`next typegen && tsc --noEmit`) | 0 | `✓ Types generated successfully`, no diagnostics |
| 7 | `make bench-test` | 0 | `40 passed in 8.19s` (39 before + the new `secret_grams` case) |
| 8 | `apps/infrx-api/.venv/bin/python -m pytest -q tests/integration` | 0 | `41 passed` (13 fake-vLLM + 13 guards + 15 services) |
| 9 | `apps/infrx-api/.venv/bin/python tests/integration/mutants.py --layer all` | 0 | `"mutants": 21, "killed": 21, "survived": 0, "pending": 0` |
| 10 | `git diff --quiet 8744418..HEAD -- models/marlin2b/results/` | 0 | E1's historical bench rows are **byte-identical to base** |
| 11 | `git diff --name-only 8744418..HEAD` | 0 | 14 files, all owned (see Changes) |

### Stage table from run #2

| Stage | Status | Measured |
|---|---|---|
| preflight | PASS | docker `29.6.2`; four digest-pinned images present; no foreign `infrx-e2-*` container; no task-local port in use |
| services | PASS | containers `infrx-e2-postgres, -clickhouse, -s3, -valkey`; versions as above |
| migrate | PASS | `0001_init.sql` sha256 `b31ab5ad6117a88005cfa9dfbb878b15bcefba135b5d3151eaf2d8171831d2bc`, `0002_seed_models.sql` sha256 `9f186e47ad8c88a1b7a97555f5ad0d6a49c3c3d5b8a66b78247e823fca629d38`; clock `infrx_e2_test.now()`, offset probe `3600.0 s`; balances `alpha-e2 23.746875`, `beta-e2 23.746875`, two personal orgs `0`; usage rows `alpha 12`, `beta 5` |
| rls | PASS | `35` cases, `failed: null`, roles `anon, authenticated, service_role` |
| engine | PASS | `run_engine_conformance` over HTTP, `8` cases, `skipped_hooks: []` |
| suites | PASS | `tests/integration` 41 · `make api-test` 670 · `make console-test` 131 · `make bench-test` 40 |
| mutants | PASS | 21 mutants, 21 killed, 0 survived, 0 pending |
| canary | PASS | python `exit 1`, `3 failed`, named; console `exit 1`, `# fail 1`, named |
| teardown | PASS | removed the four containers, `still_named_ours_but_not_ours: []`, no project volume left |

## R32 mutation results

`tests/integration/mutants.py` — one single-edit mutant per claimed invariant, applied to a
temporary copy of the owned trees. **21 declared, 21 killed, 0 survived** — but see round 2:
the runner was counting a `ModuleNotFoundError` in the fake-vLLM child as a kill, so the kills
for e2m01–e2m07 were **unearned** (r1 B3). They are now killed by assertion. Layer-2 mutants
report `pending` (never `killed`) without a live stack.

| id | Invariant | Layer | Result |
|---|---|---|---|
| e2m01 | malformed usage is never coerced into authoritative tokens | 1 | killed |
| e2m02 | a 200 stream without its terminator is a failure, not a success | 1 | killed |
| e2m03 | a torn connection raises instead of ending the stream quietly | 1 | killed |
| e2m04 | a declared stall advances the injected clock | 1 | killed |
| e2m05 | the opening role chunk is the contract's progress event | 1 | killed |
| e2m06 | a cancelled job reports only the work it really did | 1 | killed |
| e2m07 | reasoning delimiters really are split across chunk boundaries | 1 | killed |
| e2m08 | a destructive helper refuses a name outside the namespace | 1 | killed |
| e2m09 | every service image is pinned by digest | 1 | killed |
| e2m10 | every published port is inside E2's range and on loopback | 1 | killed |
| e2m11 | the movable database clock cannot exist in a deployed database | 1 | killed |
| e2m12 | namespacing really separates the colliding legacy ids | 1 | killed |
| e2m13 | a legacy id is only claimed for a document that contains it | 1 | killed |
| e2m14 | every matrix case states the invariant it pins | 1 | killed |
| e2m15 | a public key prefix only counts with its separator | 1 | killed |
| e2m16 | the role-matrix runner can report a failure at all | 2 | killed |
| e2m17 | an RLS refusal is checked against its SQLSTATE, not just "it errored" | 2 | killed |
| e2m18 | database time moves only inside a transaction that asks for it | 2 | killed |
| e2m19 | the namespace guard holds against the live daemon, not just the prefix | 2 | killed |
| e2m20 | the matrix's allowed writes really are allowed by the database | 2 | killed |
| e2m21 | a cross-tenant denial is measured, not assumed | 2 | killed |

Two of these **survived their first run and the tests were strengthened**, which is what the
mutation list is for:

- **e2m17** — `passed = outcome == "error"` (dropping the SQLSTATE comparison) survived,
  because no case exercised "errored, but with the wrong SQLSTATE". Added `E2-RLS-MUTANT3`:
  a statement that really raises `42501`, expected as `42P01`, must still fail. Without it a
  typo'd table name in any matrix row would have read as a passing tenant check.
- **e2m19** — dropping the compose-project gate from `assert_ours` survived, because every
  stranger's name already failed the *prefix* gate. Added
  `test_the_prefix_alone_is_not_enough_to_be_touchable`: a decoy created inside our own
  namespace but not by this project must be reported and refused.

## Failure drill

| Injection | Durable state before | Durable state after | Duplicate / retry behaviour | Cleanup |
|---|---|---|---|---|
| `SIGKILL infrx-e2-valkey` | key `infrx_e2:kill-drill = "before"` | the key is **gone**: `--save "" --appendonly no` means a restart is an empty index. Recorded on purpose — DUR-OUTBOX's "lose Valkey data" drill is cheap precisely because the index must be rebuildable from PostgreSQL, and nothing durable lives there | a client mid-flight sees `ConnectionError`/`TimeoutError`, never an empty success | `Faults` reverted with `compose up -d`; `wait_valkey` confirmed readiness |
| `docker pause infrx-e2-valkey` | service up | unchanged (the process is frozen, not killed) | the connection is **accepted** and never answered; the client's own 2 s timeout fired in < 15 s. This is the shape that finds a missing timeout | `docker unpause`, readiness reconfirmed |
| `docker network disconnect infrx-e2_default infrx-e2-valkey` | service up | unchanged | the peer **refuses** rather than hangs, which is a different client path from the pause above | `docker network connect`, readiness reconfirmed |
| `SIGKILL` the fake vLLM process | server answering `/health` | process gone (non-zero wait status) | `/health` raises `httpx.HTTPError`; a stream raises `EngineProcessExited`; never an empty 200 | `start()` restored it; `stop()` in `finally` |
| fake vLLM aborts a response body mid-stream (`abrupt_exit`) | — | the **server survives**; only the response is torn | the client raises `EngineProcessExited`, not a `DomainError`, and never reports a finished stream | connection closed by the server |
| a 200 SSE stream that stops without `[DONE]` (injected transport) | — | — | `EngineProcessExited("engine stream ended without [DONE]")` — the shape E1 measured client-side as `truncated_stream`; treating it as success is how a truncated generation becomes a billable request | none needed |
| every RLS check | seeded rows | **unchanged**: each check runs in its own rolled-back transaction, so the matrix is order-independent and re-runnable | a write a policy allows is rolled back with the rest | `reset role` after each |
| database clock moved +3600 s / −3600 s | — | the offset is transaction-local (`< 1 s` outside) | — | ends with the transaction |
| whole stack | — | — | — | `down -v --remove-orphans`, then **asserted**: no container and no volume carrying the project label survives; `still_named_ours_but_not_ours: []` |

## Legacy → namespaced test-id mapping (04 §1)

Published here as E2 is required to. Machine-readable source: `tests/integration/testids.py`
(`python tests/integration/testids.py` prints this table). **175 namespaced ids**, **15
legacy ids ambiguous between documents**, **30 legacy ids that are also plan task ids**.
`test_harness.py` checks the table against the documents: every legacy id must actually occur
in the document it is attributed to, no namespaced id may be issued twice, and a legacy id
used by two documents must resolve to two namespaced ids.

| Legacy id(s) | Source | Section | Scope | Namespaced |
|---|---|---|---|---|
| Q1–Q11 | `research/production-api/10-implementation-spec.md` | §12.1 test_redis_queue.py | queue order, leases, idempotent complete, depth counter | `SERV-Q*` |
| A1–A8 | `research/production-api/10-implementation-spec.md` | §12.1 test_admission.py | response selection, jittered Retry-After, idempotency, ceilings | `SERV-A*` |
| M1–M13 | `research/production-api/10-implementation-spec.md` | §12.1 test_media.py | SSRF, redirects, size cap, transcode geometry, cache isolation | `SERV-M*` |
| I1–I15 | `research/production-api/10-implementation-spec.md` | §12.2 integration against a fake vLLM | gateway+worker+queue against a fake engine, kills and restarts | `SERV-I*` |
| L1–L7 | `research/production-api/10-implementation-spec.md` | §12.3 loadtest/arrival.py | open-loop arrival-rate load runs | `SERV-L*` |
| F1–F29 | `research/production-api/10-implementation-spec.md` | §11 failure matrix | one row per injected production failure and its client-visible answer | `SERV-F*` |
| G1–G22 | `research/traces/05-gateway-capture-spec.md` | §11 | capture on the request path, spool, worker, feedback API | `TRACE-G*` |
| H1–H7 | `research/traces/08-phases-and-test-plan.md` | §9 cross-cutting drills | live A/B overhead, outage, restart, queue-full, rotation, cross-org, egress audit | `TRACE-H*` |
| T1–T12 | `research/traces/01-requirements.md` | §2 trace requirements | what a trace must contain and how long it lives | `TRACE-T*` |
| F1–F4 | `research/traces/01-requirements.md` | §2 feedback requirements | feedback acceptance, ownership and provenance | `TRACE-F*` |
| O1–O5 | `research/traces/01-requirements.md` | §2 operational requirements | retention, deletion, storage and key handling | `TRACE-O*` |
| NF1–NF11 | `research/traces/01-requirements.md` | §2 non-functional requirements | overhead, loss, duplication and cost bounds | `TRACE-NF*` |
| J1–J7 | `research/traces/01-requirements.md` | §2 judge requirements | consent, budget, rubric and egress rules | `JUDGE-J*` |
| C1–C7 | `research/traces/01-requirements.md` | §2 console requirements | tenant-safe reads, content states and operator scope | `CONSOLE-C*` |
| Q1–Q11, QI1 | `research/traces/06-feedback-and-judge-spec.md` | §5 unit tests | selection, sampling, budget, frames, prompt, schema, state machine, egress, cost | `JUDGE-Q*` |
| U1–U4 | `research/traces/07-console-spec.md` | §9 test plan | chQuery org binding, SigV4 vector, gzip/content guard, filter parsing | `CONSOLE-U*` |
| E1 | `research/traces/07-console-spec.md` | §9 manual end-to-end | the 9-step manual release checklist (**NOT** plan task E1) | `CONSOLE-E*` |

`D1`–`D4` in `production-api` §1 are design *decisions*, not test cases, and are deliberately
absent; `test_harness.py` asserts that too, so nobody adds them by accident.

### Ambiguous between documents (15)

| Legacy id | Resolves to |
|---|---|
| `F1` … `F4` | `SERV-F1`…`SERV-F4` (gateway failure matrix) **or** `TRACE-F1`…`TRACE-F4` (feedback requirements) |
| `Q1` … `Q11` | `SERV-Q1`…`SERV-Q11` (Redis queue) **or** `JUDGE-Q1`…`JUDGE-Q11` (judge selection) |

### Also a plan task id (30) — the worse pair, because "F2 failed" reads as a task

`C1 C2 C3 E1 F1 F2 G1 G2 G3 G4 G5 I1 I2 I3 I4 J1 J2 J3 M1 M2 M3 Q1 Q2 Q3 T1 T2 T3 U1 U2 U3`

Notably `E1` = the nine-step manual console checklist (`CONSOLE-E1`) **and** the benchmark
task; `Q1`/`F1` carry three meanings each. In reports, always use the namespaced form.

## Artifacts

Raw run output lives in the session scratch area (git-ignored, per R15), referenced by digest
rather than committed:

| Artifact | Path | Notes |
|---|---|---|
| stage report of run #2 | `<scratch>/e2-final.json` | the JSON `run.py --report` wrote; the stage table above is read from it |
| console log of run #2 | `<scratch>/e2-final.log` | no credential, no customer content |
| printed test-id table | `<scratch>/testids.md` | reproducible with `python tests/integration/testids.py` |

Owned-file sha256 at `be500de`:

```
9b6d6bcce1785620fc9a2ece8e35ce429919e731480317f010574f30d54a02fc tests/integration/compose.yaml
43eb43934e9ae6d85c1131b9f1a3e44a2cf7128e770ee25dc3eba8d4fa59294c tests/integration/harness.py
b3ea40305230c9b89ece5876ea5c8a67c91cbb5ff1997381282278d663fd1477 tests/integration/pgstate.py
c47763002d5c15257ac0292c29ad454a9784cf81fdbde4889d723da91c7f26c5 tests/integration/fake_vllm.py
94eecc100b67b1e89b30adb0b5fd188ad898063c9fce5a68881f76aba6beecc9 tests/integration/testids.py
c1ddbb0b4179cd1b11c2233b63f0287121592c581b92fe4fe4d48c6fbed08d60 tests/integration/run.py
14d69408c4b85a819ea7ba165c5c4a4fdd5dadf50e3565037248e789d58e4643 tests/integration/mutants.py
a0638a50cfb229913c87d1e6da47ecd1560fc84d22723002840616aa0b3a0b60 tests/integration/test_fake_vllm.py
36c111c27d9fa438e94c68ecb6df5ca3fa332517b953d74947bac2188812e82d tests/integration/test_harness.py
b1874d869b7120753c67c873863b4ef7e32414a5ea161ea9e21346070a128b72 tests/integration/test_services.py
d56c6784c40cde80a2e286c2721a9f9d9f0bb6b8c2635a41d868cea308b77df9 apps/app/tests/e2e/discovery.test.ts
```

## Changes

Owned paths only (`git diff --name-only 8744418..HEAD`, 14 files, +3574/−2):

```
tests/integration/{README.md,compose.yaml,harness.py,pgstate.py,fake_vllm.py,
                   testids.py,run.py,mutants.py,
                   test_harness.py,test_fake_vllm.py,test_services.py}
apps/app/tests/e2e/discovery.test.ts
models/marlin2b/bench.py                 (secret_grams prefix fix, +13/−2)
models/marlin2b/tests/test_leaks.py      (its regression test, +35)
```

No contract change requested. Nothing under `infrx/contracts/**`, `apps/app/lib/contracts/**`,
`research/plan/tasks.json`, the root `Makefile`, `infrx/config.py`, composition roots,
`CLAUDE.md`, `HANDOFF.md`, dependency manifests or lockfiles was touched.
`models/marlin2b/results/` is byte-identical to base.

**Migration / deploy / rollback implications:** none of E2's own — it adds no migration and
no runtime code path. It *does* pin a decision D1 inherits (the Supabase-compatible image and
the `postgres` database name); changing that later means changing `compose.yaml` and
`harness.PG_DATABASE` and nothing else. The test-only clock function lives in schema
`infrx_e2_test`, which is created by the harness at runtime and never by a migration, so
there is nothing to roll back in a deployed database.

## Limits

1. **Fake, not vLLM.** `HttpEngine` passing the exported suite proves the wire format and the
   decoder agree with the contract over a real socket. It proves nothing about vLLM. Real
   engine behaviour is W1's adapter and E4's allocated run. Owner: W, E.
2. **A stall is declared, not waited out.** The fake announces its stall and the adapter
   advances an injected clock. That is deliberate (08 §2) and it means these cases do not
   prove any real timeout fires. A hung socket is a different shape; `--stall-real-s` exists
   for a test that wants one, and no case uses it yet. Owner: W (TTFT/TPOT policy), E.
3. **The database is `postgres`, not `infrx_e2`** — a documented deviation from 08 §8 with the
   measurements above. If D1 chooses a shim migration over the Supabase image instead, this
   harness must follow it. Owner: D1.
4. **The role matrix is the console schema only.** It covers the seven tables and three RPCs
   that exist today. Every pilot relation (`jobs`, `credit_holds`, `capacity_reservations`,
   `stream_chunks`, `outbox`, `feedback`, `consent_history`, `judge_*`) is D1's and will add
   rows; `ROLE_MATRIX` is a list so that is additive. Owner: D1, E3.
5. **`service_role` bypasses RLS** (measured). Tenant safety for the gateway and the console's
   server actions is therefore route-side, and no case here can prove it. Owner: C, G, E3.
6. **RLS-only, not route-level.** `E2-RLS-70` shows the DB trusts `request.jwt.claim.sub`
   entirely. Whether the console ever sets that claim from user input is C's code and is
   untested here. Owner: C1, E3.
7. **`make bench-test` fails on a loaded host — a pre-existing wall-clock constant in E1's
   suite, with the cause now measured.** `models/marlin2b/tests/test_bench.py` bounds the
   open-loop driver lag with a literal on two lines:

   | Line | Assertion | Test |
   |---|---|---|
   | 99 | `assert lag[tag] < 0.1, f"{tag}: sends waited for completions (lag {lag[tag]}s)"` | `test_schedule_is_deterministic_and_independent_of_latency` |
   | 271 | `assert summary["schedule_lag_s"]["max"] < 0.1, "a retry wait must not count as schedule lag"` | `test_retried_rejections_stay_visible_and_latency_covers_every_attempt` |

   **Cause (measured):** the bound is host load, not the code. This box has 16 cores
   (`nproc`); while another session ran its own containers the 1-minute load average went
   `20.37 → 26.33 → 45.39`, `make api-test` stretched from `27.8 s` to `65.9 s`, and line 99
   failed with `assert 0.23425 < 0.1`. At ordinary load the same command is `40 passed in
   8.19s` and the full `run.py` exits **0**.

   **Attribution:** 3 failing occasions out of ~45 runs on this branch, all at high load,
   versus 0 out of ~30 on the base tree at ordinary load; not reproducible on demand at
   ordinary load (10 runs of `test_bench.py` alone, 8 of the whole directory, 6 concurrent
   current-vs-base pairs, 16 CPU-burner processes, and with the four containers up — all
   clean). No mechanism connects it to this task's change: `secret_grams` is a pure string
   function and the added case is pure computation. Reported, **not fixed and not hidden**.

   **Recommendation for the owning task (E1's suite, E track):** the invariant the line
   defends is "sends track the schedule even when the server is slow", i.e. lag must not grow
   with server latency — and the test already measures the same scenario at `ttft=0.001` and
   `ttft=0.25`, so the comparison between those two lags is available and is load-immune,
   where a `0.1 s` constant is not. E2 deliberately did **not** change the threshold: widening
   it weakens the invariant, and re-deriving the intent of an integrated task's assertion
   belongs to a review of that assertion, not to a side edit here. Until then `make check` and
   `make bench-test` are flaky on a shared host. Owner: E.
8. **The console end-to-end suite is pending, not passing.** `apps/app/tests/e2e/` proves
   discovery, the canary and R48 hygiene; `CONSOLE-U1`–`U4`, `CONSOLE-E1` and `CONSOLE-C7`
   are listed as pending with the module each one waits for, and a guard fails once a pending
   case's file appears. No DOM or browser stack was added (08 §6). Owner: C, U, V, E3/E4.
9. **`tests/integration` is not in any canonical `make` target**, so a coordinator running
   `make check` does not run it. That is the `make integration` integration request.
10. **One image pull is required on a fresh host** (~750 MB compressed, public registries).
    Without network `run.py` reports the service-backed stages `PENDING` and exits 3; layer 1
    still runs. MinIO is no longer on Docker Hub (`minio/minio` → `object not found`), so it
    comes from Quay; if Quay is unreachable the S3 service is pending.
11. **Digests are current, not eternal.** They were resolved on 2026-09-21 from the tags
    recorded beside them. A future re-resolve of the same tag may give a different digest; the
    digest is what is pinned and the tag is a comment.
12. **No PERF, no live, no GPU.** Nothing here measures latency, throughput or cost, and no
    cloud, Supabase or AWS resource was touched. PERF-PILOT and OPS-RECOVER remain E4's.

## Handback

**Next unblocked task:** **E3** (cross-module failures and security gate) — its start
dependency E2 is now implemented. E3 cannot be *integrated* until D6, M3, Q3, W3, G4, T3, J2,
C3, U3, V3, G5 and J3 are, and most of its verification-table cases need those tracks' real
adapters; what it can do immediately is grow `ROLE_MATRIX` and the fault drills against each
track's code as it lands.

**Integration requests (coordinator-owned files only):**

1. **Root `Makefile` — add `integration`:**
   ```make
   # E2: provisions fresh task-local services, runs smoke + contract tests, tears down.
   # Needs docker; exits 3 (PENDING, never PASS) when a service-backed stage cannot run.
   .PHONY: integration
   integration:
   	$(API)/.venv/bin/python tests/integration/run.py
   ```
   Please **do not** add it to `check`: it needs docker and ~70 s, and a `check` that needs a
   daemon stops being the cheap gate. A separate `make integration` run before a stage review
   is the intent.
2. **`research/plan/04-verification.md`** — the legacy→namespaced mapping now has a
   machine-readable home; consider linking `tests/integration/testids.py` from §1 so the next
   reader finds the checked table rather than re-deriving it. (Doc edit, coordinator's file.)
3. **For D1, as input rather than a request:** the image and database decision above, with its
   measurements. If D1 prefers a shim migration to the Supabase image, say so and E2 will
   follow; if D1 adopts the image, `tests/integration/compose.yaml` is the single place the
   digest lives and D1's task-local port stays 55432 (E2 uses 55532, no overlap).
4. **No dependency, config name, import or router** is requested. E2 adds no runtime code.

**Unresolved findings filed:** limit 7 (`make bench-test` wall-clock flake, owner E) and
limit 5/6 (route-level tenant safety is untestable from SQL alone, owners C/G for E3).

## Verification log

- 2026-09-21: Authored from the runs above at implementation SHA `be500de`. Every count is
  quoted from command output; no number here was typed by hand. Status **implemented**: the
  harness ran against real local services, and the cases that need another track's code are
  listed as pending, never as passed. Nothing is deployed and nothing is live-verified.
- 2026-09-21: Appended after one further owned-file commit, `5bab2b8` — `FakeVllmServer.start()`
  after a kill replaced `self._log` without closing the previous one, so the process-loss
  drill left one temp file behind per run. Re-verified at that head: `test_fake_vllm.py`
  `13 passed`, `mutants.py --layer 1` 15/15 killed, `run.py --canary` exit **0** with the same
  stage counts (35 RLS cases · 8 engine conformance cases · 41 + 670 + 131 + 40 suite cases ·
  21/21 mutants · canary detected and named in both runners · teardown clean). Branch head is
  `5bab2b8`; the implementation SHA this report is named for is unchanged.
- 2026-09-21: Two further full runs at head `5bab2b8` (07:00Z and 07:05Z) each exited **1**
  with **one** failing thing: `make bench-test`, on the `schedule_lag_s < 0.1` constants of
  limit 7, while another session drove this 16-core host to load average 26 and then 45
  (`make api-test` stretched 27.8 s → 65.9 s; the failure reads `assert 0.23425 < 0.1`). Every
  other stage passed identically in both runs — 35 RLS cases, 8 engine conformance cases,
  `tests/integration` 41, `make api-test` 670, `make console-test` 131, 21/21 mutants, canary
  detected and named in both runners, teardown clean. Limit 7 is updated with the measured
  cause and the recommendation; nothing E2 owns is implicated, and no threshold was changed.

---

# Round 2 — review r1 `fix_required` addressed at `bbf686b`

| Field | Value |
|---|---|
| Round-2 head SHA | **`bbf686b`** (round-1 head was `de70190`) |
| Status | **implemented**; every blocking finding and every ruling addressed, each with a killing test and a mutant |
| Commits | `425ae66` B1+R-a+R-b+R-c+B4 · `8102929` B3 path + fake vLLM same-pass + SIGTERM · `a4cbb9d` E1's injected clock · `39d2d19` B2 + honest verdicts + new mutants · `d95669c` README + loopback case · `fc87b89` three selector misses · `be101e6` one verdict definition · `bbf686b` mutant context |

## Headline run (2026-09-21 18:14:56Z → 18:17:04Z, 127.7 s)

`apps/infrx-api/.venv/bin/python tests/integration/run.py --canary` → **exit 0, all stages PASS**

| Stage | Measured |
|---|---|
| preflight | docker `29.6.2`; four digest-pinned images; no foreign container/volume/network; no orphaned fake server; no busy port |
| services | four containers; `PostgreSQL 17.6`, Valkey `8.1.10`, ClickHouse `25.8.33.6`, MinIO; database `{"database": "infrx_e2", "template": "postgres", "created_by": "supabase_admin", "owner": "postgres", "attempts": 1}` |
| migrate | both migrations by sha256; clock `infrx_e2_test.now()` offset probe `3600.0 s`; balances `alpha-e2 23.746875`, `beta-e2 23.746875` |
| rls | **39** cases, `failed: null`, roles `anon, authenticated, postgres, service_role`; **29** cases assert `auth.uid()`; **12** qualify their 42501 by message |
| engine | `run_engine_conformance` 8/8 over HTTP, `skipped_hooks: []` |
| suites | `tests/integration` **83 passed** · `make api-test` **670 passed** · `make console-test` **# pass 131 / # fail 0** · `make bench-test` **40 passed** |
| mutants | `{"mutants": 46, "killed": 44, "controls_survived": 2, "not_killed": 0, "pending": 0, "problems": null}` |
| canary | python `exit 1`, `4 failed`, named; console `exit 1`, `# fail 1`, named |
| teardown | removed the four containers; `still_named_ours_but_not_ours: []`; no project volume left |

`make console-lint` 0 errors (2 pre-existing warnings in `lib/contracts/`), `make console-typecheck` clean.

## Each item → commit + killing test/mutant

| Item | Commit | Killing test | Mutant |
|---|---|---|---|
| **B1.1** another checkout's live stack | `425ae66` | `test_a_stack_labelled_for_another_checkout_is_refused_not_destroyed`, `test_up_and_down_refuse_while_anything_foreign_exists_and_call_no_compose` | `e2m22`, `e2m19` |
| **B1.2** `down -v` deleting a same-named volume | `425ae66` | `test_an_unlabelled_volume_with_our_name_is_refused_not_deleted` | `e2m23` |
| **B2** run.py unguarded (7 + 8 claims) | `39d2d19` | `tests/integration/test_run.py`, 32 cases | `e2m30`–`e2m45` |
| **B3** false kills | `39d2d19`, `8102929`, `fc87b89`, `be101e6`, `bbf686b` | controls `e2c01`/`e2c02` must SURVIVE; `test_the_mutation_stage_and_the_cli_count_the_verdict_the_same_way` | `e2m46` |
| **B4** seed reproducibility | `425ae66` | `test_exactly_the_seeded_ids_are_in_the_database_recomputed_from_the_seed` | `e2m24` |
| **R-a** `infrx_e2` TEMPLATE postgres | `425ae66` | `test_the_target_database_is_a_template_copy_owned_by_postgres`, `test_provision_database_statements_are_the_ones_r_a_requires` | `e2m27` |
| **R-b** both JWT forms + non-NULL `auth.uid()` | `425ae66` | `test_both_jwt_claim_forms_are_set_for_an_impersonated_principal`, `test_a_matrix_that_authenticates_nobody_fails`, cases `E2-RLS-05`–`08` | `e2m25`, `e2m26` |
| **R-c** retry a bind failure once, then PENDING | `425ae66`, `8102929` | `test_a_bind_failure_is_retried_once_and_then_refused` | `e2m44` |
| SIGTERM teardown + orphaned server | `8102929` | `test_sigterm_tears_down_and_orphans_no_fake_server`, `test_preflight_fails_on_an_orphaned_fake_server_this_harness_owns` | `e2m45` |
| label-only container wedging preflight | `8102929` | `test_preflight_turns_a_harness_error_into_a_reported_failure` | (covered by `e2m22`'s FAIL path) |
| fake vLLM 4xx / loopback / cancel-after-delta / seedable | `8102929`, `d95669c` | `test_an_unknown_fault_is_a_4xx_not_a_500`, `test_the_fake_server_binds_loopback_unless_explicitly_allowed`, `test_a_cancel_that_arrives_after_the_first_delta_bills_only_what_was_produced`, `test_chunk_ids_and_created_are_seedable`, `test_a_per_request_fault_precedence_is_header_then_body_then_default` | `e2m41`, `e2m06` |
| RLS `rolbypassrls` + message fragments | `425ae66` | cases `E2-RLS-05`/`06`; `test_a_check_that_should_fail_does_fail` (wrong-SQLSTATE control) | `e2m17` |
| `testids.py` docstring | `39d2d19` | `test_namespacing_actually_resolves_the_collisions_it_exists_for` | `e2m12`, `e2m13` |
| E1's wall-clock bench bounds (owner E) | `a4cbb9d` | `test_schedule_is_deterministic_and_independent_of_latency`, `test_retried_rejections_stay_visible_and_latency_covers_every_attempt` | (E1 suite; bounded by `make bench-test` in the run above) |

## B1 reproductions, re-run

| Drill | Before (reviewer) | Now (measured) |
|---|---|---|
| Copy `tests/integration` elsewhere, `run.py --layer 2 --keep` while a stack is up | exit 0, all PASS, the first run's four containers and volumes **gone** | `[FAIL] preflight: refusing to touch resources this checkout did not create` naming each container with the owning checkout; **exit 1**. First stack intact afterwards: `infrx-e2-clickhouse infrx-e2-postgres infrx-e2-s3 infrx-e2-valkey`, volumes `infrx-e2_clickhouse-data infrx-e2_postgres-data infrx-e2_s3-data`, data `models: 4 / usage: 17 / orgs: 4` |
| `docker volume create --label reviewer=e2 infrx-e2_postgres-data`, then `harness.down()` | the volume was **removed** | `refusing to provision or tear down: these carry this project's names or label but were not created by this checkout`; the reviewer's volume **survived** |

## R-a: the template copy, measured

`CREATE DATABASE infrx_e2 TEMPLATE postgres OWNER postgres` succeeds on the pinned image, first
attempt. Three things were needed, each discovered by a failing run and recorded:

| Obstacle | Message | Resolution |
|---|---|---|
| the template's background sessions | `source database "postgres" is being accessed by other users … 2 other sessions` (`pg_net 0.20.4`, `pg_cron scheduler`) | terminate them immediately before the copy; the race with their reconnect is retried (4 attempts) rather than reported as a refusal |
| `postgres` is not a superuser here | `permission denied to terminate process … Only roles with the SUPERUSER attribute may terminate processes of roles with the SUPERUSER attribute` | run the statements as `supabase_admin` (`usesuper` true) through `docker exec` — it has no TCP password in this image |
| psql wraps a multi-statement `-c` | `DROP DATABASE cannot run inside a transaction block` | one `-c` per statement |
| copy owned by `supabase_admin` | `permission denied for schema public` on `0001_init.sql` | `OWNER postgres`: `public` is owned by `pg_database_owner` |

Verified in the copy: `auth.users` present, `auth.uid/role/email` present, `anon`/`authenticated`/
`service_role` present, `current_database() like 'infrx\_%'` true, both migrations apply, 4 models.
The image was **not** asked to do anything it refused, so nothing was worked around and D1's clock
gate is untouched.

## R-b: the vacuous-matrix hazard, closed

`impersonate()` sets `request.jwt.claim.sub`, `request.jwt.claim.role` **and** the JSON
`request.jwt.claims`. Every principal-bearing case then asserts `auth.uid()` equals the principal
before its statement runs — 29 of the 39 cases — and a mismatch is reported `no-identity` with
`passed=False`. Measured with `impersonate` replaced by a no-op: the same cross-tenant case still
answers 0 rows (the vacuous pass) and the run reports `outcome: no-identity`, `passed: False`.

New premise cases: `E2-RLS-05` `service_role` `rolbypassrls` true, `E2-RLS-06` anon/authenticated
false, `E2-RLS-07` `auth.uid()` is the impersonated principal, `E2-RLS-08` no claim means no identity.

## B3: what a kill means now

| Before | Now |
|---|---|
| `killed = returncode != 0 and selected` | a kill is pytest **`failed`** with **no `error`** (R40) |
| a `ModuleNotFoundError` in the fake-vLLM child counted as a kill for e2m01–e2m07 | `fake_vllm.py` resolves `apps/infrx-api` from `INFRX_E2_REPO_ROOT`, so the child imports in a temp copy; a collection error is reported `setup-error`, never a kill |
| a comment-only edit was "killed" | two CONTROLS (`e2c01`, `e2c02`) must **SURVIVE**; `CONTROL-KILLED` is a failure of the list |
| `-k` matching nothing counted as a survival | "N deselected" with no passed and no failed is `no-cases` |
| each false kill leaked `infrx-e2-fake-vllm-*.log` | `start()` drops its log when it raises |
| the stage and the CLI counted differently | `mutants.summarise()` is the one definition, pinned by a test and `e2m46` |

Round-2 mutation run: **46 mutants, 44 killed, 2 controls survived, 0 not-killed, 0 pending.**
Selector misses found and fixed on the way (three mutants that had been "surviving" a test that
never ran), and one mutant moved to layer 1 because against the live database it was unkillable:
the case reads the database the *unmutated* code created, and re-provisioning to prove it would
drop the fixtures every later case needs.

## E1's wall-clock bounds (owner E, done in this pass)

`bench.py` takes `CLOCK`/`SLEEP` hooks (real ones in production); the drivers, the retry wait and
the attempt timestamps go through them, and `fake_gateway.py` waits on the same hook so a virtual
clock is never mixed with real server sleeps. `test_bench.py` gains an **event-driven**
`virtual_time()`: a sleep registers a wake-up and blocks; the pump advances to the *earliest*
pending wake-up only once the loop's ready queue is empty. An additive clock is wrong here — a 1 s
Retry-After would push the shared clock past every later arrival and invent **1.156 s** of phantom
lag (measured while writing it).

| Assertion | Before | Now |
|---|---|---|
| `test_bench.py:271` retry case | `schedule_lag_s.max < 0.1` | `== 0.0`, exact |
| `test_bench.py:99` fast/slow case | `lag[tag] < 0.1` | `lag[tag] <= widest arrival slot` per tag, plus the review's signed relative invariant `lag["slow"] - lag["fast"] <= 1e-6` |

Equality between the two lags is **not** claimed: `send_s` is stamped after media preparation
(E1's coordinated-omission measure), so on a virtual clock which slot a send lands in depends on
the interleaving. A closed-loop driver would be seconds late, not one slot.

| Check | Result |
|---|---|
| `models/marlin2b/tests`, idle | **40 passed** 3/3, 9.4 s (was 26 s: the ttft waits are virtual now) |
| same, with 24 CPU burners at load average 30 on 16 cores | **40 passed** 4/4 — the condition that produced `assert 0.23425 < 0.1` |

Limit 7 of round 1 is therefore **closed**, and corrected in place above.

## Limits after round 2

1. **Harness runs are serialized host-wide** (R-c). One compose project name is shared by every
   checkout; the ownership label makes a second run refuse rather than destroy, but it still
   refuses. A per-checkout project suffix and port block would remove the restriction and is the
   obvious follow-up if two E sessions ever need to run at once.
2. **`INFRX_E2_CHECKOUT` and `INFRX_E2_REPO_ROOT` are test-runner seams.** Setting the first lets
   a process claim another checkout's resources; only `mutants.py` does, and only so a temp copy
   can see the stack it was asked to mutate against. Documented in `working_dir()`.
3. **`provision_database` shells out through `docker exec`** because `supabase_admin` has no TCP
   password in this image. That couples the migration runner to docker; if D1 prefers a shim
   migration to the template copy, this is the piece that changes.
4. Round-1 limits 1–6 and 8–12 stand unchanged (fake ≠ vLLM; a stall is declared; the role matrix
   is today's console schema; `service_role` bypasses RLS so route-side tenant safety is C/G's;
   `tests/integration` is in no canonical `make` target → `make integration` is still the
   integration request; one image pull on a fresh host; digests are current, not eternal; no PERF,
   no live, no GPU).
5. **The 20 ms inter-delta gap on the cancellation path** is the only real delay in `fake_vllm.py`.
   Without it the body is emitted before any client could cancel and the race cannot exist; it is
   not a timeout being waited out, and no other fault path has it.

## Verification log (round 2)

- 2026-09-21: Round-2 fixes at `bbf686b`. Every count above is quoted from command output. The
  three false round-1 claims are corrected in place and marked (`SUPERSEDED in round 2`,
  `Corrected in round 2`, `unearned`). Both B1 reproductions were re-run and now refuse instead of
  destroying, with the first stack's data intact afterwards. All containers, volumes, networks and
  processes created during this pass were removed; `docker ps -a`/`docker volume ls` show no
  `infrx-e2` resource. Nothing was pushed, and no `infrx-d1-*` or other session's resource was
  touched at any point.

### Round-2 addendum: R-c fired for real, and the drill's own orphan

Two things happened after the headline run and are recorded rather than smoothed over.

**1. A real ephemeral-range collision (R-c).** The confirming run at `2fc036e` (18:22:13Z)
reported:

```
[PEND] services: could not provision: port bind failed twice (ephemeral-range collision, R-c)
Error response from daemon: failed to set up container networking: driver failed programming
external connectivity on endpoint infrx-e2-s3 … failed to bind host port 127.0.0.1:55500/tcp
```

Exactly the ruling's scenario, on exactly the port it warned about. The harness retried once,
then reported **PENDING (exit 3)** — never a pass — and teardown removed everything it had
created. `busy_ports()` could not have caught it: nothing was *listening*, an outbound
connection merely held 55500 as its local port, which is why R-c asks for a retry rather than a
preflight check. The next run (18:24:33Z → 18:26:49Z, 136 s) was **exit 0, all stages**, with the
same counts as the headline run (39 RLS cases, 8 engine cases, 83/670/131/40, 46 mutants /
44 killed / 2 controls survived / 0 problems, canary named in both runners, teardown clean).

**2. The SIGTERM drill left the orphan it exists to prevent.** Checking the host after the
mutation run found one fake vLLM on 55583, from the `e2m45` mutant: that mutant removes the
SIGTERM handler, so the drill's child died without stopping its server. Fixed in `2fc036e` — the
drill kills the recorded process group in `finally`, which is the test tidying up after itself,
not part of what it asserts. A root-owned `/tmp/infrx-e2-s3` from an early round-1 MinIO run was
removed too (through the pinned image, since the host user cannot).

Final state at `2fc036e`: `docker ps -a`, `docker volume ls` and `docker network ls` show **no**
`infrx-e2` resource, no fake vLLM process survives, `/tmp` holds no `infrx-e2-*` path, and the
only other container on the host (`gideon-migration-order-test-…`, plus the `infrx-d1-*` and
`infrx-review-*` containers that came and went during this pass) was never touched.

### Round-2 final state at `c7977a0`

One more leak found by checking the host rather than trusting the code: 28
`infrx-e2-fake-vllm-*.log` files in `$TMPDIR`. The server's log was a
`NamedTemporaryFile(delete=False)` removed only by `stop()`, so any path that kills the
*owning* process leaks one - which is what the SIGTERM drill does to itself under the mutant
that removes the handler. It is now a `TemporaryFile`, unlinked at creation and freed when the
last fd closes, so it cannot be leaked at all; `_tail()` seeks the fd instead of a path, and the
drill reads its marker before `finally` removes it.

Final run at `c7977a0` (18:28:54Z -> 18:31:13Z): **exit 0, all stages** - 39 RLS cases,
8 engine conformance cases, `tests/integration` 83 passed, `make api-test` 670 passed,
`make console-test` # pass 131 / # fail 0, `make bench-test` 40 passed, 46 mutants /
44 killed / 2 controls survived / 0 problems, canary detected and named in both runners,
teardown clean. Afterwards: no `infrx-e2` container, volume or network; no fake vLLM process;
no `infrx-e2-*` path in `$TMPDIR`.

---

# Round 3 — review r2 `fix_required` (PROOF only) addressed at `4108e70`

The r2 review re-verified every behaviour and failed the task on **proof**: eight claims had no
test that caught their loss. Each now has a case and a mutant, and writing them found **three
real defects** in code the r2 review had signed off as working.

| Field | Value |
|---|---|
| Round-3 head SHA | **`4108e70`** (r2 head was `5f1552d`) |
| Status | **implemented**; all eight claims proved, three defects fixed |

## The three defects the new tests found

| Defect | How it showed | Fix |
|---|---|---|
| **`fake_server_orphans()` never worked.** `ps -eo args=` truncates each line to the terminal width — 80 columns under pytest, shorter than this repository's absolute paths — so the marker never matched and preflight could not have reported an orphan. The r1 note "preflight catches them" was wrong. | B5's first real test of it: a spawned server's pid was missing from a scan that had just been asked for it (`found []`). | reads `/proc/<pid>/cmdline`, so there is no width to truncate |
| **`_verdict` read its counts from the whole pytest output**, so a traceback quoting the failing test's own source was read as the run's verdict. The case that guards `_verdict` necessarily contains the literals `1 error in 0.37s` and `no tests ran` — and both turned genuine kills into `setup-error` / `no-cases` (measured: e2m47 and e2m50 reported `no-cases` while their own summary said `1 failed`). | the new B2 case, immediately | counts **and** `nothing_ran` come from pytest's summary LINE (`_summary()`), never from the body |
| **`FakeVllmServer` relied on the CLI's `--host` default**, so changing that default took every server down at `setup_module` — 17 collection errors instead of the one case that guards it, which the review's own criterion then read as "not a kill". | the V7 mutant: `killed → SURVIVED, errors=17` | `start()` passes `--host 127.0.0.1` explicitly |

## Each claim → test + mutant

| r2 finding | Test | Mutant | Result |
|---|---|---|---|
| **B1** P4 "each check runs in its own rolled-back transaction" | `test_the_role_matrix_holds_for_every_role` now snapshots `api_keys`, `usage_events`, `credit_ledger`, `org_members`, revocations, renames, operator flags and exact `balances()` either side of `run_role_matrix` and asserts equality | `e2m56` | killed; the mutant leaves exactly the residue the reviewer measured (`api_keys 3`, `usage_events 18`, `revoked 1`) |
| **B2** `mutants._verdict` untested | `test_the_verdict_reads_pytests_own_summary_and_not_the_exit_code` — canned summaries: `1 failed, 12 deselected` → killed; `1 error` → setup-error; `5 deselected` → no-cases; exit 2 with no summary → setup-error; **exit 1 with `30 passed` and nothing failed → SURVIVED** (the shape that discriminates "a kill needs a reported failure" from "non-zero exit"); plus two tracebacks that quote pytest's own phrases | `e2m47`, `e2m48`, `e2m49`, `e2m50` | all killed |
| **B3** mutation stage only ever driven with a healthy list | `test_the_mutation_stage_and_the_cli_count_the_verdict_the_same_way` now also drives a **surviving non-control** (FAIL, problems named, exit 1) and an all-pending list (PENDING, exit 3) | `e2m57` | killed |
| **B4** "12 cases qualify their 42501 by message" | `test_a_check_that_should_fail_does_fail` adds the right SQLSTATE with a message it never emits (`passed is False`, observed `42501`, real message present) and the same case with the fragment it does emit (`passed is True`) | `e2m58` | killed |
| **B5** unlabelled NETWORK not a candidate | `test_an_unlabelled_network_with_our_name_is_refused_not_removed` — a decoy network reported foreign, the gate refusing, the network surviving, and the project's own network still owned | `e2m51` | killed |
| **B5** real `fake_server_orphans` | `test_a_real_orphaned_fake_server_is_found_by_its_command_line` — spawns this checkout's `fake_vllm.py` on a free task-local port, asserts its pid is listed, that the scanner is never its own orphan, and that a dead server is not an orphan | `e2m52` | killed |
| **minor M12** `assert_ours` in `provision_database` | `test_provision_database_refuses_a_container_outside_the_namespace` — a stub `container_of` returning `infrx-d1-postgres`, and one carrying our name with another checkout's label; both refused with **no** `docker exec` reaching either | `e2m53` | killed |
| **minor M43** leaked server logs | `test_the_server_log_is_never_left_behind_in_the_temp_directory` — no `infrx-e2-fake-vllm-*` visible in `$TMPDIR` while running, after `stop()`, or after `SIGKILL`, while `_tail()` stays readable | `e2m54` | killed |
| **minor V7** default host | `test_the_fake_server_binds_loopback_unless_explicitly_allowed` asserts the default on its own (`127.0.0.1`, in `LOOPBACK`, `allow_non_loopback is False`) | `e2m55` | killed |

`Mutant.dirties_database` is new: e2m56 can only be proved by letting the un-rolled-back writes
commit, so the runner **re-provisions** the database afterwards (`database infrx_e2 recreated and
reseeded (seed 20260921)`). Without it every later layer-2 mutant fails on the residue and the
failures look like broken policies — which is exactly how the reviewer found B1.

## Commands

| # | Command | Exit | Result |
|---|---|---|---|
| 1 | `run.py --canary` (2026-09-21 21:40:16Z → 21:42:59Z, 163.0 s) | **0** | every stage PASS |
| 2 | `pytest -q tests/integration` **with** the stack | 0 | **88 passed** |
| 3 | `pytest -q tests/integration` **without** the stack | 0 | **67 passed, 21 skipped**, each skip naming `run.py` |
| 4 | `mutants.py --layer all` | 0 | `{"mutants": 58, "killed": 56, "controls_survived": 2, "not_killed": 0, "pending": 0, "problems": null}` |

Run 1's stages: 39 RLS cases (`failed: null`) · engine conformance 8/8, `skipped_hooks: []` ·
suites `tests/integration` **88** / `make api-test` **670** / `make console-test`
**# pass 131 / # fail 0** / `make bench-test` **40** · mutants as above · canary python
`exit 1, 4 failed, named` and console `exit 1, # fail 1, named` · teardown removed the four
containers with `still_named_ours_but_not_ours: []`.

## Limits recorded, not changed (r2 review)

1. **The ownership label is the checkout path**, which is guessable: a deliberate copier that
   sets `INFRX_E2_CHECKOUT` to another checkout's path is mis-classified as the owner. It stops
   the accident it exists for and nothing forges it in practice; a per-run random id in the state
   file is the upgrade if two E sessions ever share a host.
2. **A fake vLLM started by the test suite is orphaned if `run.py` is interrupted during the
   `suites` stage** — the signal handler only knows the servers `run.py` itself started. The next
   run's preflight now genuinely reports it (defect 1 above was why that claim had been hollow).
3. **D1's `infrx.now()` is declared STABLE**, so within one statement it returns the value it had
   before an `advance()` in that same statement. Move the clock in its own statement, then read.
4. For the coordinator, already agreed: when D1's migrations are in the harness, `E2-RLS-01`–`04`
   and `E2-RLS-44` flip to error expectations (D1 revokes `anon` outright) and E2 drops its
   private clock for `infrx_test`. Both are noted in `tests/integration/README.md`.

## Verification log (round 3)

- 2026-09-21: r2's eight proof gaps closed at `4108e70`; every count above quoted from output.
  Writing the proofs found three defects in already-reviewed code (the orphan scan, the verdict's
  output parsing, the server's reliance on a CLI default) and each is fixed with the case that
  found it. Test files plus three one-line code fixes; no behaviour was added. All containers,
  volumes, networks, processes and temp files created during this pass were removed.
