# E2 — pinned integration services and fault harness

E's layer-2 stack (`research/plan/04-verification.md`): isolated real PostgreSQL, Valkey,
ClickHouse and S3-compatible storage with pinned versions, plus a controllable fake vLLM,
seeded fixtures, migration/RLS runners and fault injection. **No production credential is
needed and none may be reachable**; nothing here may be pointed at Supabase production or
AWS.

## The one command

```bash
make api-env                                                   # once: the pinned Python env
apps/infrx-api/.venv/bin/python tests/integration/run.py
```

It provisions a fresh stack, applies the console migrations, seeds deterministic fixtures,
runs the DUR-RLS role matrix, runs the exported engine conformance suite against the fake
vLLM over real HTTP, runs this suite plus the canonical `make` targets, runs the R32 mutation
list, and then removes exactly what it created. Exit `0` = everything ran and passed, `1` =
something failed, `3` = a service-backed stage could not run and is reported **PENDING**
(never PASS).

Useful flags:

| Flag | Effect |
|---|---|
| `--layer 1` | only what needs no container (fake vLLM, guards, mutants) |
| `--keep` | leave the stack up for iteration (still only `infrx-e2-*`) |
| `--canary` | also prove an INTENTIONAL failure is detected, in both runners |
| `--pull` | pull the pinned digests first (first run on a fresh host) |
| `--seed N` | the fixture seed; the same seed gives the same uuids and rows |
| `--report P` | write the JSON stage report to `P` |
| `--no-mutants` | skip the ~2 minute mutation run |

**Harness runs are serialized host-wide** (r1 review R-c). One compose project name is shared
by every checkout of this repository, so two runs cannot be in flight at once; the ownership
label below makes the second run *refuse* rather than destroy the first, but it still refuses.
Ports 55500–55599 also lie inside the kernel's ephemeral range (32768–60999), so a transient
bind failure is possible: provisioning retries once after a short wait and then reports
PENDING (exit 3), never a pass.

`make integration` would be the natural spelling and is an **integration request**: the root
`Makefile` is coordinator-owned.

Preflight removes whatever a previous run of *this* project left (fresh services are the
acceptance criterion), then refuses to continue if any task-local port is still held by
something else, naming the ports — a busy port otherwise surfaces as an unexplained exit
status from whichever process tried to bind it.

Other entry points, all self-contained:

```bash
apps/infrx-api/.venv/bin/python -m pytest -q tests/integration       # the suite alone
apps/infrx-api/.venv/bin/python tests/integration/mutants.py --list  # the R32 mutant list
apps/infrx-api/.venv/bin/python tests/integration/testids.py         # the legacy test-id map
apps/infrx-api/.venv/bin/python tests/integration/fake_vllm.py --port 55580 --fault prefill_stall
```

## Files

| File | What it is |
|---|---|
| `compose.yaml` | the four services, every image pinned by digest, project `infrx-e2` |
| `harness.py` | compose lifecycle, namespace guard, readiness, clients, fault injection |
| `pgstate.py` | migration runner, seeded fixtures, the DUR-RLS role matrix, the test clock |
| `fake_vllm.py` | the ASGI fake vLLM, its server process, and the `ports.Engine` adapter |
| `testids.py` | legacy research test ids → the namespaced ids of 04 §1 |
| `run.py` | the one command above |
| `mutants.py` | R32: one single-edit mutant per claimed invariant, on a temp copy |
| `test_harness.py` | layer 1: the guards (pinning, ports, namespace, clock, test-id map) |
| `test_run.py` | layer 1: the orchestration itself — exit mapping, every stage, the harness lifecycle — with docker and the shells stubbed |
| `test_fake_vllm.py` | layer 1: the exported engine conformance suite over HTTP, plus wire shapes |
| `test_services.py` | layer 2: migrations, the role matrix, the three other stores, faults |

The console half of E's ownership is `apps/app/tests/e2e/`, which today proves nested console
discovery and the console canary and lists the `CONSOLE-*` cases still pending.

## Isolation

Everything is in one namespace, and every destructive helper checks it **three** ways: the name
must carry the prefix, docker must attribute the resource to this compose project, **and** it
must carry `ai.infrx.e2.checkout` set to this checkout's compose directory.

The third gate is the one that matters (r1 review B1): the project name is every checkout's, so
keying teardown on it destroyed another run's live stack, and `down -v` deleted a hand-made
volume that merely had the name compose would have picked. `compose.yaml` stamps the label on
every container, every volume and the network from `INFRX_E2_CHECKOUT` (with `:?`, so compose
refuses to run without it), and anything carrying one of our names or our project label without
it is **reported, never touched** — `up()` and `down()` both refuse while such a resource
exists, and `down()` records every id before removing and re-checks by id afterwards.

