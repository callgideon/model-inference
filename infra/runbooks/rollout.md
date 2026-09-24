# Rollout — phase 2: the Marlin pilot on the box (checklist)

**Coordinator-run, in this order, after checkpoint 2.** Written by the ROLLOUT-PREP lane,
which ran nothing against the box and applied nothing to hosted; its read-only hosted
inventory, backup, restore rehearsal and dry-runs are in
`research/plan/evidence/i/ROLLOUT-PREP-*.md`. The box scripts are I2B's
([../rollout/README.md](../rollout/README.md), steps under `infra/rollout/steps/`, sent by
`infra/rollout/ssm.sh`); this page fixes the order for this release, adds the hosted backup,
the release route, the settings the merged lanes introduced, the real-bucket check and the
rollback triggers. Rules: [README.md](README.md) (log before you act; names, never values).

```bash
# coordinator host, repository root checked out at the release; every AWS call like this
aws() { env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN aws --region us-east-1 "$@"; }
export RELEASE=${RELEASE:?the checkpoint-2 commit, 40 hex}
PY=apps/infrx-api/.venv/bin/python
HOSTED="host=aws-0-us-east-2.pooler.supabase.com port=5432 user=postgres.fcbnscgsymzdykendbrc dbname=postgres sslmode=require"
```

## 0. Before the window

