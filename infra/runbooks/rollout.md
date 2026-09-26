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
it into `INFRX_SET` itself and refuses to start without it (no default since ROLLOUT-FIXES;
it was 32, the old box value), and a name given twice is refused.

| Name | Value | Source |
|---|---|---|
| `INFRX_MODE` | `pilot`, explicit (50-install runs install.sh in pilot mode; unset refuses to start since the cutover) | R44 |
| `MODEL_ID`, `MAX_INFLIGHT`, `USAGE_LOG`, `UPSTREAM`, `VALKEY_URL`, `PROCESSING_CACHE_DIR` | installer defaults (`VALKEY_URL=valkey://127.0.0.1:6379/0`, loopback only) | preflight `local_values` |
| `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | SSM `supabase_url`, `supabase_service_role_key` | present |
| `DATABASE_URL` | SSM `pg_journal_url` | **created 2026-09-24T01:03Z (v1, SecureString)** by the coordinator: the session-pooler DSN (`postgres.<ref>@aws-0-us-east-2.pooler.supabase.com:5432/postgres?sslmode=require`, password = `/INFRX-SUPABASE-PROD/db_password`, composed in-process, never on a command line); read-only check: connects, `rolbypassrls` true, member of `service_role` (the pool's `set role service_role` hook works); rollback `aws ssm delete-parameter`. **After W10b** the env file's `DATABASE_URL` is 0021's dedicated `infrx_runtime` login on the transaction pooler (:6543) and `MONITOR_DATABASE_URL` the `infrx_monitor` login (`55-runtime-login.sh`, RV-09, R127); `pg_journal_url` stays the owner login that step and the certify ledger half read |
| `GATEWAY_API_KEY` | never (R51: forbidden in pilot, not read) | - |
| `INFRX_IMAGE`, `INFRX_RELEASE_SHA` | written by install.sh/preflight from the build and `RELEASE` (`--release`); both required in pilot: `infrx_build_info{revision,image}` on `/metrics`, which the edge keeps private (404) | cutover lane |
| `S3_MEDIA_BUCKET` | `llm-bootcamp-641134885443` (prefix default `infrx/`, `S3_ENDPOINT_URL` unset) | session-02, 2026-09-23T20:59Z; the instance role already permits |
| `MAX_VIDEO_SECONDS` | `82` (code default 120) | P-20, W4 phase B |
| `ENGINE_MAX_NUM_SEQS` | `8` (the box unit runs 32 until the release's serve.sh replaces it) | W3/W4 pin |
| `WORKER_CONCURRENCY` | `8` (08 §5 default 10) | W3/W4 pin |
| `ACCOUNTING_REGIME`, `ACTIVE_RATE_CARD_VERSION` | `credit` and `rc_marlin2b_20260925_launch` (the P-01 card) for the E4C candidate: P-17 check 3 needs the CREDIT regime with the P-01 card. **Order (R133, G8 activation steps 1-4 in `research/plan/evidence/g/G8-6a075c5.md`):** the card is published and CREDIT activated on hosted (W7f) **before** W10 installs these two. What happens otherwise (code at 6cbb6a45): `credit` with no card version - preflight's probe runs `validate_runtime`, which refuses `ACTIVE_RATE_CARD_VERSION` (`infrx/config.py:249-251`), so 50-install exit 2 (R1); a card version that is not the public deployment's active card on hosted - the gateway's `price_source` probe is false (`infrx/gateway/routes/models.py:187-208`) and a pilot refuses to start (`unreachable at startup: price_source`, `routes/ingress.py:106-114`), so 50-install exit 4 (R2); the card active but CREDIT not activated (`credit_admission` off) - the gateway reads ready, but `infrx.require_feature` refuses every admission (SQLSTATE 55000, 503 `dependency_unavailable` with Retry-After 30, `infrx/state/jobstore.py:116-117`), so W12 fails (R2). History: until 2026-09-26 this row read "default `legacy_usd`, not set: no `credit` deployment before the worker change (D5 IR 5)"; the worker change is in (`infrx/worker/__main__.py:139`, `CreditWork`) | P-01, P-17, G8; D5 (history) |
| `LARGE_BODY_LIMIT` | `8` (code default 2; `LARGE_BODY_THRESHOLD_BYTES` stays 1 MiB). Memory model: a body over the threshold is held four times across the whole of `Ingress.validated` while the slot is held (3.00x through the parse alone; 4.00x at 3, 85 and 95 MiB measured through `validated` by the INTAKE-DRAIN verifier, local tracemalloc), so the worst case is 8 slots x 96 MiB x 4 = 3,072 MiB of the gateway container's 8 GiB (`--memory 8g`), and 8 x 0.59 s (meas. local, one 95 MiB parse) = ~3.6 s of worst-case loop stall; the pilot's clips (0.3-3 MB) cost 8 x 3 MB x 3 = 72 MiB. 8 matches `ENGINE_MAX_NUM_SEQS`/`WORKER_CONCURRENCY`, so a burst is refused by job capacity rather than by the intake gate. ⚠️ TO BE MEASURED: validated by the next certification's overload cell (every refusal a read 429 with Retry-After, no ReadError, gateway RSS under the bound) | INTAKE-DRAIN, 2026-09-24 box overload cell |

| `DATABASE_POOL_MAX_SIZE` | `6` (code default 10), **proposed by I8, interim**: `infra/runbooks/pool_budget.py` computes the session pooler's peak from the deployed knobs - defaults 10 + 10 (+1 CLI) = 21 > 15 slots (the 2026-09-24 `EMAXCONNSESSION`); 6 + 6 + 1 = 13 + 2 headroom = 15 PASS. The worker then waits for a connection at full load (8 runners + 2 preparers + the reaper > 6): watch `DbPoolWaiting`/`DbPoolTimeouts` (pending WR-I8-2). The durable fix is the runtime on the transaction port (WR-I8-1 + D10's login), where 200 clients share the 15 server connections | I8, 2026-09-24 |

```bash
INSTALL_ARGS=(RELEASE="$RELEASE" ENGINE_MAX_NUM_SEQS=8
  INFRX_SET="S3_MEDIA_BUCKET=llm-bootcamp-641134885443 MAX_VIDEO_SECONDS=82 WORKER_CONCURRENCY=8 LARGE_BODY_LIMIT=8 DATABASE_POOL_MAX_SIZE=6 ACCOUNTING_REGIME=credit ACTIVE_RATE_CARD_VERSION=rc_marlin2b_20260925_launch")