| Thing | Value |
|---|---|
| compose project | `infrx-e2` |
| containers | `infrx-e2-postgres`, `-valkey`, `-clickhouse`, `-s3` |
| ownership label | `ai.infrx.e2.checkout` = this checkout's `tests/integration` directory |
| PostgreSQL database | `infrx_e2`, created `TEMPLATE postgres OWNER postgres` (see below) |
| host ports | `55532` PG, `55579` Valkey, `55523`/`55590` ClickHouse, `55500` S3, `55580` fake vLLM — all inside E's 55500–55599 range (08 §8), all bound to `127.0.0.1` |
| volumes | project-scoped and disposable; `down -v` removes them and the teardown fails if any survive |
| ClickHouse database | `infrx_e2` |
| Valkey key prefix | `infrx_e2:` |
| object prefix | `test/e2/` in bucket `infrx-e2` |
| test-only SQL schema | `infrx_e2_test` (the movable clock) |
| run state | `$TMPDIR/infrx-e2-state.json`, removed at teardown |

Credentials are fixed local literals in `compose.yaml` (`infrx-e2-local` and friends). A
container named `infrx-e2-*` that this project did not create is **reported, never removed**.

## Pinned images

Resolved by digest on 2026-09-21 with `docker pull <tag>` followed by
`docker images --digests`; the tag beside each digest is the tag it was resolved from and is
a comment only.

| Service | Image | Digest | Tag |
|---|---|---|---|
| PostgreSQL | `supabase/postgres` | `sha256:7768d0d1d377250b718a9ad07f4661d008ebe6c96ecbbc4c08f3c5e53553e8fd` | `17.6.1.173` |
| Valkey | `valkey/valkey` | `sha256:d2e18f3410b6f616de1417f570fa55261af2898b9c5b2cfb6781ce2373ea43d1` | `8.1-alpine` |
| ClickHouse | `clickhouse/clickhouse-server` | `sha256:87e0a5b72f5465b18eacca7c76850e7ff551c9795c50e451f5646299e5e24146` | `25.8.33.6-alpine` |
| S3-compatible | `quay.io/minio/minio` | `sha256:14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e` | `RELEASE.2025-09-07T16-13-09Z` |

All four are public registries (Docker Hub, Quay). MinIO is no longer published on Docker
Hub — `minio/minio` returns `object not found` — so it comes from Quay.

### Why `supabase/postgres`, and why the database is `infrx_e2`

`08 §10` ("D1: four things the fakes cannot tell you", item 1) is right: `0001_init.sql`
references `auth.users`, `auth.uid()`, `authenticated` and `service_role`, none of which plain
PostgreSQL has.

- **`supabase/postgres` 17.6.1.173, no shim.** Measured on this image: the roles `anon`,
  `authenticated` and `service_role` exist (only the last two with `BYPASSRLS`), the `auth`
  schema carries `users`, `audit_log_entries`, `refresh_tokens`, `instances`,
  `schema_migrations` and the `uid()`/`role()`/`email()` functions, and `pgcrypto` +
  `uuid-ossp` are installed.
