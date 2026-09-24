# Supported verification environment (E2C, VERIFY-REPRO)

The local gates are certified on **Linux** only. A Mac or any host missing a prerequisite can
still run individual suites, but the gate reports **BLOCKED / NOT RUN**, never PASS. The
machine-readable form of this page is [`environment.json`](environment.json);
[`preflight.py`](preflight.py) checks a host against it and nothing else decides it.

## Pinned host

Measured on the development host on 2026-09-24 (not the pilot box). `match` in
`environment.json` is the pin; a different version is `mismatch` and blocks the profile.

| Component | Pin (`match`) | Observed | Why |
|---|---|---|---|
| OS / kernel | Linux (`sys.platform`) | Ubuntu 24.04.4 LTS, Linux 7.0.0-1010-aws x86_64, glibc 2.39 | deploy/serve/measure scripts target the Ubuntu box |
| Python | 3.12.x in `apps/infrx-api/.venv` | 3.12.3 | `requires-python ==3.12.*`, built by `make api-env` from `uv.lock` |
| uv | 0.11.8 | 0.11.8 | `uv sync --frozen`, `uv run --frozen` |
| Node | v22 ≥ 22.18 | v22.23.1 | `apps/app` `engines.node` |
| pnpm | 9.15.9 | 9.15.9 | `apps/app` `packageManager`; `pnpm install --frozen-lockfile` |
| Bash | ≥ 4.4 | 5.2.21 | empty arrays under `set -u` in the box scripts |
| GNU coreutils | GNU | 9.4 | `realpath -m/-s`, `sha256sum`, `stat -c`, `mktemp -d` |
| util-linux flock | present | 2.39.3 | `models/marlin2b/measure/candidate.sh` |
| systemd | `systemd-analyze` present | 255 | `tests/i` verifies the shipped units |
| git | ≥ 2.28 | 2.43.0 (`init.defaultBranch` unset → `master`) | tests pin their own branch names; see below |
| Docker | server reachable | 29.6.2, Compose v5.3.1, overlayfs, cgroup v2 | D/Q task-local containers, E2 compose stack |

Service images, every one pinned by digest (the tag is a comment). Whether a host has them
is state, not a pin: preflight reports an absent one with its `docker pull` line, and a clean
host pulls them once, which is the only network step.

| Image | Tag | Digest (sha256) | Used by |
|---|---|---|---|
| `postgres` | 16 (16.14-bookworm) | `33f923b0…5e20` | `apps/infrx-api/tests/d/pgharness.py` default |
| `supabase/postgres` | 17.6.1.173 | `7768d0d1…e8fd` | E2 stack; tests/d with `INFRX_D1_IMAGE=supabase` |
| `valkey/valkey` | 8.1-alpine | `d2e18f34…43d1` | E2 stack; tests/q `vkharness.py`; tests/d `vkstore.py` |
| `clickhouse/clickhouse-server` | 25.8.33.6-alpine | `87e0a5b7…6146` | E2 stack |
| `quay.io/minio/minio` | RELEASE.2025-09-07T16-13-09Z | `14cea493…936e` | E2 stack (S3-compatible) |
| `postgrest/postgrest` | v13.0.4 | `a312f4b2…7732` | `tests/integration/backend/compose.yaml` (layer 3) |

Recorded host state that is not ours and must not be touched: two App dev servers
(`next-server v16.3.5`, pids 3907004 and 3969240, listening on :3111/:3112, up ~4.8 days, owner
unknown) fail the dev-host certify precondition "App/Lab stopped"; a `infrx-d2-postgres`
container (55433) and `gideon-migration-order-test-*` belong to other checkouts.

## From a clean checkout

```bash
make api-env                                            # pinned Python env (uv sync --frozen)
(cd apps/app && pnpm install --frozen-lockfile)         # App checks
docker pull <ref>                                       # each image the profile lists (see preflight output)
apps/infrx-api/.venv/bin/python tests/integration/preflight.py consumer-local   # 0 = ready
tests/integration/consumer-local.sh                     # or backend-certify.sh / app-e2e.sh
```

`preflight.py <profile>` prints JSON (`verdict`, `exit`, one row per check, `not_ok`) and exits
**0** PASS, **3** BLOCKED (missing/mismatched tool, absent image, busy port, a container already
holding the namespace, or the wrong platform), **4** INVALID (a `--certify-profile` that lacks a
field group or carries a literal secret), **2** usage. It never pulls, starts or removes
anything.

## Gates

