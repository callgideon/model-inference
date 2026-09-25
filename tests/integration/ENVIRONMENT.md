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
| `net.ipv4.ip_local_reserved_ports` | covers every namespace port (an uncovered one is reported as a **risk**, not pinned) | `55432-55499,56379,56700-56999,58123,59000,59100,59110` (`/etc/sysctl.d/60-infrx-task-ports.conf`, P-21, set 2026-09-24/25); ephemeral range 32768–60999 | task-local ports sit inside the ephemeral range (see below); the E2 block 55500–55599 is still exposed |

Service images, every one pinned by digest (the tag is a comment). Whether a host has them
is state, not a pin: preflight reports an absent one with its `docker pull` line, and a clean
host pulls them once, which is the only network step.

| Image | Tag | Digest (sha256) | Used by |
|---|---|---|---|
| `postgres` | 16 (16.14-bookworm) | `33f923b0…5e20` | `apps/infrx-api/tests/d/pgharness.py` default |
| `supabase/postgres` | 17.6.1.173 | `7768d0d1…e8fd` | E2 stack; tests/d with `INFRX_D1_IMAGE=supabase` |
| `valkey/valkey` | 8.1-alpine | `d2e18f34…43d1` | E2 stack; tests/q `vkharness.py`; tests/d `vkstore.py` |
| `clickhouse/clickhouse-server` | 25.8.33.6-alpine | `87e0a5b7…6146` | E2 stack |
| `pgsty/minio` (MinIO community fork) | RELEASE.2026-08-04T00-00-00Z | `b6bfe723…d602372` (OCI index; `latest` resolved to it too) | E2 stack (S3-compatible). Replaces `quay.io/minio/minio@sha256:14cea493…`, which answers `401 UNAUTHORIZED` on pull |
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
apps/infrx-api/.venv/bin/python tests/integration/preflight.py integration-l3   # 0 = the stack stage runs
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
| `consumer-local.sh` | preflight → `s3`: the gate starts the manifest's MinIO pin as `infrx-e2c-s3` on e2c's S3 port (`docker run --pull never`, health-checked; BLOCKED and no endpoint exported if it cannot start) → every maintained API suite, `tests/{contracts,d,g,i,j,m,q,t,w}` (each `uv run --frozen pytest -q`, on e2c: `INFRX_D_TASK=e2c`, `INFRX_D2_VALKEY_PORT/_CONTAINER`, `INFRX_Q_VALKEY_PORT`, `INFRX_M_S3_ENDPOINT` + `INFRX_M_S3_LOCAL_CREDS=1`) → `infrx-e2c-s3` removed (also on an interrupt) → the `bench-test` suite → `run.py --layer 3 --only-suites --no-mutants` when the `integration-l3` preflight passes (stack, migrate, RLS, E3B backend, `tests/integration` with the stack up), else `run.py --layer 1 --only-suites --no-mutants` plus an `integration-l3` BLOCKED row → E3C's `tests/integration/backend/e3c/runner.py --out <out>/e3c` (NOT RUN while the file is absent; its `verdict.json["verdict"]` counts only if it agrees with the runner's exit code, else INVALID) | worst stage |
| `consumer-local.sh --break-seam readiness\|expiry` | preflight → one existing mutant (`tests.g.mutants:pilot_starts_unreachable`, `tests.contracts.mutants:upload_expiry_ignored`) through the shared runner, pristine baseline first | must be **FAIL** (killed); PASS would be an undetected broken seam |
| `backend-certify.sh [--certify-profile P] [--validate-only] [-- flags]` | preflight (+ profile shape) → `tests/integration/backend/certify.py` for the local fake-engine target only | `--box`/`--target` in any spelling certify.py's argparse accepts (`--target=URL`, an abbreviation such as `--tar`/`--b`) without a profile: INVALID; with one: NOT RUN, prints the recorded box command, starts nothing |
| `app-e2e.sh` | preflight → `make console-test console-lint console-typecheck` | NOT RUN until E3A's browser journey exists |