- **The database is `infrx_e2`** (r1 review R-a), created with
  `CREATE DATABASE infrx_e2 TEMPLATE postgres OWNER postgres`. The name matters: D1's
  `infrx.now()` honours a test clock offset only when `current_database() like 'infrx\_%'`,
  and production is `postgres`, so that gate is what keeps a movable clock out of a deployed
  database. Two things the copy needs, both measured:
  - the template's background workers must be gone first — `pg_net 0.20.4` and the `pg_cron
    scheduler` hold permanent sessions on `postgres`, and PostgreSQL refuses to copy a
    database anything else is connected to (`source database "postgres" is being accessed by
    other users … 2 other sessions`);
  - `pg_terminate_backend` on those needs a real superuser, and in this image `postgres` is
    **not** one (`usesuper` false) — `supabase_admin` is, and has no TCP password here, so the
    statement goes through `docker exec` on the container's local socket. Each statement is
    its own `-c`, because `DROP`/`CREATE DATABASE` cannot run inside a transaction block, and
    the race with the reconnecting workers is retried rather than reported as a refusal.
  - `OWNER postgres` is not cosmetic: `public` is owned by `pg_database_owner`, so without it
    the copy's owner is `supabase_admin` and the migration fails with `permission denied for
    schema public`.
  - E2's own `infrx_e2_test.now()` clock stays until D1 merges, then gives way to D1's
    `infrx_test` clock; both are gated the same way.
- `auth.uid()` reads `current_setting('request.jwt.claim.sub')`, while hosted Supabase and
  PostgREST ≥ 10 set the JSON `request.jwt.claims`. The matrix sets **both** (r1 review R-b)
  and asserts `auth.uid()` is the impersonated principal on every principal-bearing case — a
  matrix in which it is NULL passes every deny-case vacuously. Case `E2-RLS-70` is the point:
  whoever sets the claim **is** the tenant, so it may only ever come from a verified JWT.

## What the fake vLLM does

An ASGI app (`FakeVllmApp`) speaking the OpenAI/vLLM chat API, run in its own process so
"kill the engine" is a real `SIGKILL`, plus `HttpEngine`, the `ports.Engine` adapter that
decodes its SSE. `HttpEngine` passes the **exported** `run_engine_conformance` suite — the
same cases `infrx.contracts.fakes.FakeEngine` passes — which is what F-CONTRACT asks for.

Fault modes (04 §Test environments plus 08 §2), selected per request with the
`X-Infrx-Fault` header or an `infrx_fault` body field, or process-wide via `POST /_control`:

| Fault | Wire behaviour |
|---|---|
| `prefill_stall` | role chunk, then a declared stall past `TTFT_TIMEOUT_S`, then a clean end with no usage |
| `midstream_stall` | one delta, then a declared stall past `TPOT_STALL_S` |
| `malformed_usage` | a usage object with `"1200"` and `null` counts |
| `missing_usage` | `finish_reason` but no usage object at all |
| `cancellation_race` | a cancel **before** the stream gives one usage chunk for zero work; a cancel **after** the first delta stops the stream and reports the deltas actually sent. 20 ms between deltas on this path only, because without a gap the body is emitted before any client could cancel |
| `abrupt_exit` | the response body is aborted mid-stream: no terminator, torn connection |
| `split_reasoning_delimiters` | `<think>`/`</think>` split across chunk boundaries |

An unknown fault is a **400** carrying the set it would have accepted, never a 500, and a
refused `/_control` leaves the default unchanged. `--host` must be loopback unless
`--allow-non-loopback` is passed in so many words. `--seed` makes the chunk `id`s and `created`
a function of the seed, so a captured transcript is comparable between runs.

**A stall is declared, not waited out.** The stream emits an SSE comment
`: infrx-stall <seconds>` and the adapter advances its **injected** clock by that much,
exactly as the in-memory fake expresses a stall as a clock advance — the timeout policy
belongs to W, not to the engine, and a suite with a 61-second sleep in it gets deleted by the
first person in a hurry. `--stall-real-s` adds a real delay on top when a test wants to watch
a socket actually go quiet.

A stall ends the stream **cleanly** (`data: [DONE]`, no usage) while `abrupt_exit` tears the
connection. The adapter treats those differently on purpose, and a 200 that simply stops
without `[DONE]` is an `EngineProcessExited`, never a success — the shape E1 measured from
the client side (`truncated_stream`).

## Fault injection

| Helper | Shape | Undo |
|---|---|---|
| `Faults.kill_container(svc, "SIGKILL")` | process loss inside a container | `compose up -d` |
| `Faults.pause(svc)` | a network drop that **hangs**: the connection is accepted and nothing answers, which is what finds a missing client timeout | `docker unpause` |
| `Faults.disconnect(svc)` | a hard partition: the endpoint leaves the project network, so the peer refuses rather than hangs | `docker network connect` |
| `pgstate.set_clock_offset(conn, s)` | database time moves, transaction-locally | end of transaction |
| `FakeVllmServer.kill(SIGKILL)` | the engine process dies mid-request | `start()` |

`run.py` handles SIGTERM as well as SIGINT: both raise into `main`'s `finally`, so the stack is
torn down and the exit code is 128+signum, and the fake server runs in its own process group so
one `killpg` takes it with them. A server that still survives is reported by the next run's
preflight rather than surfacing as a failure to bind.

`Faults` is a context manager that reverts in reverse order even when the body raises, and
every container helper is namespace-checked first.

The database clock lives in schema `infrx_e2_test` behind the GUC `infrx_e2.clock_offset_s`.
**No migration creates either**, which is checked against the migration files rather than
asserted — a clock a deployed process can move is a way to release an unknown-usage hold
early (08 §10, item 3).

## Honesty rules this harness follows

- A service-backed step that cannot run is **PENDING**, never PASS. Without a provisioned
  stack every layer-2 case is a reported skip naming the command that would run it.
- An intentional failure must be *detected*: `INFRX_E2_CANARY=fail` makes one case in each of
  the three Python modules and one in the console suite fail, and `run.py --canary` treats a
  green run as the failure.
- Counts in evidence come from command output, never from a hand-typed number.
- R32: `mutants.py` ships one single-edit mutant per invariant this task claims, applied to a
  temporary copy; a survivor is a failed task.
