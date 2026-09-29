# Pilot rollout runbook — the headless Marlin endpoint on `i-0e8449a4ffca29bab` (I2B.c)

**Coordinator-run.** The I2B session wrote and locally rehearsed these steps; it ran none of
them against the box, AWS or hosted Supabase. Every box step is a script under `steps/`,
sent by `ssm.sh` as root through `aws ssm send-command` (AWS-RunShellScript), base64-wrapped
so no quoting survives the trip. Coordinator-host steps are the exact AWS/Docker commands
below. Log purpose, expected cost and rollback in the coordinator record **before** each
mutating step (session-02 operating rule), and hold the deployment lock of
[infra/README.md §1](../README.md) for the whole window.

```bash
aws() { env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN aws --region us-east-1 "$@"; }
export RELEASE=<the merged commit to deploy>          # a full SHA on claude/consumer-v1
export INSTANCE=i-0e8449a4ffca29bab
```

## 0. Go / no-go gates (all at `RELEASE`, all local, before any AWS call)

| Gate | Command (coordinator host, repo checked out at `RELEASE`) | Pass |
|---|---|---|
| G1 the tree is the release | `git rev-parse HEAD; git status --porcelain` | `$RELEASE`, empty. A symlinked `apps/infrx-api/.venv` shows as untracked (`?? apps/infrx-api/.venv`): build it in the tree with `make api-env` instead |
| G2 the suite | `(cd apps/app && pnpm install --frozen-lockfile) && (cd apps/lab && pnpm install --frozen-lockfile)` first (both packages; LW0), then `INFRX_D_TASK=e2c INFRX_D2_VALKEY_PORT=55493 INFRX_D2_VALKEY_CONTAINER=infrx-e2c-valkey INFRX_Q_VALKEY_PORT=55430 PYTEST_ADDOPTS=-rsxX make check` then `cd apps/infrx-api && INFRX_D_TASK=e2c uv run --frozen pytest -q tests/i`. Before and for the whole run (about 3.5 h): no other user of e2c (`infrx-e2c-*`, 55448/55493/55494, Q's `infrx-q3-valkey-55430`) or i8 (`infrx-i8-*`, 55450/55495/55496, `/tmp/infrx-i8-postgres-55450.lock`); never `INFRX_D_TASK=i8` (the D harness and `tests/i/pooler.py` share that lock) and never the defaults (d1's 55432, Q3's 55462) | exit 0 both; the skips are only the named S3 (no MinIO), PostgREST (`INFRX_D1_IMAGE`) and empty-parameter ones ([G2-FIX](../../research/plan/evidence/coordinator/G2-FIX-041d6f6.md) lists them) |
| G3 the pilot runtime starts | `docker build --provenance=false -q -f apps/infrx-api/deploy/Dockerfile -t infrx-runtime:$RELEASE apps/infrx-api` then `printf 'INFRX_MODE=pilot\nSUPABASE_URL=https://gate.supabase.co\nSUPABASE_SERVICE_ROLE_KEY=gate-placeholder-0123456789\nDATABASE_URL=postgresql://gate@127.0.0.1/gate\nPROCESSING_CACHE_DIR=/opt/dlami/nvme/processing\nVALKEY_URL=valkey://127.0.0.1:6379/0\n' \| docker run --rm -i --network none infrx-runtime:$RELEASE python /app/deploy/preflight.py probe --mode pilot --env-file /dev/stdin` | `"ok": true` (passes since the cutover: routers composed, `python -m infrx.worker` present; the rollout gates rehearsal at e607b705 recorded PASS) |
| G4 the engine pin | `cd apps/infrx-api && uv run --frozen python -c 'import sys,pathlib; sys.path.insert(0,"deploy"); import preflight; print(preflight.engine_problems(pathlib.Path("../../models/marlin2b/serve.sh"), "pilot"))'` | `[]` (needs W3's `serve.sh` + `serving-version.json`). It does not read the processor digests: G4b gates P-06 |
| G4b the processor pin (P-06) | `python3 -B -c 'import json,sys; sys.path.insert(0,"infra/runbooks"); import artifacts; m=json.load(open("models/marlin2b/serving-version.json"))["model"]; bad=[f for f in ("processor_config_digest","preprocessor_config_digest") if not m.get(f) or f not in artifacts.PINNED]; print(bad or "ok"); sys.exit(bool(bad))'` (repo root) | `ok`, exit 0: both served-bytes digests are non-null in `serving-version.json` and `infra/runbooks/artifacts.py` PINNED names them. G4 does not read them; fails (exit 1) while P-06 is open |
| G5 the local rehearsal | `apps/infrx-api/deploy/rehearse.sh` | `REHEARSAL PASSED`, teardown leaves nothing |
| G6 inputs | `/model-inference/pg_journal_url` exists (created 2026-09-24T01:03Z, v1 SecureString: [rollout.md §1](../runbooks/rollout.md#1-settings-for-this-release); step 2 below is for a new DSN only); hosted backup rehearsed by I3B before any hosted migration; G6B can issue a scoped key and revoke one | recorded |

## 1. The window

| # | Where | Step | Record |
|---|---|---|---|
| 1 | coordinator | **Snapshot the root volume** (§7 step 1): `vol=$(aws ec2 describe-instances --instance-ids $INSTANCE --query 'Reservations[0].Instances[0].BlockDeviceMappings[?DeviceName==\`/dev/sda1\`].Ebs.VolumeId' --output text)`; `snap=$(aws ec2 create-snapshot --volume-id "$vol" --description "infrx I2B pre-rollout $RELEASE" --tag-specifications "ResourceType=snapshot,Tags=[{Key=Name,Value=infrx-i2b-pre-${RELEASE:0:12}}]" --query SnapshotId --output text)`; `aws ec2 wait snapshot-completed --snapshot-ids "$snap"` | `$snap`, volume id, UTC |
| 2 | coordinator | **Create the journal DSN parameter** (§5, PROPOSED until now). The value is written to a 0600 file without a trailing newline (preflight refuses a newline) and read by the CLI from it, never typed on a command line: `read -rs DSN` (pasted: not echoed, not in shell history), then `umask 077; printf '%s' "$DSN" > ~/.infrx-dsn; aws ssm put-parameter --name /model-inference/pg_journal_url --type SecureString --value file://$HOME/.infrx-dsn; shred -u ~/.infrx-dsn`. Check: `aws ssm describe-parameters --parameter-filters Key=Name,Values=/model-inference/pg_journal_url --query 'Parameters[].[Name,Type,Version]'` | name, type, version (never the value) |
| 3 | box | `infra/rollout/ssm.sh infra/rollout/steps/10-inventory.sh` (read-only: docker/buildx versions, checkout, units, env **names**, containers, edge volumes, non-loopback listeners) | output |
| 4 | box | `infra/rollout/ssm.sh infra/rollout/steps/20-prepull.sh RELEASE=$RELEASE` - fetch, and pull the engine image `serve.sh` pins at `RELEASE` (outside the window; the working tree is untouched) | pulled digest |
| 5 | box | **Window opens:** `infra/rollout/ssm.sh infra/rollout/steps/30-pause.sh RELEASE=$RELEASE` - the release's edge (pinned Caddy, certificates kept in the `caddy_data` volume) serves maintenance 503, the monolith gateway stops; the previous HEAD is saved as `/var/backups/infrx/pre-$RELEASE.head` | UTC |
| 6 | coordinator | **Hosted migrations** (only after I3B's backup rehearsal; D-owned files, applied as one transaction): `read -rs MIGRATE_DATABASE_URL; export MIGRATE_DATABASE_URL` (paste the session-pooler DSN :5432 of the migrating role, composed from /INFRX-SUPABASE-PROD/* - never typed on a command line, so never in history or `ps`); `run() { docker run --rm -e MIGRATE_DATABASE_URL -v "$PWD/apps/app/supabase/migrations:/migrations:ro" infrx-runtime:$RELEASE python /app/deploy/migrate.py "$@" --dir /migrations; }`; `run plan` (read-only; review applied/pending/sha256), then `run apply --expect <plan digest>`. A refusal (exit 2) or a failure (exit 3, rolled back) changes nothing; if hosted history versions do not match the file versions, `plan` refuses - resolve with D, never by hand | plan output, digest, apply output |
| 6b | coordinator | **CREDIT activation** (when `INSTALL_ARGS` carries `ACCOUNTING_REGIME=credit`, as the E4C candidate's does; after 6, which applied 0022's `infrx.set_feature_flag`, before 7): [rollout.md W7f](../runbooks/rollout.md#2-the-window) = [E4C-runbook §1a](../../models/marlin2b/results/E4C-runbook.md#1a-after-the-hosted-apply-before-w8) H1 `publish-card` (P-01) and H2 `credit-transition --dry-run`, then `credit-transition --card` (P-02, G8). Without it step 8 exits 4 (`price_source`: the card is not active) or its gateway reads ready and refuses every admission (503, flags off). Once it ran, R1, R2 and R4 first reverse it (`credit-transition --to legacy_usd`) | card id, dry-run JSON sha256, the flags |
| 7 | box | `infra/rollout/ssm.sh infra/rollout/steps/40-checkout.sh RELEASE=$RELEASE`. **From here to step 8 no engine restart** (no `systemctl restart marlin2b-vllm`, no reboot): the installed unit still passes `--max-num-seqs 32`, which the checked-out `serve.sh` refuses, so the engine would stay down until step 8 or R2. Run 8 right after 7. This window is **procedural**: nothing masks the unit, and its `Restart=always` means an engine crash inside the window loops every 5 s until step 8 installs the new unit (the recovery) or R2 runs | HEAD |
| 8 | box | **Cutover:** `TIMEOUT_S=3600 infra/rollout/ssm.sh infra/rollout/steps/50-install.sh "${INSTALL_ARGS[@]}" MIGRATION_DIGEST=<the digest step 6 applied, or nothing-pending>` with `INSTALL_ARGS` exactly as [rollout.md §1](../runbooks/rollout.md#1-settings-for-this-release) defines it (`ENGINE_MAX_NUM_SEQS=8`, and `DATABASE_POOL_MAX_SIZE=6` inside `INFRX_SET`; with `ACCOUNTING_REGIME=credit` in `INFRX_SET`, as the E4C candidate, only after 6b ran) - the step refuses to start without `ENGINE_MAX_NUM_SEQS` (no default) or the digest, or with anything but 64 hex or exactly `nothing-pending` (the box cannot see hosted history, so this is the operator's recorded statement that step 6 ran, not a check of it); `install.sh` in pilot mode: image built from `RELEASE`, secrets read and probed **inside** that image, env file replaced by one rename, units installed, engine restarted onto its pinned image and media root, runtime restarted, gateway and worker `/readyz`, and only then the edge's normal site. Exit 2 = refused, nothing changed, edge still in maintenance (→ R1); exit 4 = not ready, or a Caddyfile that does not validate with the pinned Caddy (edge unchanged) (→ R2/R3) | image id, backup dir (`/var/backups/infrx/<UTC>-<sha>`), the migration digest it echoed, full output |
| 8b | box | **Dedicated runtime logins** (RV-09, R127; after step 6 applied 0021+ and after every step 8, which rewrites the env file from SSM): `infra/rollout/ssm.sh infra/rollout/steps/55-runtime-login.sh` - reads `/model-inference/infrx_runtime_password`, `/model-inference/infrx_monitor_password` and `/model-inference/pg_journal_url` by name on the box (override: `RUNTIME_PASSWORD_PARAM`, `MONITOR_PASSWORD_PARAM`, `OWNER_DSN_PARAM`), sets both passwords on hosted through the owner login inside the installed image, logs in as each on :6543, writes `DATABASE_URL` (`infrx_runtime`) and `MONITOR_DATABASE_URL` (`infrx_monitor`) into the env file after envcheck, restarts gateway and worker and waits for `/readyz`, then writes `MONITOR_DATABASE_URL` alone into `/etc/infrx-observe.env` (0600; observe.sh's durable exporter reads it first, and its fallback, the env file's `DATABASE_URL`, is now `infrx_runtime`, which 0021 grants none of those tables). The two password parameters are created first like step 2 (a 0600 file, `--value file://`). Exit 2 refused (an unreadable SSM parameter is named), 1 SQL/login failed, 3 envcheck red (nothing replaced), 5 the login check did not return both DSNs (nothing staged), 4 a failed restart or not ready (the previous env file put back, the units restarted on it and both `/readyz` probed); a rerun sets no password for a role whose login works; when the env file already names both DSNs it prints `unchanged` and restarts nothing; no staged copy outlives any exit | the saved env file path, `unchanged` on a rerun. At the window: `95-maintenance.sh RELEASE=$RELEASE` right after step 8's exit 0, then this step, then step 8c (E4C-RUNBOOK-2 CS-6/F4; rollout.md W10b). |
| 8c | box | **Resume after 8b** (W10b, RB4-1): `infra/rollout/ssm.sh infra/rollout/steps/56-resume.sh RELEASE=$RELEASE` - the release's `drain.sh resume`: starts gateway and worker, waits for both `/readyz`, then reopens the edge. No checkout (unlike `91-abort.sh`). After an 8b put-back the edge stays in maintenance until this runs on the owner login (RV-09 not met, record it) or the window stops | `resumed: ... ready; the edge serves` |
| 9 | box | `infra/rollout/ssm.sh infra/rollout/steps/60-verify-local.sh` - units active, `/readyz` 200, each container's image/user/read-only/capabilities as applied, non-loopback listeners only :80/:443/sshd, env names, `INFRX_MODE=pilot`, no `GATEWAY_API_KEY` | output |
| 10 | coordinator | `read -rs INFRX_TEST_KEY; read -rs INFRX_REVOKED_KEY; export INFRX_TEST_KEY INFRX_REVOKED_KEY; export LEGACY_KEY=$(aws ssm get-parameter --with-decryption --name /model-inference/marlin2b_api_key --query Parameter.Value --output text); infra/rollout/verify-external.sh` (G6B's keys pasted, never typed on a command line; the script hands every key to curl in a 0600 header file, never as an argument): sanitized health, `/metrics` `/readyz` 404, ports 8000/8001/6379/2019 unreachable, no key / made-up key / revoked key / legacy shared key 401, scoped key 200 with `Server-Timing`, a link-local media URL refused, a declared oversize body 413. `PENDING` lines are not passes | output, `failures: 0` |
| 11 | coordinator | Record `RELEASE`, image id, engine digest (`serving-version.json`), region us-east-1, `$snap`, backup dir, env names, migration digest; remove the install backups older than the previous accepted release's (`/var/backups/infrx/*`: they hold past env files, secrets included); close the lock | - |

## 2. Revert paths

| Situation | Action |
|---|---|
| R1 - step 8 refused (exit 2) | Nothing was installed. Fix and rerun 8, or abort: if 6b ran, first the W7f reversal, `credit-transition --to legacy_usd` ([rollout.md §3](../runbooks/rollout.md#3-rollback-triggers): the restored release runs `legacy_usd`, which hosted then refuses at every admission), then `infra/rollout/ssm.sh infra/rollout/steps/91-abort.sh RELEASE=$RELEASE` (previous checkout, previous gateway, edge reopened after `/health`) |
| R2 - step 8 exit 4 or step 9/10 fails, **no pilot request was accepted** | If 6b ran, first the W7f reversal, `credit-transition --to legacy_usd` ([rollout.md §3](../runbooks/rollout.md#3-rollback-triggers): the restored release runs `legacy_usd`, which hosted then refuses at every admission); then `infra/rollout/ssm.sh infra/rollout/steps/90-revert.sh RELEASE=$RELEASE BACKUP=<backup dir from step 8> ROLLBACK_TO_UNMETERED=no-pilot-request-was-accepted` - previous checkout, the backed-up env/unit/Caddy files, the engine restarted onto its previous unit and healthy **before** the previous gateway (the monolith's `/health` asks the engine, and step 8 may have failed on the engine itself), then `drain.sh resume`: the backup was taken after step 5, so the Caddy file it restores is the maintenance site, and the edge reopens (the release's normal site in front of the restored gateway) only once that gateway is ready. Exit 4 from the step means the restored engine or gateway did not come up: the edge stays in maintenance - fix that (`journalctl -u marlin2b-vllm`, `-u marlin2b-gateway`), then `/root/infrx-deploy-$RELEASE/apps/infrx-api/deploy/drain.sh resume`. The statement is the operator's, not something the script can check: corroborate it first with a read-only count of hosted pilot job and ledger rows since step 8 (zero), and put both in the lock record ([infra/README.md §8](../README.md)) |
| R3 - pilot has accepted work and must stop | `infra/rollout/ssm.sh infra/rollout/steps/95-maintenance.sh RELEASE=$RELEASE` - maintenance 503 until a compatible metered runtime exists (§8 rule 3); `rollback.sh` refuses to put the unmetered monolith back |
| Rollback target after migrations 0019+ | Once hosted carries migrations 0019 or later, a recorded target qualifies only through a `schema_proof` reaching the applied migration (`known-good.py` refuses one without it). Both recorded targets, bda1586 and 4226315, carry a `schema_proof` through 0051 (0027-0051 are the Lab migrations at 72dc76ad; KNOWN-GOOD-REPROOF, plain PostgreSQL and the Supabase image; see the paragraph below), so the [known-good rollback](../runbooks/rollback.md#known-good-rollback-drill) has a target up to 0051; beyond 0051 none qualifies until a new proof is recorded, and R3 (maintenance) is the fallback. Steps 71/72/74/80/81/86 also answer `BLOCKED ... I8+ checkout` (exit 3) on such a checkout |
| R4 - the host itself | If 6b ran, first the W7f reversal, `credit-transition --to legacy_usd` ([rollout.md §3](../runbooks/rollout.md#3-rollback-triggers): the restored release runs `legacy_usd`, which hosted then refuses at every admission); then `aws ec2 create-replace-root-volume-task --instance-id $INSTANCE --snapshot-id $snap` then `aws ec2 describe-replace-root-volume-tasks --filters Name=instance-id,Values=$INSTANCE` until `succeeded` (the instance reboots; the instance-store NVMe - weights, media cache - survives a reboot), then step 3. RTO est. 10-20 min (§6), ⚠️ TO BE VERIFIED by I3B. Hosted migrations are additive and are **not** reverted (§8 rule 4) |

**Rollback targets after the window applies 0019+ (KNOWN-GOOD-PROOF).** R2 and the O12 drill
return to a release from `known-good.json`; with hosted ahead of that release's tree,
`known-good.py` accepts it only through its record's `schema_proof`. `bda1586` and `4226315`
carry `through: 0051` (0001-0021 as on main, 0022 from `codex/d10-followup` c584f54a, 0023 from
`codex/door-revoke` 1d0a418d, 0024/0025 from `codex/d10-merge-2` 9e4e34ca, 0026 from `codex/d10-0026-fence` 400a7e94, 0027-0051 the Lab migrations at 72dc76ad; KNOWN-GOOD-PROOF-2/3 and KNOWN-GOOD-REPROOF reran the whole proof on plain PostgreSQL and on the Supabase image): each release's own `tests/d` (admission, preparation/claim and
leases, settlement in both regimes, journal/stream, outbox relay, operations, signup, catalog,
gateway composition over PostgreSQL) and a probe of its result read after a committed outcome
passed on a database built from those files. `infra/runbooks/schema_proof.py <sha>` reruns it
task-locally; with `SCHEMA_PROOF_DSN` it also checks, read-only, that a migrated database's
history is exactly those files: run that against hosted after step 6, before relying on the
proof. Each `schema_proof` also records the sha256 of 0019-0051 (`files`), and `known-good.py`
refuses a checkout whose migrations beyond the target's tree are other bytes; the driver counts
a suite that skipped a case or passed none as FAIL, and accepts a Supabase CLI history only when
its statements, in order, are the whole file. Not proven: any migration after 0051, or a
0022-0051 other than those bytes (rerun, then add the new `through` and `files`); the old release on the `infrx_runtime` login (0023 revokes
`admit`/`claim_preparation` from it on purpose, so revert with the target's own env file, which
R2 restores: the login the release ran on, which cannot be `infrx_runtime`, created by 0021); hosted rows written before the window (the proof uses
fresh rows); a browser key INSERT by an unverified owner (0024 refuses it whichever backend runs); the targets' lease-less result write stays unfenced on 0026 (R147 follow-up: refuse it only after these targets leave the record). Fourteen old cases (thirteen SQL-shape cases plus the registry decoy lint) that list the
old catalog, read a result before its outcome or exercise the 0024/0025 browser surface are skipped by name, each with its reason
(`SHAPE` in the driver). If no record reaches `--applied`, R3 maintenance is the only fallback.

## 3. What changes for clients (legacy-account transition)

P-02 is resolved: the four hosted accounts hold USD 0.00 and are dev/e2e accounts that
receive no grant until verified (A1). In pilot mode the shared key
`/model-inference/marlin2b_api_key` is neither read nor written (R51), so any client using
it gets 401 after step 8; scoped keys come from G6B. The parameter itself is left in place
for the revert paths.

The operator CLI (`python -m infrx.operations.cli`: grant, adjust, issue-key, revoke-key,
credit-transition, ...) runs with its **own** DSN: `read -rs OPERATIONS_DATABASE_URL; export
OPERATIONS_DATABASE_URL` (the owner or broad login, never typed on a command line), which it
prefers over `DATABASE_URL`. It does not use the runtime env file's `DATABASE_URL` once that
names the dedicated `infrx_runtime` login (R127): a dedicated login (`infrx_runtime`,
`infrx_monitor`) is refused before anything is dialled, with a message naming the variable,
never the DSN - the runtime login must never rewrite money, and it cannot `set role`.

## 4. Not in this window

The role/boundary/KMS/IMDS/SSH hardening of [infra/README.md §5](../README.md) (rows
`M-ROLE`, `M-BOUNDARY`, `M-KMS`, `M-IMDS`, `M-SSH`) is its own locked operation with its
own read-only checks; it is not a precondition of this runbook, and this runbook does not
perform it. The Caddy container keeps host networking (so it can reach IMDS; §5 records
that residual risk and its bound).

## 5. The E4C certify window (on the live release)

`infra/rollout/certify-window.sh` runs the E4C certificate run and E1B's cells on the installed RELEASE in
[E4C-runbook](../../models/marlin2b/results/E4C-runbook.md) order, from the repo root after `make api-env`,
logging every step to its 0700 `LOGDIR`; `--step <step> --logdir <dir>` resumes, `--only <step>` runs one step,
`DRY_RUN=1` prints the plan and its stop conditions and calls nothing. One sequencer holds a LOGDIR (`flock` on
`$LOGDIR/.lock`); no step starts while another detached cell of the LOGDIR is live, and no step after `report` starts
before `report` has seen the launched certify run's `exit N`. Its steps and helpers:

| Script | Where | What | Stops / exits |
|---|---|---|---|
| `steps/76-e4c-prepare.sh RELEASE=` | box | A1 edge 200; `/opt/dlami/nvme/w3-checkout` to RELEASE from W1's verified bundle (the deploy checkout is not touched); `infrx-certify:$RELEASE` built once; `e4b/inventory.txt` retaken (the previous kept as `.prev`) | 2: edge not 200, dirty checkout, bundle sha256 mismatch (nothing changed); 3: inventory failed or the engine is off the pin (old file kept) |
| `steps/77-e4c-profiles.sh RELEASE= MIGRATION_VERSION= MAINTENANCE_WINDOW= KEYS_TAKEN_AT= KEYS_SOURCE_SHA256= ACTIVE_PREFIXES=` | box | E4C-runbook §2/§3: reads every identity from the served build, fills the six bases (`certify-fill.py` verbatim) and writes `keys-certify.json` into `e4b/e4c/`, validates each in the certify image with `--network none`, prints `certify --hashes` | 2: a read disagrees (nothing written); 3: a profile does not validate |
| `steps/78-e4b-report.sh RUN=` | box | the certify run's outputs: `certify exit N` (certify.log's closing line, first, inside SSM's 24,000 characters), the listing, the JSON, the log tails; with no `RUN`, the newest UTC-named run (never `e4c/` or `e1b-*`) | 3: no run |
| `steps/79-wc0-scrape.sh ACTION=start\|stop` | box | E1B WC-0: the metrics/vmstat scrape sidecar, detached, one at a time | 2: a second start or a stop with none; 3: no scrape line |
| `steps/80-e4b-fetch.sh RUN=` | box | packs one finished run (report, log, work) to `s3://…/w4/e4b-box/<RUN>.tgz`, prints its sha256 | 2: not a UTC run name; 3: no report.json |
| `certify-h6.sh <LOGDIR> <tag>` | host | H6 and the closing inventories: `credit-transition --dry-run`, key id prefixes of active non-operator keys; the operator key printed apart | 1: `STOP:` (another spending key, or the dry run failed) |
| `certify-fill.py <tree> <dest> NAME=BASE…` | host/box | fills FILL values only; §5.0's two-tenant bases need `TENANT2_PREFIX` | 2: any mismatch (nothing written) |
| `certify-validate.py <tree> <filled> <plan>` | host | `bench --validate-only` on every window cell (E4C §3 lines, certify's stamped cells, `e1b-window.sh DRY_RUN=1`'s plan) | 1: a cell not runnable |

Skipped by the user's decision: O4–O6 (alerts) and the canary. BLOCKED and recorded: the SSE journey and replay
(`MEDIA_BASE_URL`), the canary re-enable (P-24). Each §6 drill asks first; the engine restart and worker
SIGKILL have a box form, both probing the worker's `127.0.0.1:8002/readyz` (200 only when the engine answers ready),
and pass only when the probe went non-200 and answered 200 again within the bound; the others are recorded NOT RUN
unless run by hand.

## Verification log

- 2026-09-22: Written by I2B at the commit that carries it; steps are `bash -n`-clean and
  `ssm.sh`'s base64 round trip is tested (`apps/infrx-api/tests/i/test_rollout.py`). The
  box, AWS and hosted Supabase were not touched; nothing here has run against them.
- 2026-09-23 (ROLLOUT-PREP): steps `25-save-edge.sh` (keep the live Caddyfile + sha256),
  `45-s3-check.sh` (tests/m/test_s3.py on the real bucket with the instance role, before the
  install) and `93-restore-edge.sh` (put the saved edge back on an abort) added; the order that
  uses them is [infra/runbooks/rollout.md](../runbooks/rollout.md). Not run on the box.
- 2026-09-24 (I8): steps 71 (pool budget), 72/73 (monitor install, status), 74 (alert
  delivery test), 79 (evidence export), 80/81 (model mirror, restore), 85 (rollback target
  on the box), 86 (bounded cleanup); `known-good.py`/`known-good.json` and
  `verify-journey.sh`; the order is [infra/runbooks/rollout.md](../runbooks/rollout.md) §4 and
  [rollback.md](../runbooks/rollback.md#known-good-rollback-drill). Tested against stubs
  (`apps/infrx-api/tests/i/test_ops_steps.py`, `test_artifacts.py`, `test_rollback_drill.py`);
  not run on the box.
- 2026-09-25: OPS-CLI-DSN (RL-V5): §3 records the operator CLI's own DSN (`OPERATIONS_DATABASE_URL`) and its refusal of the dedicated runtime/monitor logins; steps unchanged.
- 2026-09-25 (ROLLOUT-FIXES, after the rollout gates rehearsal at e607b705): `RELEASE` is a
  SHA on `claude/consumer-v1`; G3's "false by design" note removed - the probe passes since
  the cutover (the note is kept here as history: it was true at I2B, 2026-09-22); G6 names
  the existing `pg_journal_url` (2026-09-24T01:03Z); step 8 uses rollout.md's `INSTALL_ARGS`
  (`ENGINE_MAX_NUM_SEQS=8`, now required by 50-install; `DATABASE_POOL_MAX_SIZE=6`); the
  revert table records that after 0019+ no target qualifies without a `schema_proof`, R3 the
  fallback; G1 notes the symlinked-`.venv` untracked entry. Tooling: `ssm.sh` refuses
  `--help`/a non-file before any aws call; 45-s3-check pins uv.lock's pytest 9.1.1; the
  pre-I8 checkout guard (exit 3) on 71/72/74/80/81/86; rehearse.sh's worker `infrx_build_info`
  check reads the whole body. Not run on the box.
- 2026-09-25 (KNOWN-GOOD-PROOF): `bda1586` and `4226315` proven on migrations 0001-0023 (the `schema_proof` entries in `known-good.json`; driver `infra/runbooks/schema_proof.py`; evidence `research/plan/evidence/i/KNOWN-GOOD-PROOF-aab4b41.md`). Only the task-local database was used; hosted and the box were not touched.
- 2026-09-25 (KNOWN-GOOD-PROOF fix round): `schema_proof.files` binds the proven 0019-0023 bytes (`known-good.py` refuses others); the driver fails a skipped or pass-less suite and a CLI history that omits, reorders or cuts statements; proof rerun on both targets (evidence `research/plan/evidence/i/KNOWN-GOOD-PROOF-aab4b41.md`, section Fix round). Task-local only.
- 2026-09-26 (WAVE4B-UNION): the "Rollback target after migrations 0019+" row now says both recorded targets carry a `schema_proof` through 0023, as the KNOWN-GOOD-PROOF paragraph in §2 does; doc only.
- 2026-09-26 (KNOWN-GOOD-PROOF-2, WR-KGP2-2): both targets proven through 0025 on plain PostgreSQL and the Supabase image (evidence `research/plan/evidence/i/KNOWN-GOOD-PROOF-2-3f7df77.md`; the driver's SHAPE list gained the three 0024/0025 cases); the row and the §2 paragraph now say 0025 and name the unverified-owner key INSERT as not proven; doc only.
- 2026-09-26 (KNOWN-GOOD-PROOF-3, WR-KGP3-2): both targets proven through 0026 on plain PostgreSQL and the Supabase image with the unchanged driver (evidence `research/plan/evidence/i/KNOWN-GOOD-PROOF-3-af552ed.md`; 0026 changes no grant); the row and the §2 paragraph say 0026 and name the lease-less result write as not proven; doc only.
- 2026-09-26 (E4C-RUNBOOK-2): step 8b `55-runtime-login.sh` (the dedicated `infrx_runtime`/`infrx_monitor` logins, D10 wiring 6); the E4C certify launcher is `infra/rollout/e4c-certify.sh` (E4C-runbook §4). Tested against stubs (`apps/infrx-api/tests/i/test_ops_steps.py`, `test_rollout.py`); its SQL ran once against a task-local PostgreSQL 16. Not run on the box or hosted.
- 2026-09-26 (E4C-RUNBOOK-2 fix round): step 6b (CREDIT activation = rollout.md W7f) between the hosted apply and the checkout, and step 8 installs `ACCOUNTING_REGIME=credit` only after it; R1, R2 and R4 first reverse it (`credit-transition --to legacy_usd`, rollout.md §3) when it ran; step 8b also writes `/etc/infrx-observe.env` (the monitor login for observe's durable exporter). Tested: `tests/integration/backend/recovery/test_runbooks.py` rb12/rb13, `apps/infrx-api/tests/i/test_ops_steps.py` (step 55 + one observe cycle). Not run on the box or hosted.
- 2026-09-26 (STEP55-FIX): step 8b `55-runtime-login.sh` made to match its W10b sentences: a rerun sets no password (CS-4), exit 5 for the DSN count and a put-back after a failed restart (CS-5), an unreadable SSM parameter exits 2 by name (F2; also `e4c-certify.sh`), bounded probes and a probed put-back (F4), no staged leftover (F5); new step 8c `56-resume.sh` (RB4-1). Tested against stubs and a task-local PostgreSQL 16 (`apps/infrx-api/tests/i/test_ops_steps.py`). Not run on the box or hosted.
- 2026-09-26 (STEP55-FIX-2): step 8b reads each role's `rolcanlogin` over the owner login and sets a NOLOGIN role (the first run) with no :6543 login attempt, so the first run and a rerun make no failed pooler authentication; only a rotated password costs one, for that role (S55F-2). Row 8b's rerun sentence now matches rollout.md W10b (S55F-1). Tested against stubs and a task-local PostgreSQL 16 (`apps/infrx-api/tests/i/test_ops_steps.py`). Not run on the box or hosted.
- 2026-09-26 (G2-FIX): G2 names its full command (the E2C gate env with Q on e2c's own `valkey-q` 55430, `tests/i` with `INFRX_D_TASK=e2c`, e2c and i8 held free); G4 says it does not read the processor digests and G4b (P-06) fails while they are null. Evidence `research/plan/evidence/coordinator/G2-FIX-041d6f6.md`.
- 2026-09-27 (CERTIFY-WINDOW): §5 added: `certify-window.sh` (the E4C window sequencer), steps 76/77 (E4C preconditions and profiles), 79-wc0-scrape and 80-e4b-fetch, `certify-h6.sh`/`certify-fill.py`/`certify-validate.py`; 78's default run is the newest UTC-named run. Tested against stubs and a DRY_RUN (`apps/infrx-api/tests/i/test_ops_steps.py`, `test_rollout.py`); not run on the box, AWS or hosted.
- 2026-09-27 (CERTIFY-WINDOW fix round): `certify-window.sh` holds a per-LOGDIR lock, starts no step on a live detached cell and no step after `report` before the certify run's `exit N`; 78 prints `certify exit N` first (SSM keeps 24,000 characters); the engine drill probes `:8002/readyz` and a drill needs the outage seen. Tested against stubs (`apps/infrx-api/tests/i/test_rollout.py`); not run on the box, AWS or hosted.
- 2026-09-29 (KNOWN-GOOD-REPROOF, WR-KGR-1): both targets proven through 0051 on plain PostgreSQL and the Supabase image with the driver at fca3ea38 (SHAPE 14: + the copied port registry's decoy lint; evidence `research/plan/evidence/i/KNOWN-GOOD-REPROOF-fca3ea3.md`); the row and the §2 paragraph say 0051 and `files` 0019-0051. The hosted window applied 0027-0051 at 08:26Z (digest 11eecd1d…) before this record merged; task-local only, doc only.