Each writes `<out>/verdict.json` (`--out DIR`, default a fresh `$TMPDIR/infrx-e2c-<gate>-*`),
with the head SHA and dirty flag, per-stage command, exit, duration, junit counts
(`tests/passed/failed/errors/skipped/xfailed`), skip and xfail reasons and failing node ids.
Exit codes: **0** PASS, **1** FAIL, **3** BLOCKED or NOT RUN, **4** INVALID. Ordering:
FAIL > INVALID > BLOCKED > NOT RUN > PASS.

- A pytest stage that exits 0 **with skips** is BLOCKED: a required case that did not run
  certifies nothing. A setup error or a pytest that exits nonzero (with or without a junit
  report) is FAIL.
- An **empty parametrization** (`got empty parameter set`: a mutant list whose default subset
  is empty - tests/m `test_pilot_mutants`, tests/w `test_prep_worker_mutants` and
  `test_worker_main_mutants` PG lists without `INFRX_MUTANTS=all`) has no case to run: it is
  listed under `counts.empty_params`, not counted as a skip. `make api-mutants` runs those lists.
- A **runner stage** (`run.py`, `certify.py`) is PASS only on exit 0 with a readable report
  whose runs record no skipped case; exit 3 (PENDING) is BLOCKED, exit 0 without a report is
  FAIL. At layer 1, `tests/integration` skips 103 stack cases (79 backend, 24 seeded
  services), so `integration-l1` is BLOCKED; they run at layer 3.
- **Quarantine** is a strict `xfail` (or a skip): the stage is BLOCKED and lists the reasons, so
  the gate cannot be PASS while one stands. tests/d carries five today (0007 public-listing
  case, two unbuilt provider_dev admissions, the 0011 `state_conflict` vs fake
  `idempotency_conflict` delta, the F2 two-organization case); none records an owner or expiry
  yet - add them to the reason when quarantining.
- pytest stages run with a private `--basetemp` (`mkdtemp`, mode 0700, `$TMPDIR/infrx-e2c-<stage>-*`,
  removed afterwards): pytest < 9.0.3 roots its temporary directories in the shared
  `/tmp/pytest-of-<user>` (GHSA-6w46-j5rx-g56g). `TMPDIR` is deliberately not moved: the D/Q
  harnesses take their host-wide `flock`s under it.

## Namespaces and teardown

Ports, container names and database names come from **one registry**,
`apps/infrx-api/infrx/contracts/tasklocal.py` (the E2 compose layout from
`tests/integration/harness.py`, which `test_harness.py` asserts against it).
`environment.json` only names which namespace a profile uses.

