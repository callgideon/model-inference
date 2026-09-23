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
export RELEASE=<the merged commit to deploy>          # a full SHA on claude/backend-impl
export INSTANCE=i-0e8449a4ffca29bab
```

## 0. Go / no-go gates (all at `RELEASE`, all local, before any AWS call)

| Gate | Command (coordinator host, repo checked out at `RELEASE`) | Pass |
|---|---|---|
| G1 the tree is the release | `git rev-parse HEAD; git status --porcelain` | `$RELEASE`, empty |
| G2 the suite | `make check` (skips reported) and `cd apps/infrx-api && uv run --frozen pytest -q tests/i` | exit 0 |
| G3 the pilot runtime starts | `docker build --provenance=false -q -f apps/infrx-api/deploy/Dockerfile -t infrx-runtime:$RELEASE apps/infrx-api` then `printf 'INFRX_MODE=pilot\nSUPABASE_URL=https://gate.supabase.co\nSUPABASE_SERVICE_ROLE_KEY=gate-placeholder-0123456789\nDATABASE_URL=postgresql://gate@127.0.0.1/gate\nPROCESSING_CACHE_DIR=/opt/dlami/nvme/processing\nVALKEY_URL=valkey://127.0.0.1:6379/0\n' \| docker run --rm -i --network none infrx-runtime:$RELEASE python /app/deploy/preflight.py probe --mode pilot --env-file /dev/stdin` | `"ok": true`. **Today it is false by design**: the pilot routers are not composed (G2), the worker's composition root `python -m infrx.worker` is absent (W3/coordinator), and the store adapters behind `DATABASE_URL` are D2+'s |
| G4 the engine pin | `cd apps/infrx-api && uv run --frozen python -c 'import sys,pathlib; sys.path.insert(0,"deploy"); import preflight; print(preflight.engine_problems(pathlib.Path("../../models/marlin2b/serve.sh"), "pilot"))'` | `[]` (needs W3's `serve.sh` + `serving-version.json`) |
| G5 the local rehearsal | `apps/infrx-api/deploy/rehearse.sh` | `REHEARSAL PASSED`, teardown leaves nothing |
| G6 inputs | `/model-inference/pg_journal_url` DSN decided (login role per D's 0004 note: the pool runs `set role service_role`, D-owned); hosted backup rehearsed by I3B before any hosted migration; G6B can issue a scoped key and revoke one | recorded |

## 1. The window

| # | Where | Step | Record |
|---|---|---|---|
| 1 | coordinator | **Snapshot the root volume** (§7 step 1): `vol=$(aws ec2 describe-instances --instance-ids $INSTANCE --query 'Reservations[0].Instances[0].BlockDeviceMappings[?DeviceName==\`/dev/sda1\`].Ebs.VolumeId' --output text)`; `snap=$(aws ec2 create-snapshot --volume-id "$vol" --description "infrx I2B pre-rollout $RELEASE" --tag-specifications "ResourceType=snapshot,Tags=[{Key=Name,Value=infrx-i2b-pre-${RELEASE:0:12}}]" --query SnapshotId --output text)`; `aws ec2 wait snapshot-completed --snapshot-ids "$snap"` | `$snap`, volume id, UTC |
| 2 | coordinator | **Create the journal DSN parameter** (§5, PROPOSED until now). The value is written to a 0600 file without a trailing newline (preflight refuses a newline) and read by the CLI from it, never typed on a command line: `read -rs DSN` (pasted: not echoed, not in shell history), then `umask 077; printf '%s' "$DSN" > ~/.infrx-dsn; aws ssm put-parameter --name /model-inference/pg_journal_url --type SecureString --value file://$HOME/.infrx-dsn; shred -u ~/.infrx-dsn`. Check: `aws ssm describe-parameters --parameter-filters Key=Name,Values=/model-inference/pg_journal_url --query 'Parameters[].[Name,Type,Version]'` | name, type, version (never the value) |
| 3 | box | `infra/rollout/ssm.sh infra/rollout/steps/10-inventory.sh` (read-only: docker/buildx versions, checkout, units, env **names**, containers, edge volumes, non-loopback listeners) | output |
| 4 | box | `infra/rollout/ssm.sh infra/rollout/steps/20-prepull.sh RELEASE=$RELEASE` - fetch, and pull the engine image `serve.sh` pins at `RELEASE` (outside the window; the working tree is untouched) | pulled digest |
| 5 | box | **Window opens:** `infra/rollout/ssm.sh infra/rollout/steps/30-pause.sh RELEASE=$RELEASE` - the release's edge (pinned Caddy, certificates kept in the `caddy_data` volume) serves maintenance 503, the monolith gateway stops; the previous HEAD is saved as `/var/backups/infrx/pre-$RELEASE.head` | UTC |
| 6 | coordinator | **Hosted migrations** (only after I3B's backup rehearsal; D-owned files, applied as one transaction): `read -rs MIGRATE_DATABASE_URL; export MIGRATE_DATABASE_URL` (paste the session-pooler DSN :5432 of the migrating role, composed from /INFRX-SUPABASE-PROD/* - never typed on a command line, so never in history or `ps`); `run() { docker run --rm -e MIGRATE_DATABASE_URL -v "$PWD/apps/app/supabase/migrations:/migrations:ro" infrx-runtime:$RELEASE python /app/deploy/migrate.py "$@" --dir /migrations; }`; `run plan` (read-only; review applied/pending/sha256), then `run apply --expect <plan digest>`. A refusal (exit 2) or a failure (exit 3, rolled back) changes nothing; if hosted history versions do not match the file versions, `plan` refuses - resolve with D, never by hand | plan output, digest, apply output |
| 7 | box | `infra/rollout/ssm.sh infra/rollout/steps/40-checkout.sh RELEASE=$RELEASE`. **From here to step 8 no engine restart** (no `systemctl restart marlin2b-vllm`, no reboot): the installed unit still passes `--max-num-seqs 32`, which the checked-out `serve.sh` refuses, so the engine would stay down until step 8 or R2. Run 8 right after 7 | HEAD |
| 8 | box | **Cutover:** `TIMEOUT_S=3600 infra/rollout/ssm.sh infra/rollout/steps/50-install.sh RELEASE=$RELEASE MIGRATION_DIGEST=<the digest step 6 applied, or nothing-pending>` - the step refuses to start without it (the box cannot see hosted history, so this is the operator's recorded statement that step 6 ran, not a check of it); `install.sh` in pilot mode with `ENGINE_MAX_NUM_SEQS=32` (the box's value today; pass another through `ssm.sh` once W3's sweep decides it): image built from `RELEASE`, secrets read and probed **inside** that image, env file replaced by one rename, units installed, engine restarted onto its pinned image and media root, runtime restarted, gateway and worker `/readyz`, and only then the edge's normal site. Exit 2 = refused, nothing changed, edge still in maintenance (→ R1); exit 4 = not ready, or a Caddyfile that does not validate with the pinned Caddy (edge unchanged) (→ R2/R3) | image id, backup dir (`/var/backups/infrx/<UTC>-<sha>`), the migration digest it echoed, full output |
| 9 | box | `infra/rollout/ssm.sh infra/rollout/steps/60-verify-local.sh` - units active, `/readyz` 200, each container's image/user/read-only/capabilities as applied, non-loopback listeners only :80/:443/sshd, env names, `INFRX_MODE=pilot`, no `GATEWAY_API_KEY` | output |
| 10 | coordinator | `read -rs INFRX_TEST_KEY; read -rs INFRX_REVOKED_KEY; export INFRX_TEST_KEY INFRX_REVOKED_KEY; export LEGACY_KEY=$(aws ssm get-parameter --with-decryption --name /model-inference/marlin2b_api_key --query Parameter.Value --output text); infra/rollout/verify-external.sh` (G6B's keys pasted, never typed on a command line; the script hands every key to curl in a 0600 header file, never as an argument): sanitized health, `/metrics` `/readyz` 404, ports 8000/8001/6379/2019 unreachable, no key / made-up key / revoked key / legacy shared key 401, scoped key 200 with `Server-Timing`, a link-local media URL refused, a declared oversize body 413. `PENDING` lines are not passes | output, `failures: 0` |
| 11 | coordinator | Record `RELEASE`, image id, engine digest (`serving-version.json`), region us-east-1, `$snap`, backup dir, env names, migration digest; remove the install backups older than the previous accepted release's (`/var/backups/infrx/*`: they hold past env files, secrets included); close the lock | - |

## 2. Revert paths

| Situation | Action |
|---|---|
| R1 - step 8 refused (exit 2) | Nothing was installed. Fix and rerun 8, or abort: `infra/rollout/ssm.sh infra/rollout/steps/91-abort.sh RELEASE=$RELEASE` (previous checkout, previous gateway, edge reopened after `/health`) |
| R2 - step 8 exit 4 or step 9/10 fails, **no pilot request was accepted** | `infra/rollout/ssm.sh infra/rollout/steps/90-revert.sh RELEASE=$RELEASE BACKUP=<backup dir from step 8> ROLLBACK_TO_UNMETERED=no-pilot-request-was-accepted` - previous checkout, the backed-up env/unit/Caddy files, the engine restarted onto its previous unit and healthy **before** the previous gateway (the monolith's `/health` asks the engine, and step 8 may have failed on the engine itself), then `drain.sh resume`: the backup was taken after step 5, so the Caddy file it restores is the maintenance site, and the edge reopens (the release's normal site in front of the restored gateway) only once that gateway is ready. Exit 4 from the step means the restored engine or gateway did not come up: the edge stays in maintenance - fix that (`journalctl -u marlin2b-vllm`, `-u marlin2b-gateway`), then `/root/infrx-deploy-$RELEASE/apps/infrx-api/deploy/drain.sh resume`. The statement is the operator's, not something the script can check: corroborate it first with a read-only count of hosted pilot job and ledger rows since step 8 (zero), and put both in the lock record ([infra/README.md §8](../README.md)) |
| R3 - pilot has accepted work and must stop | `infra/rollout/ssm.sh infra/rollout/steps/95-maintenance.sh RELEASE=$RELEASE` - maintenance 503 until a compatible metered runtime exists (§8 rule 3); `rollback.sh` refuses to put the unmetered monolith back |
| R4 - the host itself | `aws ec2 create-replace-root-volume-task --instance-id $INSTANCE --snapshot-id $snap` then `aws ec2 describe-replace-root-volume-tasks --filters Name=instance-id,Values=$INSTANCE` until `succeeded` (the instance reboots; the instance-store NVMe - weights, media cache - survives a reboot), then step 3. RTO est. 10-20 min (§6), ⚠️ TO BE VERIFIED by I3B. Hosted migrations are additive and are **not** reverted (§8 rule 4) |

## 3. What changes for clients (legacy-account transition)

P-02 is resolved: the four hosted accounts hold USD 0.00 and are dev/e2e accounts that
receive no grant until verified (A1). In pilot mode the shared key
`/model-inference/marlin2b_api_key` is neither read nor written (R51), so any client using
it gets 401 after step 8; scoped keys come from G6B. The parameter itself is left in place
for the revert paths.

## 4. Not in this window

The role/boundary/KMS/IMDS/SSH hardening of [infra/README.md §5](../README.md) (rows
`M-ROLE`, `M-BOUNDARY`, `M-KMS`, `M-IMDS`, `M-SSH`) is its own locked operation with its
own read-only checks; it is not a precondition of this runbook, and this runbook does not
perform it. The Caddy container keeps host networking (so it can reach IMDS; §5 records
that residual risk and its bound).

## Verification log

- 2026-09-22: Written by I2B at the commit that carries it; steps are `bash -n`-clean and
  `ssm.sh`'s base64 round trip is tested (`apps/infrx-api/tests/i/test_rollout.py`). The
  box, AWS and hosted Supabase were not touched; nothing here has run against them.