# DATABASE_POOL_MAX_SIZE=6: I8's interim pin until WR-I8-1 (pool_budget.py: 21 > 15 at defaults, 13 at 6)
# ACCOUNTING_REGIME/ACTIVE_RATE_CARD_VERSION: only after W7f activated CREDIT with this card on hosted
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
| W7d | host | **Consumer keys need the signup grant**: `infrx.feature_flags.signup_grant` must be `true` (A1's grant provisions the personal wallet that `issue-key` requires; CREDIT admission stays off), and in the legacy_usd regime a tenant needs a USD balance: a `grant` row in `public.credit_ledger` (R103: the legacy writer; `infrx.ledger_moves_wallet` applies it). The operator key: `infrx.bootstrap_operator_key(org, name, prefix, sha256hex, actor, reason)` with the secret in SSM `/model-inference/operator_key` only (2026-09-24) | `grant`/`issue-key` succeed through `python -m infrx.operations.cli` with `OPERATIONS_DATABASE_URL` exported (`read -rs`; the owner or broad login - the CLI refuses the dedicated `infrx_runtime`/`infrx_monitor` logins, OPS-CLI-DSN) | `revoke-key`; a negative `adjustment` row; the flag back to false |
| W7e | host | **One price version per model string the clients send** (first pilot install only; idempotent): the USD admission keys `infrx.price_versions` by the request's literal model string (R45; not by the resolved revision), so a client that sends the labelled alias (`nemostation/marlin-2b@2026-09-01` - the E4B certify client, the E1B bench with `--model`) is refused `400` with no price version even though the unlabelled alias has one. Seed a row per alias form the pilot's clients use, same rates and `token_rules_version` as W7c. Better: D/G key the lookup on the resolved revision (ruling pending; 2026-09-24 box certification, run1) | 
| W7f | host | **CREDIT activation** (after W7: it calls 0022's `infrx.set_feature_flag`; before W10, which installs `ACCOUNTING_REGIME=credit`, §1): [E4C-runbook §1a](../../models/marlin2b/results/E4C-runbook.md#1a-after-the-hosted-apply-before-w8) H1 `publish-card` (P-01), H2 `credit-transition --dry-run` then `credit-transition --card` (P-02, G8). The edge has served maintenance since W5, so no admission falls between the flag flip and the install (`--freeze-only` is not needed) | H2's dry-run `drift == []`; the flags `credit_admission` t, `legacy_usd_admission` f, `signup_grant` t (`transition.py` `ENABLE`) | `credit-transition --to legacy_usd` (PI P-02; §3: its keys and its drill); the card is immutable (P-01) |
| W8 | box | `40-checkout.sh RELEASE=$RELEASE` - from here to W10 no engine restart | HEAD = `RELEASE` | the W7f reversal (§3), then `91-abort.sh` returns the previous checkout |
| W9 | box | **Real-bucket check**: `45-s3-check.sh RELEASE=$RELEASE` (tests/m/test_s3.py, instance role, image built from `RELEASE`) - before the install, because install.sh opens the edge itself once ready. The role needs Get/Put/Delete on `<bucket>/test/m1l2/*` (and `s3:ListBucket` for that prefix: the cases list); the bootcamp role allows the whole bucket | `passed`, no failure; `test/m1l2/` empty afterwards | red → the W7f reversal (§3), then `91-abort.sh` + `93-restore-edge.sh` (nothing installed) |
| W10 | box | **Install**: `TIMEOUT_S=3600 infra/rollout/ssm.sh infra/rollout/steps/50-install.sh "${INSTALL_ARGS[@]}" MIGRATION_DIGEST=<W7 digest>` - image, preflight (SSM + host HeadBucket), units, engine restart (start-to-ready meas. 168-181 s on this box, `ENGINE_READY_S` 900), gateway + worker `/readyz`, **then the edge goes live** | exit 0, `deployed …`, backup dir recorded | the W7f reversal (§3) first, then exit 2 → R1; exit 4 → R2 |
| W10b | box | **Dedicated runtime logins** (RV-09, R127; after W7, which created them, and after every W10, which rewrites the env file from SSM): `infra/rollout/ssm.sh infra/rollout/steps/55-runtime-login.sh` - reads `/model-inference/infrx_runtime_password`, `/model-inference/infrx_monitor_password` and `pg_journal_url` by name, sets both passwords on hosted through the owner login, logs in as each on :6543, then `DATABASE_URL` = `infrx_runtime` and `MONITOR_DATABASE_URL` = `infrx_monitor` in the env file (envcheck first), gateway + worker restarted; then `MONITOR_DATABASE_URL` alone into `/etc/infrx-observe.env` (0600, by rename), which observe.sh's durable exporter reads first - its fallback, the env file's `DATABASE_URL`, is now `infrx_runtime`, which 0021 grants none of durable.py's tables. Needs the two parameters (coordinator-created, as README step 2) | `runtime on infrx_runtime, gauges on infrx_monitor`; `/readyz` 200 on both; a rerun prints `unchanged` | exit 4 puts the previous env file back itself; else the saved file it names, or rerun 50-install |
| W11 | box | `60-verify-local.sh` | units active, `/readyz` 200, least privilege as applied, `INFRX_MODE=pilot`, no `GATEWAY_API_KEY` | the W7f reversal (§3), then R2 |
| W12 | host | **Smoke**: `read -rs INFRX_TEST_KEY; read -rs INFRX_REVOKED_KEY; export INFRX_TEST_KEY INFRX_REVOKED_KEY` then `infra/rollout/verify-external.sh` (+ `LEGACY_KEY` as step 10 there) | `failures: 0`, `PENDING` lines are not passes | before any pilot request was accepted: the W7f reversal (§3), then R2; after: R3 |
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
# `applied:` line of hosted's own read-only `plan`, read here at the window, never typed (the
# prep-time literal 0001/0002 went stale when hosted reached 0018). Hosted today: 0001-0018.
export MIGRATE_DATABASE_URL="$HOSTED"                  # read-only; PGPASSWORD is exported above
HOSTED_APPLIED=$($PY apps/infrx-api/deploy/migrate.py plan | sed -n 's/^applied: //p'); echo "$HOSTED_APPLIED"
SEED=$(sed 's/, /\n/g' <<<"$HOSTED_APPLIED" | sed -E "s/^([0-9]{4}) ?(.*)$/('\1', '\2')/" | paste -sd, -)
docker exec infrx-rollout-restore psql -q -U postgres -d infrx_rollout_copy -v ON_ERROR_STOP=1 \
  -c "create schema supabase_migrations" \
  -c "create table supabase_migrations.schema_migrations (version text primary key, statements text[], name text)" \
  -c "insert into supabase_migrations.schema_migrations (version, name) values $SEED"
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

Pass: `HOSTED_APPLIED` is `0001 init, …, 0018 terminal_settlement` (0001-0018,
[20-platform-handoff](../../research/plan/20-platform-handoff-2026-09-24.md) "Hosted Supabase"),
anything else is a hosted change nobody recorded: stop; `check` exit 0 with `"equal": true`;
`apply` prints every pending version (0019-0026 on this tree); flags as hosted's own at 0018:
`credit_admission` f, `legacy_usd_admission` t, `signup_grant` t (the prep-time `signup_grant` f
was a 0002 hosted with no flag rows); `drift_rows` 0. Retention of
`$BACKUP`: [restore.md A9](restore.md#a9-clean-up).

### W7 — the hosted apply

`PGPASSWORD` is still exported from W6; libpq reads it, so the DSN carries no secret.
README step 6 runs the same `migrate.py` inside `infrx-runtime:$RELEASE`; either is the file at
`RELEASE`.

```bash
export MIGRATE_DATABASE_URL="$HOSTED"
$PY apps/infrx-api/deploy/migrate.py plan        # applied: == $HOSTED_APPLIED (W6), digest == $COPY_DIGEST
$PY apps/infrx-api/deploy/migrate.py apply --expect "$COPY_DIGEST"
$PY apps/infrx-api/deploy/migrate.py plan        # applied: 0001 … 0026, nothing pending
unset MIGRATE_DATABASE_URL PGPASSWORD
```

At prep time (0018 at `8554b47`) hosted's plan listed **0003-0018, sixteen files**, digest
`4524cbc0…`, the same digest as the restored copy's; a changed 0018 changes it.

For the E4C candidate hosted goes from 0001-0018 to **0001-0026**: 0019-0023 are on the
integration tip, 0024/0025 land from `codex/d10-merge-2`, and this page is written for a
release tree that has them. On a tree that stops at 0023 the applied set ends there and E4C's
freeze refuses (`migration_version` 0025 or newer, E4C-runbook §2). The known-good proof
reaches 0025 (KNOWN-GOOD-PROOF-2; §3 Known-good record): extend it to 0026 before relying on R2 after a 0026 apply.

## 3. Rollback triggers

**The W7f reversal.** From W7f on, hosted admits CREDIT only (`credit_admission` t,
`legacy_usd_admission` f), and 0006's admission guard (0011) refuses every admission of a
release that runs `legacy_usd` - the previous pilot release and both known-good targets.
`91-abort.sh`, `90-revert.sh` and R4 restart such a release and reopen the edge, which would
then refuse every request while reading ready. So once W7f ran, every row below that reaches
them first runs, from the coordinator host with `OPERATIONS_DATABASE_URL` (the owner login,
`read -rs`) and `INFRX_OPERATOR_KEY` exported: `python -m infrx.operations.cli
credit-transition --to legacy_usd --drain-timeout-s 900 --idempotency-key revert-<window id>
--reason "<window id> rollback to legacy_usd"` (PI P-02; G8-6a075c5.md step 6). It freezes
`credit_admission` and enables `legacy_usd_admission` (`signup_grant` stays t: hosted's flags
at 0018); nothing converts, and a CREDIT job already accepted settles in CREDIT. Then the
row's step, which reopens the edge. Before W7f (the W6 and W7 rows) there is nothing to reverse.

Its drain needs the worker of the release it reverses: only a CREDIT worker finishes a CREDIT
job, and one that a pause released (`drain.sh pause`: stop claiming, release the rest) stays
in flight until a worker's reaper requeues it and a CREDIT worker runs it. So the reversal
runs before any step that stops that worker (`30-pause.sh`, `95-maintenance.sh`, the rollout
rollback's drain: [rollback.md](rollback.md#rollout-rollback) step 2, drill step 3); every
row below runs it first, before its own step stops anything. If it exits 1 `in_flight` with the
worker already stopped: on the box `systemctl start infrx-worker` (the current release's; the
edge stays in maintenance, the engine still runs), the same-key rerun until it exits 0, then
`systemctl stop infrx-worker`. Where no CREDIT worker can run (R4's dead host), it cannot
finish: the host stays in maintenance.

Its keys: one per reversal (`revert-<window id>`). A run stopped at the drain bound (exit 1,
`in_flight` or `open_transactions`) has frozen `credit_admission`, left `legacy_usd_admission`
off and audited nothing, so neither regime admits: rerun it under the **same** key while that
worker runs, until the CREDIT job has ended (it settles in CREDIT, at its card). A run resumed
after exit 1 audits the frozen flags as its `before`; the freeze itself is attributed on the
`credit_admission` row (`updated_by`, `reason`) and on the blocked run's output (RV3-4). A finished key's replay prints the
recorded result with `replayed: true` (WR-RB3-1) and writes nothing, so W7f's own key never
re-activates CREDIT: a roll-forward reruns W7f's `credit-transition --card …` under a **new**
key. `--dry-run` writes nothing and needs no operator key. Proof on PostgreSQL:
`apps/infrx-api/tests/g/ops/test_reversal_pg.py::test_reversal_pg__credit_back_to_legacy_usd_drains_keeps_credit_exact_and_replays_nothing`,
with the mutants `reversal_skips_the_drain`, `reversal_audits_as_forward` and
`replay_reruns_the_freeze` (`tests/g/ops/mutants.py`, case `test_reversal.py`). The box half,
putting a `legacy_usd` release back (`85-known-good-box.sh`, its checkout and 50-install, or a
backup/snapshot restore), stays an operator step that no test runs.

**The reinstall rule.** Every 50-install rewrites `/etc/marlin2b-gateway.env` from SSM
(`preflight.py apply`, one rename): `DATABASE_URL` is `pg_journal_url`, the owner login, again,
and `MONITOR_DATABASE_URL` comes only from the SSM leaf `monitor_database_url`, which no step
creates, so it is dropped and the worker reports no reconciliation gauges. So every 50-install of a release that carries R127's
dedicated logins (this release and later: W10, the drill's roll-forward in
[rollback.md](rollback.md#known-good-rollback-drill)) is followed by `55-runtime-login.sh` (W10b).
A release before R127, such as the known-good targets 4226315 and bda1586, stays on
`pg_journal_url`: never run 55 after installing one. Its pool runs `set role service_role` on
every connection, which `infrx_runtime` may not (0021: a member of no role); 55's exit 4
would then put the file back (inferred from the code, not run). A restore that puts a saved env file back (`rollback.sh` through
90-revert or R2, 55's exit 4, R4's snapshot) needs no rerun: the file comes back as it was —
unless the runtime/monitor passwords were rotated (a later 55 run) after that backup was taken:
the restored DSNs then fail the login check, so rerun 55 (an R127 target) after the restore (RV3-2).

| Trigger | Action |
|---|---|
| W6 check not equal, or the copy's apply fails | Stop before any hosted write: `91-abort.sh`, `93-restore-edge.sh` |
| W7 plan digest ≠ `$COPY_DIGEST`, or `apply` exit 2/3 | Nothing changed: the same abort |
| W7 `apply` exit 4, or anything wrong after it committed | Maintenance stays; [restore.md A8](restore.md#a8-then-and-only-then-the-hosted-apply). The migrations are additive: the monolith's own statements ran on the migrated copy (W6), so the abort path above still serves |
| W9 red (bucket, role, AWS semantics) | Nothing installed: the W7f reversal, then the same abort (`91-abort.sh`, `93-restore-edge.sh`) |
| W10 exit 2 (preflight refused: a missing key, P3/P4) | The W7f reversal, then R1 of [../rollout/README.md](../rollout/README.md): `91-abort.sh`, then `93-restore-edge.sh` |
| W10 exit 4 (the runtime never became ready; the edge unchanged) | The W7f reversal, then `90-revert.sh RELEASE=$RELEASE BACKUP=<the backup dir the install printed>` and, when no pilot request was ever accepted (the edge never switched), `ROLLBACK_TO_UNMETERED=no-pilot-request-was-accepted`; then `93-restore-edge.sh`. If the running Caddy then answers only on `localhost:2019` (the socket-addressed steps fail with `dial unix /config/admin.sock`), reload the live file once with `docker exec caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile --address localhost:2019` (2026-09-24) |
| W10 exit 4, W11 or W12 red, **no pilot request accepted** | R2: after the read-only zero count of pilot jobs/ledger rows since W10, the W7f reversal, then `90-revert.sh` (runs `rollback.sh` on install.sh's backup, engine first) |
| Pilot requests were accepted and it must stop | R3: `95-maintenance.sh`; `rollback.sh` refuses the unmetered monolith |
| The host itself | R4: the W7f reversal (the snapshot's release runs `legacy_usd`), then root-volume swap to the W2 snapshot ([restore.md](restore.md#box-snapshot)) |

### Known-good record

P-25 (decided 2026-09-25): a release is a known-good rollback target when this exits 0
with all six checks passing (commit, preparation, migrations, config, record, bundle)
**and** `infra/rollout/steps/85-known-good-box.sh TARGET=<sha>` exits 0 on the box. A backup
directory or a short readiness is not the record.

```bash
apps/infrx-api/.venv/bin/python infra/rollout/known-good.py <sha> --applied <hosted version> \
  --set S3_MEDIA_BUCKET --set MAX_VIDEO_SECONDS --set WORKER_CONCURRENCY \
  --set LARGE_BODY_LIMIT --set DATABASE_POOL_MAX_SIZE --set ENGINE_MAX_NUM_SEQS \
  --set ACCOUNTING_REGIME --set ACTIVE_RATE_CARD_VERSION \
  --bundles s3://llm-bootcamp-641134885443/releases/
```

The `config` check compares only the `--set` names (without one it passes vacuously), so
the command passes every name this install does: section 1's `INFRX_SET` plus the
`ENGINE_MAX_NUM_SEQS` 50-install adds (rollback.md's drill passes the same list). Leave
`RETENTION_GRACE_S` (P-25's 3,600 s default) out of `INFRX_SET`, or a candidate that
predates it refuses (meas. local, `--applied 0023` without `--bundles`: 4226315 and
bda1586 pass `config` with these six names and fail it with `--set RETENTION_GRACE_S`; with the
eight names above, full SHAs, `--applied 0023`, both exit 0 KNOWN-GOOD, meas. 2026-09-26
E4C-RUNBOOK-2). Both targets only ever served `legacy_usd`: after W7f, returning to one first
needs the W7f reversal (above) and that release's own env file (R2 restores it). The database
half of that path is drilled (`test_reversal_pg.py`, above); the box half is not.
The `schema_proof` for bda1586 and 4226315 reaches 0025 (`infra/rollout/known-good.json`,
research/plan/evidence/i/KNOWN-GOOD-PROOF-2-3f7df77.md); a migration beyond 0025 needs the
proof extended before `--applied` may name it.

## 4. Continuous operations (I8) — after the release that carries I8

Each row is one coordinator op, logged first (README rule 1), serialized after any running
certification. Box rows: `infra/rollout/ssm.sh infra/rollout/steps/<step> NAME=VALUE…`.
Nothing here has run; every row's output goes into the I8 evidence record.

| # | Where | Op | Pass | Blocked on |
|---|---|---|---|---|
| O1 | host | `apps/infrx-api/.venv/bin/python infra/runbooks/pool_budget.py --runtime-port 5432 --set DATABASE_POOL_MAX_SIZE=6` (and without `--set`: the FAIL that explains EMAXCONNSESSION) | `PASS session: peak 13` | - |
| O2 | host | the release with I8 through W1-W13, `DATABASE_POOL_MAX_SIZE=6` in `INFRX_SET` (§1). New: the runtime units run `preflight.py envcheck` before every start (the journal names a refused setting) | W12 `failures: 0` | - |
| O3 | box | `71-pool-budget.sh` | exit 0, `PASS session` | - |
| O4 | box | `72-observe-install.sh RELEASE=$RELEASE CANARY_VIDEO=<in-cap clip on the box> CANARY_KEY_PARAM=<ssm name of the canary tenant key>` [+ `P24_APPROVED=<ref>`] [+ `ALERT_WEBHOOK_PARAM=<ssm name> ALERT_OWNER=<who> ALERT_ESCALATION=<how>`] (no `MONITOR_DSN_PARAM`: W10b already wrote `/etc/infrx-observe.env` with the `infrx_monitor` login; a parameter given replaces it) | timers listed; first cycle exit 0 or 3 (delivery BLOCKED); without `P24_APPROVED` the canary timer is not enabled (`BLOCKED (P-24)`) | P-24 (canary spend + its tenant, canary half only); P-25 (destination); W10b (monitor login) |
| O5 | box | `73-observe-status.sh` two minutes later | `infrx_durable_up 1`, `infrx_canary_up` 1 for text and video (only once O4 ran with `P24_APPROVED`), no unexpected firing | P-24 (the canary half) |
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
- 2026-09-25 (OPS-CLI-DSN): W7d names the operator CLI's own `OPERATIONS_DATABASE_URL` and its refusal of the dedicated logins. Not run.
- 2026-09-25 (ROLLOUT-FIXES): §1's `INSTALL_ARGS` carries `DATABASE_POOL_MAX_SIZE=6` inside
  `INFRX_SET` (it was a comment; the rehearsal's `pool_budget.py` FAILs at defaults, peak 21 +
  headroom 2 > 15, and PASSes at 6, peak 13); 50-install requires `ENGINE_MAX_NUM_SEQS` (its
  default of 32 is gone). Not run on the box.
- 2026-09-26 (P25-ENACT): §3 "Known-good record" states P-25's definition (known-good.py's
  six checks with `--bundles`, plus 85-known-good-box.sh) and the 0023 schema proof. Not run.
- 2026-09-26 (P25-ENACT fix round, 1-P25R-3): the Known-good record command passes
  `--set` for every install name (§1's INFRX_SET and ENGINE_MAX_NUM_SEQS), as rollback.md's
  drill does, so its `config` check is exercised rather than vacuous. Not run on the box.
- 2026-09-26 (E4C-RUNBOOK-2): W6 seeds the copy's history from hosted's own read-only `plan`
  `applied:` line at the window (0001-0018 today; the literal 0001/0002 was stale), and its pass
  line names hosted's flags at 0018; W7 expects 0001-0026 after the apply (0024/0025/0026 from
  `codex/d10-merge-2`); §1 installs `ACCOUNTING_REGIME=credit` with the P-01 card and states what
  the code does when the card or the activation is missing; W7f (CREDIT activation, E4C-runbook
  §1a) before W10; W10b `55-runtime-login.sh` (the dedicated logins); the Known-good record passes
  the two regime names (measured). Tests: `tests/integration/backend/recovery/test_runbooks.py`
  rb09-rb11, `apps/infrx-api/tests/i/test_ops_steps.py` (step 55). Not run on the box or hosted.
- 2026-09-26 (E4C-RUNBOOK-2 fix round): §3 defines the W7f reversal (`credit-transition --to
  legacy_usd`) and every rollback after W7f runs it before the step that reopens the edge on a
  legacy_usd release (W8-W12 cells; §3 W9, W10 exit 2/4, R2, R4), since hosted then refuses
  every legacy admission; W10b also writes `/etc/infrx-observe.env` (the monitor login), so O4
  needs no `MONITOR_DSN_PARAM` and O5's `infrx_durable_up 1` reads through `infrx_monitor`.
  Tests: rb12 (`test_runbooks.py`), the step-55 case (observe cycle). Not run on the box or hosted.
- 2026-09-26 (RUNBOOK-3): §3 states the reversal's keys (same key to resume a stopped run, a
  new key to re-activate; a replay writes nothing) and its proof, the PostgreSQL drill
  `test_reversal_pg.py` (K1 activation, CREDIT settled and in flight, K2 stopped at the bound,
  drained, finished, replays) with three new `tests/g/ops` mutants; the reinstall rule (55
  after every 50-install of an R127 release, never after a pre-R127 target). Tests:
  `tests/integration/ops/test_runbook_reversal.py`. Not run on the box or hosted.
- 2026-09-26 (RUNBOOK-3 fix round, review 0-RV3-1): §3 states that the reversal's drain needs
  the worker of the release it reverses (a job a pause released stays in flight until a CREDIT
  worker runs it), so it runs before any step that stops that worker, with the way out after
  one did (`systemctl start infrx-worker`, the same-key rerun, `systemctl stop infrx-worker`).
  Rows unchanged. Not run on the box or hosted.