| Namespace | Source | Ports | Containers / DB / objects | Used by |
|---|---|---|---|---|
| `e2c` | tasklocal `e2c` | 55448 PostgreSQL, Valkey and S3 from tasklocal (55474/55475 at efad43e0; 55493/55494 on `claude/consumer-v1`, whose value wins at merge) | `infrx-e2c-postgres` (`infrx_e2c`), `infrx-e2c-valkey`, `infrx-q3-valkey-<valkey port>` (tests/q's name for a non-default port), `infrx-e2c-s3` (started and removed by the gate); buckets `infrx-m1l2` and tests/w's | consumer-local API stages: `INFRX_D_TASK=e2c`, `INFRX_D2_VALKEY_PORT/_CONTAINER`, `INFRX_Q_VALKEY_PORT`, `INFRX_M_S3_ENDPOINT` |
| `e2` | harness `e2` | 55500, 55523, 55532, 55579, 55580, 55590 (PostgREST 55530/55531 at layer 3) | `infrx-e2-*`, `infrx_e2`, `test/e2/`, `infrx_e2:` | consumer-local's `integration-l3` (`run.py --layer 3`), certify.py local |
| `e3c` | harness `e3c` (E2's layout +1400; tasklocal block 56900–56999) | 56900, 56923, 56932, 56979, 56980, 56990 | `infrx-e3c-*`, `infrx_e3c` | E3C's `backend/e3c/runner.py` (consumer-local's last stage) |
| `e2c-selftest` | E2C lane block | 55510–55519 | none (`infrx-e2c-selftest-*` reserved) | `test_preflight.py` binds 55510 |

Setup refuses (BLOCKED) when a namespace port is bound (a listener or a live connection;
a closed connection lingering in `TIME_WAIT` is not busy, the check binds with `SO_REUSEADDR`
as a server does) or a container with its name exists.

**Ephemeral-port overlap (measured).** Every task-local port (tasklocal's 554xx, the E blocks
555xx–569xx) is inside this host's ephemeral range, so any outgoing connection may take one
as its source port. On 2026-09-24 a `make api-test` run lost its D container that way
(`failed to bind host port 127.0.0.1:55432/tcp: address already in use`, 54 D cases failed),
and an E2C test found its own 55510 held as the source port of a connection to 55448.
Preflight reports the exposed ports as `ephemeral-overlap: risk`; it never changes the verdict,
because a collision can only produce a spurious BLOCKED/FAIL, not a pass. The host owner has
since reserved `55432-55499,56379,56700-56999,58123,59000,59100,59110` (P-21,
`/etc/sysctl.d/60-infrx-task-ports.conf`): tasklocal's per-lane ports and the e3b2/e4b/e3c
compose blocks are safe. Still exposed, and reported as a risk by `integration-l3` and
`backend-certify`: E2's own block **55500–55599** (55500, 55523, 55532, 55579, 55580, 55590)
and the E2C self-test block 55510–55519. Adding `55500-55599` to the same file closes it.
Teardown belongs to the runner that created the resource: `pgharness`/`vkstore` remove their
containers at interpreter exit (only ones carrying this checkout's label), `run.py` removes the
`infrx-e2-*` stack in its `finally`. After a crash, list what is left with
`docker ps -a --filter name=infrx-e2c-` and `docker ps -a --filter name=infrx-e2-`; the next
run of the same harness replaces its own labelled leftovers and reports foreign ones.

Known limits (each recorded in the verdict, each a wiring request in the E2C evidence):

- `api-d` leaves out `tests/d/test_signup.py::test_code_mutant_is_killed` and adds an
  `api-d-mutants` **NOT RUN** row while `tests/d/signup_mutants.py`'s Runner declares no `env`:
  the shared mutant runner copies only `Runner.env` into a copy's environment, so those copies
  would resolve pgharness to `d1`'s PostgreSQL (55432, another lane's port, under its host-wide
  lock) whatever the gate exported. The gate reads the Runner file and runs the case in full
  once it names `INFRX_D_TASK` (wiring request E2C-FR-1).
- `run.py --layer 3` reruns `tests/integration` after the backend stage has removed PostgREST,
  so the two journey cases that need it skip there (read from `run.py`, not measured here):
  `integration-l3` stays BLOCKED until `run.py`'s layer-3 suites run leaves out the backend
  suite its own stage already ran.
- `run.py`'s own `make api-test` (without `--only-suites`) pins `INFRX_D_TASK=e3b2d` and
  `INFRX_Q_VALKEY_PORT=55469` (d10's Valkey); the gate never runs it.

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
- 2026-09-25 (E2C fix round): every API suite in consumer-local; the gate starts its own
  MinIO for the S3 cases; runner stages read their reports' skips; the stack stage is layer 3;
  empty parametrizations listed, not skipped; remote certify flags in any argparse spelling;
  the D mutant case NOT RUN until its copies inherit the task; `gates.py`/`preflight.py`
  resolve the checkout through `harness.REPO_ROOT`, so `mutants.py` (`e2cg*`, `e2cp*`: one
  mutant per gate and preflight rule, run by `run.py`'s mutation stage) can copy them; known
  limits above. Evidence `research/plan/evidence/e/E2C-f61d2f0.md` (fix round).