| # | Check | Pass |
|---|---|---|
| P1 | Gates G1-G4 and G6 of [../rollout/README.md](../rollout/README.md) at `RELEASE` | as there |
| P2 | G5 `apps/infrx-api/deploy/rehearse.sh` | `REHEARSAL PASSED`. On the cutover tree its dev deploy cannot start: `create_app` needs `S3_MEDIA_BUCKET` (answering HeadBucket), `DATABASE_URL` and Valkey in every mode (owner: I / cutover lane) |
| P3 | The in-image pilot probe (G3) on `RELEASE` | `"ok": true`. At the cutover + M1-L2 merge (`0645e65`), fed the real SSM values plus §1's settings, it refused `requires DATABASE_URL` (P4) and `PENDING(W3): infrx.worker.__main__`; with a placeholder DSN the worker root was the **only** refusal - the worker composition root (I2B-R4), on no branch at prep time (checked: backend-impl, cutover-mount, e3b-phase3-bodies, m-pilot-media): **install refuses (exit 2) until it lands** |
| P4 | SSM holds every manifest key (§1): `aws ssm describe-parameters --parameter-filters Key=Name,Option=BeginsWith,Values=/model-inference/ --query 'Parameters[].[Name,Type]'` (names only) | `/model-inference/pg_journal_url` was **absent** at prep time (a coordinator input: the session-pooler DSN of the login role D's 0004 note names); `supabase_url`, `supabase_service_role_key` present, HeadBucket on the media bucket answered |
| P5 | Two G6B keys for the smoke: one scoped, one revoked | pasted at W12 with `read -rs`, never typed |
| P6 | Deployment lock taken; session-record entry: purpose, cost (none beyond the running box), rollback (this page §3) | recorded |

## 1. Settings for this release

`preflight.py` writes the env file; secrets come from SSM (`--param-prefix /model-inference`),
tunables only through `INFRX_SET`. `ENGINE_MAX_NUM_SEQS` travels on its own: 50-install puts
it into `INFRX_SET` itself (default 32, the old box value), and a name given twice is refused.

| Name | Value | Source |
|---|---|---|
| `INFRX_MODE` | `pilot`, explicit (50-install runs install.sh in pilot mode; unset refuses to start since the cutover) | R44 |
| `MODEL_ID`, `MAX_INFLIGHT`, `USAGE_LOG`, `UPSTREAM`, `VALKEY_URL`, `PROCESSING_CACHE_DIR` | installer defaults (`VALKEY_URL=valkey://127.0.0.1:6379/0`, loopback only) | preflight `local_values` |
| `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | SSM `supabase_url`, `supabase_service_role_key` | present |
| `DATABASE_URL` | SSM `pg_journal_url` | **created 2026-09-24T01:03Z (v1, SecureString)** by the coordinator: the session-pooler DSN (`postgres.<ref>@aws-0-us-east-2.pooler.supabase.com:5432/postgres?sslmode=require`, password = `/INFRX-SUPABASE-PROD/db_password`, composed in-process, never on a command line); read-only check: connects, `rolbypassrls` true, member of `service_role` (the pool's `set role service_role` hook works); rollback `aws ssm delete-parameter` |
| `GATEWAY_API_KEY` | never (R51: forbidden in pilot, not read) | - |
| `INFRX_IMAGE`, `INFRX_RELEASE_SHA` | written by install.sh/preflight from the build and `RELEASE` (`--release`); both required in pilot: `infrx_build_info{revision,image}` on `/metrics`, which the edge keeps private (404) | cutover lane |
| `S3_MEDIA_BUCKET` | `llm-bootcamp-641134885443` (prefix default `infrx/`, `S3_ENDPOINT_URL` unset) | session-02, 2026-09-23T20:59Z; the instance role already permits |
| `MAX_VIDEO_SECONDS` | `82` (code default 120) | P-20, W4 phase B |
| `ENGINE_MAX_NUM_SEQS` | `8` (the box unit runs 32 until the release's serve.sh replaces it) | W3/W4 pin |
| `WORKER_CONCURRENCY` | `8` (08 §5 default 10) | W3/W4 pin |
| `ACCOUNTING_REGIME` | default `legacy_usd`, not set: no `credit` deployment before the worker change (D5 IR 5) | D5 |
| `LARGE_BODY_LIMIT` | `8` (code default 2; `LARGE_BODY_THRESHOLD_BYTES` stays 1 MiB). Memory model: a body over the threshold is held four times across the whole of `Ingress.validated` while the slot is held (3.00x through the parse alone; 4.00x at 3, 85 and 95 MiB measured through `validated` by the INTAKE-DRAIN verifier, local tracemalloc), so the worst case is 8 slots x 96 MiB x 4 = 3,072 MiB of the gateway container's 8 GiB (`--memory 8g`), and 8 x 0.59 s (meas. local, one 95 MiB parse) = ~3.6 s of worst-case loop stall; the pilot's clips (0.3-3 MB) cost 8 x 3 MB x 3 = 72 MiB. 8 matches `ENGINE_MAX_NUM_SEQS`/`WORKER_CONCURRENCY`, so a burst is refused by job capacity rather than by the intake gate. ⚠️ TO BE MEASURED: validated by the next certification's overload cell (every refusal a read 429 with Retry-After, no ReadError, gateway RSS under the bound) | INTAKE-DRAIN, 2026-09-24 box overload cell |

| `DATABASE_POOL_MAX_SIZE` | `6` (code default 10), **proposed by I8, interim**: `infra/runbooks/pool_budget.py` computes the session pooler's peak from the deployed knobs - defaults 10 + 10 (+1 CLI) = 21 > 15 slots (the 2026-09-24 `EMAXCONNSESSION`); 6 + 6 + 1 = 13 + 2 headroom = 15 PASS. The worker then waits for a connection at full load (8 runners + 2 preparers + the reaper > 6): watch `DbPoolWaiting`/`DbPoolTimeouts` (pending WR-I8-2). The durable fix is the runtime on the transaction port (WR-I8-1 + D10's login), where 200 clients share the 15 server connections | I8, 2026-09-24 |

```bash
INSTALL_ARGS=(RELEASE="$RELEASE" ENGINE_MAX_NUM_SEQS=8
  INFRX_SET="S3_MEDIA_BUCKET=llm-bootcamp-641134885443 MAX_VIDEO_SECONDS=82 WORKER_CONCURRENCY=8 LARGE_BODY_LIMIT=8")
# I8 (proposed, interim until WR-I8-1): add DATABASE_POOL_MAX_SIZE=6 to INFRX_SET
```

## 2. The window

Each row: what runs, what proves it, and the way back. Box rows are
`infra/rollout/ssm.sh infra/rollout/steps/<step> NAME=VALUE…`; record every command id.

| # | Where | Step | Verify | Rollback |
|---|---|---|---|---|
| W1 | host → box | **Release to the box** (outside the window): `apps/infrx-api/deploy/release-bundle.sh "$RELEASE"` then `infra/rollout/ssm.sh <out>/$RELEASE.fetch.sh`; then `20-prepull.sh RELEASE=$RELEASE` (its `git fetch origin` works too: origin is anonymously readable) | `sha256sum -c` OK; `release … is in …`; the engine digest pulled | `git update-ref -d refs/infrx/releases/$RELEASE` on the box; nothing else changed |
| W2 | host | Root-volume snapshot ([../rollout/README.md](../rollout/README.md) step 1) **[cost]** | snapshot id `completed` | - |
| W3 | box | `10-inventory.sh` (read-only) | output recorded | - |
| W4 | box | **Save the live edge**: `25-save-edge.sh` - the box lane's pattern, `/opt/dlami/nvme/w4-logs/Caddyfile.live-<utc>` | path + sha256 recorded | - |
| W5 | box | **Maintenance edge, then drain**: `30-pause.sh RELEASE=$RELEASE` (the release's edge + `drain.sh pause`) | public `/health` 503 + `Retry-After`; the monolith stopped | `drain.sh resume` from `/root/infrx-deploy-$RELEASE/apps/infrx-api/deploy`, then `93-restore-edge.sh SAVED=… SAVED_SHA256=…` |
| W6 | host | **Hosted backup, fresh, and its restore check** (block below) - after the drain, so nothing writes between it and W7 | `"equal": true`; `SHA256SUMS` recorded; the copy's `migrate.py plan` digest | read-only: nothing to undo. Check red → stop: `91-abort.sh`, `93-restore-edge.sh` |
| W7 | host | **Hosted migrations** (block below): `plan`, which must print the copy's digest, then `apply --expect` | `nothing pending`; flags and drift as on the copy | exit 2/3: nothing changed → `91-abort.sh` + `93-restore-edge.sh`. Exit 4, or a problem after commit: [restore.md A8](restore.md#a8-then-and-only-then-the-hosted-apply) (maintenance, never a hand edit) |
| W7b | host | **Operator seed** (first pilot install only; idempotent): the Marlin catalog `apps/infrx-api/infrx/state/seed_marlin_provisional.sql` applied from the release tree with the DSN in-process (psycopg; no psql on the host) - `pilot.price_check` needs the public deployment and an active card, else the gateway refuses `unreachable at startup: price_source` (2026-09-24, first window) | rows: provider_orgs 1, serving_versions 1, endpoints 2, deployment_revisions 2 (one public/active), rate_card_versions 1 (provisional, P-01); a second run changes nothing | delete the seed's fixed-identity rows |
| W7c | host | **USD price version** (first pilot install only; idempotent): `infrx.price_versions` has no product writer (R45: the store's price source); insert `pv_<model>_usd_<yyyy_mm>` for `model_revision` = the request's model string (`nemostation/marlin-2b`), currency USD, the rates of `public.models` (the monolith's live USD prices - no new pricing decision), `token_rules_version` as the test stack's (`tr-1`), `effective_from infrx.now()`, `created_by operator-seed` - else the legacy_usd admission refuses every request with the sanitized `invalid_request` "no price snapshot for the requested model" (2026-09-24) | one row; `select * from infrx.price_versions` | `effective_to = now()` on the row |
| W7d | host | **Consumer keys need the signup grant**: `infrx.feature_flags.signup_grant` must be `true` (A1's grant provisions the personal wallet that `issue-key` requires; CREDIT admission stays off), and in the legacy_usd regime a tenant needs a USD balance: a `grant` row in `public.credit_ledger` (R103: the legacy writer; `infrx.ledger_moves_wallet` applies it). The operator key: `infrx.bootstrap_operator_key(org, name, prefix, sha256hex, actor, reason)` with the secret in SSM `/model-inference/operator_key` only (2026-09-24) | `grant`/`issue-key` succeed through `python -m infrx.operations.cli` | `revoke-key`; a negative `adjustment` row; the flag back to false |
| W7e | host | **One price version per model string the clients send** (first pilot install only; idempotent): the USD admission keys `infrx.price_versions` by the request's literal model string (R45; not by the resolved revision), so a client that sends the labelled alias (`nemostation/marlin-2b@2026-09-01` - the E4B certify client, the E1B bench with `--model`) is refused `400` with no price version even though the unlabelled alias has one. Seed a row per alias form the pilot's clients use, same rates and `token_rules_version` as W7c. Better: D/G key the lookup on the resolved revision (ruling pending; 2026-09-24 box certification, run1) | 
| W8 | box | `40-checkout.sh RELEASE=$RELEASE` - from here to W10 no engine restart | HEAD = `RELEASE` | `91-abort.sh` returns the previous checkout |
| W9 | box | **Real-bucket check**: `45-s3-check.sh RELEASE=$RELEASE` (tests/m/test_s3.py, instance role, image built from `RELEASE`) - before the install, because install.sh opens the edge itself once ready. The role needs Get/Put/Delete on `<bucket>/test/m1l2/*` (and `s3:ListBucket` for that prefix: the cases list); the bootcamp role allows the whole bucket | `passed`, no failure; `test/m1l2/` empty afterwards | red → `91-abort.sh` + `93-restore-edge.sh` (nothing installed) |
| W10 | box | **Install**: `TIMEOUT_S=3600 infra/rollout/ssm.sh infra/rollout/steps/50-install.sh "${INSTALL_ARGS[@]}" MIGRATION_DIGEST=<W7 digest>` - image, preflight (SSM + host HeadBucket), units, engine restart (start-to-ready meas. 168-181 s on this box, `ENGINE_READY_S` 900), gateway + worker `/readyz`, **then the edge goes live** | exit 0, `deployed …`, backup dir recorded | exit 2 → R1; exit 4 → R2 |
| W11 | box | `60-verify-local.sh` | units active, `/readyz` 200, least privilege as applied, `INFRX_MODE=pilot`, no `GATEWAY_API_KEY` | R2 |
| W12 | host | **Smoke**: `read -rs INFRX_TEST_KEY; read -rs INFRX_REVOKED_KEY; export INFRX_TEST_KEY INFRX_REVOKED_KEY` then `infra/rollout/verify-external.sh` (+ `LEGACY_KEY` as step 10 there) | `failures: 0`, `PENDING` lines are not passes | before any pilot request was accepted: R2; after: R3 |
| W13 | host | Record `RELEASE`, image id, engine digest, snapshot, backup dir, `SHA256SUMS`, migration digest, command ids; release the lock | - | - |

### W4/W5 and the abort — the edge swap (the box lane's pattern)

What `25-save-edge.sh` and `93-restore-edge.sh` do, as the box lane ran it by hand
(session-02 record, `codex/box-measure` d35a2c8 and its close at eea17d8):

```bash
# box, as root, before the maintenance site goes in
saved=/opt/dlami/nvme/w4-logs/Caddyfile.live-$(date -u +%Y%m%dT%H%M%SZ)
cp -p /etc/caddy/Caddyfile "$saved"; sha256sum "$saved"        # record path + sha256
# ... the window (maintenance site, admin on unix//config/admin.sock) ...
# restore: check, rewrite in place (the single-file bind mount keeps its inode), reload
echo "<sha256>  $saved" | sha256sum -c - && cat "$saved" > /etc/caddy/Caddyfile
docker exec caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile --address unix//config/admin.sock
```

The reload is addressed to the admin endpoint of the config that is **running** (the
release's sites put it on the socket); the restored live file moves admin back to
`localhost:2019`, so any later manual reload uses the default address. Measured there: the
restore brought the live file back (sha256 `ff47f706…` on the host and in the container) and
public `/health` 200 within 8 s; each engine restart was ready in 168-171 s, and the
start-to-ready across the lane 168-181 s (the I2B figure `ENGINE_READY_S` 900 bounds it).

### W9 from the coordinator host — the same conformance with the operator's credentials

Before the window, from a checkout at `RELEASE` (the box step then only proves the role):

```bash
cd apps/infrx-api && uv sync --frozen --all-extras      # = make api-env; botocore is the traces extra
env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN -u INFRX_M_S3_LOCAL_CREDS \
  AWS_DEFAULT_REGION=us-east-1 INFRX_M_S3_ENDPOINT=https://s3.us-east-1.amazonaws.com \
  INFRX_M_S3_BUCKET=llm-bootcamp-641134885443 uv run --frozen pytest -q -rs tests/m/test_s3.py
```

At prep (tree `0645e65`, default profile): **40 passed in 91 s**, `test/m1l2/` empty after.
A venv without the extras fails every S3 case at `import botocore` before any S3 call. The
in-image mechanism of `45-s3-check.sh` (the five hash-pinned wheels, `--network host`) ran
locally on the same tree's image without an endpoint: 25 passed, 15 skipped (M1-L2's count).

### W6 — the fresh hosted backup and its restore check

`restore.md` A1-A6 with one addition, measured by this lane: the pinned image's template has
only its own `auth.users`, while hosted's auth schema is GoTrue's (`auth.identities` and 80+
migrations), so a restore into the bare template fails at `auth.identities`. GoTrue
`v2.197.0` - whose migration head `20260831180000` is hosted's - migrates the template first.
Port 55697 is this lane's; any free loopback port works.

```bash
BACKUP="$HOME/infrx-backups/hosted-$(date -u +%Y%m%dT%H%M%SZ)"
read -r PGPASSWORD < <(aws ssm get-parameter --name /INFRX-SUPABASE-PROD/db_password \
                        --with-decryption --query Parameter.Value --output text); export PGPASSWORD
$PY infra/runbooks/pgrestore.py dump --conninfo "$HOSTED" --out "$BACKUP"     # prints SHA256SUMS
IMAGE=$($PY -c 'import runpy; print(runpy.run_path("infra/runbooks/pgrestore.py")["IMAGE"])')
GOTRUE=supabase/gotrue@sha256:1736a63078f5922b198c4cbe50f80ab9a2d3b54fe8b7b6cfb2e9dc5dbbc12c6b
LOCALPW=infrx-rollout-local                    # a throwaway local literal, as restore.md A4
docker run -d --name infrx-rollout-restore -p 127.0.0.1:55697:5432 -e POSTGRES_PASSWORD="$LOCALPW" "$IMAGE"
until $PY -c "import psycopg; psycopg.connect('host=127.0.0.1 port=55697 user=postgres password=$LOCALPW dbname=postgres connect_timeout=3').close()" 2>/dev/null; do sleep 1; done
sb() { docker exec infrx-rollout-restore psql -q -U supabase_admin -d "$1" -v ON_ERROR_STOP=1 -c "$2"; }
sb postgres "alter role supabase_auth_admin with password '$LOCALPW'"
docker run --rm --network host -e GOTRUE_DB_DRIVER=postgres \
  -e DATABASE_URL="postgres://supabase_auth_admin:$LOCALPW@127.0.0.1:55697/postgres?sslmode=disable" \
  -e GOTRUE_JWT_SECRET=rehearsal-only-literal-not-a-secret-0000 -e GOTRUE_SITE_URL=http://localhost \
  -e API_EXTERNAL_URL=http://localhost "$GOTRUE" auth migrate 2>&1 | tail -1
for _ in 1 2 3 4 5; do                          # pg_cron reattaches to the template within ms
  sb template1 "select pg_terminate_backend(pid) from pg_stat_activity where datname = 'postgres' and pid <> pg_backend_pid()" >/dev/null
  sb template1 "create database infrx_rollout_copy template postgres owner postgres" && break; sleep 0.3
done
LOCAL="host=127.0.0.1 port=55697 user=postgres password=$LOCALPW dbname=infrx_rollout_copy sslmode=disable"
$PY infra/runbooks/pgrestore.py restore --conninfo "$LOCAL" --from "$BACKUP"
$PY infra/runbooks/pgrestore.py check --source "$HOSTED" --target "$LOCAL"   # exit 0, "equal": true
# The apply, on the copy first. The backup has no supabase_migrations schema: its rows are the
# `applied:` line hosted's own plan prints (today 0001 init, 0002 seed_models).
docker exec infrx-rollout-restore psql -q -U postgres -d infrx_rollout_copy -v ON_ERROR_STOP=1 \
  -c "create schema supabase_migrations" \
  -c "create table supabase_migrations.schema_migrations (version text primary key, statements text[], name text)" \
  -c "insert into supabase_migrations.schema_migrations (version, name) values ('0001', 'init'), ('0002', 'seed_models')"
export MIGRATE_DATABASE_URL="postgresql://postgres:$LOCALPW@127.0.0.1:55697/infrx_rollout_copy"
COPY_DIGEST=$($PY apps/infrx-api/deploy/migrate.py plan | sed -n 's/^plan digest: //p'); echo "$COPY_DIGEST"
$PY apps/infrx-api/deploy/migrate.py apply --expect "$COPY_DIGEST"
docker exec -i infrx-rollout-restore psql -U postgres -d infrx_rollout_copy -v ON_ERROR_STOP=1 <<'SQL'
select name, enabled from infrx.feature_flags order by name;
select count(*) as drift_rows from infrx.wallet_reconciliation where ledger_drift <> 0 or reserved_drift <> 0;
SQL
unset MIGRATE_DATABASE_URL
docker rm -f infrx-rollout-restore >/dev/null
```

Pass: `check` exit 0 with `"equal": true`; `apply` prints every pending version; flags
`credit_admission` f, `legacy_usd_admission` t, `signup_grant` f; `drift_rows` 0. Retention of
`$BACKUP`: [restore.md A9](restore.md#a9-clean-up).

### W7 — the hosted apply

`PGPASSWORD` is still exported from W6; libpq reads it, so the DSN carries no secret.
README step 6 runs the same `migrate.py` inside `infrx-runtime:$RELEASE`; either is the file at
`RELEASE`.

```bash
export MIGRATE_DATABASE_URL="$HOSTED"
$PY apps/infrx-api/deploy/migrate.py plan        # "applied: 0001 init, 0002 seed_models", digest == $COPY_DIGEST
$PY apps/infrx-api/deploy/migrate.py apply --expect "$COPY_DIGEST"
$PY apps/infrx-api/deploy/migrate.py plan        # nothing pending
unset MIGRATE_DATABASE_URL PGPASSWORD
```

At prep time (0018 at `8554b47`) hosted's plan listed **0003-0018, sixteen files**, digest
`4524cbc0…`, the same digest as the restored copy's; a changed 0018 changes it.

## 3. Rollback triggers

| Trigger | Action |
|---|---|
| W6 check not equal, or the copy's apply fails | Stop before any hosted write: `91-abort.sh`, `93-restore-edge.sh` |
| W7 plan digest ≠ `$COPY_DIGEST`, or `apply` exit 2/3 | Nothing changed: the same abort |
| W7 `apply` exit 4, or anything wrong after it committed | Maintenance stays; [restore.md A8](restore.md#a8-then-and-only-then-the-hosted-apply). The migrations are additive: the monolith's own statements ran on the migrated copy (W6), so the abort path above still serves |
| W9 red (bucket, role, AWS semantics) | Nothing installed: the same abort |
| W10 exit 2 (preflight refused: a missing key, P3/P4) | R1 of [../rollout/README.md](../rollout/README.md): `91-abort.sh`, then `93-restore-edge.sh` |
| W10 exit 4 (the runtime never became ready; the edge unchanged) | `90-revert.sh RELEASE=$RELEASE BACKUP=<the backup dir the install printed>` and, when no pilot request was ever accepted (the edge never switched), `ROLLBACK_TO_UNMETERED=no-pilot-request-was-accepted`; then `93-restore-edge.sh`. If the running Caddy then answers only on `localhost:2019` (the socket-addressed steps fail with `dial unix /config/admin.sock`), reload the live file once with `docker exec caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile --address localhost:2019` (2026-09-24) |
| W10 exit 4, W11 or W12 red, **no pilot request accepted** | R2: `90-revert.sh` (runs `rollback.sh` on install.sh's backup, engine first), after the read-only zero count of pilot jobs/ledger rows since W10 |
| Pilot requests were accepted and it must stop | R3: `95-maintenance.sh`; `rollback.sh` refuses the unmetered monolith |
| The host itself | R4: root-volume swap to the W2 snapshot ([restore.md](restore.md#box-snapshot)) |

## 4. Continuous operations (I8) — after the release that carries I8

Each row is one coordinator op, logged first (README rule 1), serialized after any running
certification. Box rows: `infra/rollout/ssm.sh infra/rollout/steps/<step> NAME=VALUE…`.
Nothing here has run; every row's output goes into the I8 evidence record.

| # | Where | Op | Pass | Blocked on |
|---|---|---|---|---|
| O1 | host | `apps/infrx-api/.venv/bin/python infra/runbooks/pool_budget.py --runtime-port 5432 --set DATABASE_POOL_MAX_SIZE=6` (and without `--set`: the FAIL that explains EMAXCONNSESSION) | `PASS session: peak 13` | - |
| O2 | host | the release with I8 through W1-W13, `DATABASE_POOL_MAX_SIZE=6` in `INFRX_SET` (§1). New: the runtime units run `preflight.py envcheck` before every start (the journal names a refused setting) | W12 `failures: 0` | - |
| O3 | box | `71-pool-budget.sh` | exit 0, `PASS session` | - |
| O4 | box | `72-observe-install.sh RELEASE=$RELEASE CANARY_VIDEO=<in-cap clip on the box>` [+ `ALERT_WEBHOOK_PARAM=<ssm name> ALERT_OWNER=<who> ALERT_ESCALATION=<how>`] [+ `MONITOR_DSN_PARAM=<D10's read-only DSN name>`] | timers listed; first cycle exit 0 or 3 (delivery BLOCKED) | P-25 (destination); D10 (monitor login) |
| O5 | box | `73-observe-status.sh` two minutes later | `infrx_durable_up 1`, `infrx_canary_up` 1 for text and video, no unexpected firing | - |
| O6 | box | `74-alert-test.sh`, the owner confirms the nonce, then `74-alert-test.sh RESOLVE=<nonce>` | `http=2xx` twice + the owner's confirmation | **P-25** |
| O7 | host | `read -rs PROBE_DATABASE_URL` (the runtime login's DSN on :6543) then `privilege_probe.py --role <login> --pooler-semantics` | today: FAIL (the `postgres` login is privileged - the baseline); after D10: PASS with its `--allow-functions` list | D10 |
| O8 | host | `read -rs SUPABASE_ACCESS_TOKEN` then `supabase_policy.py` | the backup/PITR facts, the pooler's `max_client_conn` (feeds `pool_budget.py --txn-client-limit`) | the token |
| O9 | box | `80-mirror-artifacts.sh RELEASE=$RELEASE MIRROR_URL=<approved prefix>` ([restore.md](restore.md#model-artifacts)) | `N/N match`, manifest read back equal | the prefix approval (P-25) |
| O10 | box | `81-restore-artifacts.sh RELEASE=$RELEASE MIRROR_URL=<prefix> MODE=fetch CLEANUP=1` | `EQUAL`, `fetch_s`, `verify_s` | O9 |
| O11 | box | **window** `81-restore-artifacts.sh ... MODE=swap`, then host `verify-journey.sh` + `drift.py --request-id` | the seven timings; journey `failures: 0`; `SETTLED` | O10 + authorization |
| O12 | both | **window** the rollback drill, [rollback.md](rollback.md#known-good-rollback-drill) steps 1-7 (target bda1586 or 4226315) | both journeys `failures: 0`, both `SETTLED` | authorization |
| O13 | box | `79-evidence-export.sh` | one JSON document, saved to the evidence record | - |
| O14 | box | `86-cleanup.sh` (dry run), then `86-cleanup.sh DRY_RUN=0` after logging | only allowlisted paths listed/removed | - |

## Verification log

- 2026-09-23 (ROLLOUT-PREP): written; W1's script and the W6 block were run by the lane
  (backup, GoTrue-migrated template, restore, check equal, copy apply of 0003-0018), W7's
  `plan` read-only against hosted. Nothing ran against the box; no hosted write.
- 2026-09-23T23:30Z (ROLLOUT-PREP, resumed): W1 round trip through the real bucket
  (`releases/test-20260923T231945Z.{bundle,sha256}`, 12.5 MB, fetched by the box step into
  an empty repository on the coordinator host, then deleted; the bucket is unversioned);
  the edge-swap and W9 blocks added; W9's host command run on the real bucket (40 passed);
  P3/P4 re-checked by a preflight dry-run on `0645e65` with the real SSM values (names only
  printed). Steps 25/45/93 tested against stubs (`tests/i/test_rollout.py`, 8 mutants).
- 2026-09-24 (coordinator): `/model-inference/pg_journal_url` created (P4 closed); the login is the project's `postgres.<ref>` pooler login (a dedicated login role stays a follow-up: 0004 creates none).
- 2026-09-24 (coordinator, first pilot window): W7b operator seed added (the first install refused on `price_source`); the W10-exit-4 revert row (90-revert's BACKUP and ROLLBACK_TO_UNMETERED, the admin-socket reload); the box checkout refuses untracked files (moved aside on the same filesystem). Second window deployed 27af05a in 4 minutes.
- 2026-09-24 (coordinator, second window): W7c (USD price version) and W7d (signup grant flag, USD balance, operator key) added from the smoke's findings; the smoke then passed 15/15 and text + video jobs settled with engine-exact counts.
- 2026-09-24 (coordinator, box certification): W7e added - the first box certification run refused every request because only the unlabelled alias had a price version (the certify client sends the labelled one); the rerun admits after the second row.
- 2026-09-24 (coordinator, INTAKE-DRAIN merge): the LARGE_BODY_LIMIT row's memory figures corrected from the verifier's measurement (4x across `Ingress.validated`: 3,072 MiB and ~4.7 s worst case at 8 slots; still inside 8 GiB).
- 2026-09-24 (I8): §1 gains the interim `DATABASE_POOL_MAX_SIZE=6` row (pool_budget.py);
  §4 lists the continuous-operations ops O1-O14 with their pass criteria and blockers. Not
  run.