| Wrapper | Composes | Verdict |
|---|---|---|
| `consumer-local.sh` | preflight → `tests/contracts`, `tests/d`, `m`, `w`, `g` (each `uv run --frozen pytest -q`, on the lane's own services) → the `bench-test` suite → `run.py --layer 1 --only-suites --no-mutants` → `run.py --layer 2 --no-mutants` when its own preflight passes | worst stage |
| `consumer-local.sh --break-seam readiness\|expiry` | preflight → one existing mutant (`tests.g.mutants:pilot_starts_unreachable`, `tests.contracts.mutants:upload_expiry_ignored`) through the shared runner, pristine baseline first | must be **FAIL** (killed); PASS would be an undetected broken seam |
| `backend-certify.sh [--certify-profile P] [--validate-only] [-- flags]` | preflight (+ profile shape) → `tests/integration/backend/certify.py` for the local fake-engine target only | `--box`/`--target` without a profile: INVALID; with one: NOT RUN, prints the recorded box command, starts nothing |
| `app-e2e.sh` | preflight → `make console-test console-lint console-typecheck` | NOT RUN until E3A's browser journey exists |

Each writes `<out>/verdict.json` (`--out DIR`, default a fresh `$TMPDIR/infrx-e2c-<gate>-*`),
with the head SHA and dirty flag, per-stage command, exit, duration, junit counts
(`tests/passed/failed/errors/skipped/xfailed`), skip and xfail reasons and failing node ids.
Exit codes: **0** PASS, **1** FAIL, **3** BLOCKED or NOT RUN, **4** INVALID. Ordering:
FAIL > INVALID > BLOCKED > NOT RUN > PASS.

- A pytest stage that exits 0 **with skips** is BLOCKED: a required case that did not run
  certifies nothing. Strict `xfail`s (tests/d's declared "no real store can pass this as
  written" cases) are listed with their reasons and do not change the verdict.
- **Quarantine** has no mechanism of its own: a quarantined case is a skip, so the gate it
  belongs to cannot be PASS. Record owner, reason and expiry in the skip reason and the lane
  evidence.
- pytest stages run with `--basetemp=<out>/basetemp/<stage>`: pytest < 9.0.3 roots its
  temporary directories in the shared `/tmp/pytest-of-<user>` (GHSA-6w46-j5rx-g56g). `TMPDIR`
  is deliberately not moved: the D/Q harnesses take their host-wide `flock`s under it.

## Namespaces and teardown

Ports, container names and database names come from **one registry**,
`apps/infrx-api/infrx/contracts/tasklocal.py` (the E2 compose layout from
`tests/integration/harness.py`, which `test_harness.py` asserts against it).
`environment.json` only names which namespace a profile uses.

| Namespace | Source | Ports | Containers / DB / objects | Used by |
|---|---|---|---|---|
| `e2c` | tasklocal `e2c` | 55448 PostgreSQL, 55474 Valkey (55475 S3 reserved, unused) | `infrx-e2c-postgres` (`infrx_e2c`), `infrx-e2c-valkey`; object prefix `test/e2c/` | consumer-local API stages: `INFRX_D_TASK=e2c`, `INFRX_D2_VALKEY_PORT/_CONTAINER` |
| `e2` | harness `e2` | 55500, 55523, 55532, 55579, 55580, 55590 | `infrx-e2-*`, `infrx_e2`, `test/e2/`, `infrx_e2:` | `run.py` layers, certify.py local |
| `e2c-selftest` | E2C lane block | 55510–55519 | none (`infrx-e2c-selftest-*` reserved) | `test_preflight.py` binds 55510 |

Setup refuses (BLOCKED) when a namespace port is bound or a container with its name exists.
Teardown belongs to the runner that created the resource: `pgharness`/`vkstore` remove their
containers at interpreter exit (only ones carrying this checkout's label), `run.py` removes the
`infrx-e2-*` stack in its `finally`. After a crash, list what is left with
`docker ps -a --filter name=infrx-e2c-` and `docker ps -a --filter name=infrx-e2-`; the next
run of the same harness replaces its own labelled leftovers and reports foreign ones.

Known limit: mutant copies of DB-backed lists run with the shared runner's fixed environment
(`Runner.env`), which does not carry `INFRX_D_TASK`, so they fall back to `d1` (55432) under
its host-wide lock.

**Credentials.** None are needed. Every local credential is a fixed test literal in the
harness/compose files. Nothing here reaches the hosted database, the pilot box or AWS; a
certify profile references protected material by id or hash and a literal secret makes it
INVALID.

## Platform declarations (RV-12)

- `tests/i/support.py::LINUX_USERLAND` marks the cases that run the box's scripts as the box
  runs them. Off Linux they skip with `BLOCKED platform prerequisite: …`; on Linux they always
  run and a missing tool fails them (`systemd-analyze` absent is still a failure there).
  `support.blocked_off_linux` turns an off-Linux mutant whose named cases were all skipped into
  the same BLOCKED skip instead of a misdeclared list; it is inert on Linux.
- The gateway build-info case reads a fixture procfs through `collect_host(proc=...)`; the
  real `/proc` reading is its own Linux case (`…metrics_read_the_linux_host_the_gateway_runs_on`).
- The release-bundle case runs under `init.defaultBranch=main` on every host and names the
  box's unborn branch, so the fetch never targets the checked-out branch.

## Verification log

- 2026-09-24 (E2C): manifest measured on the development host; preflight, gates and platform
  declarations added; counts and failures in `research/plan/evidence/e/E2C-*.md`.
