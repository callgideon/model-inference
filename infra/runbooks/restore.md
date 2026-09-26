# Restore — hosted Supabase backup/restore, box snapshot

**The hosted project `fcbnscgsymzdykendbrc` has no backup** (I1B inventory: free-tier
settings not observable, nothing we made). The coordinator's rule: migrations 0003-0009 are
applied there only after Part A has run end to end and its check passed. Part A is drilled
locally by `test_i3b_bk01`/`bk01b`/`bk01c`/`bk01d`/`bk02` with the **same tool**,
[`pgrestore.py`](pgrestore.py), the same flags and the same pinned client image; `bk03`
measures the database's own durability boundary. Conventions: [README.md](README.md).

Why a tool and not three `pg_dump` lines: on the pinned Supabase image a plain dump/restore
(1) fails on the template's `public` schema, (2) **leaves `anon` and `authenticated` with ALL
on every restored tenant table** (the template's default privileges apply at CREATE, and
pg_dump does not revoke them) and (3) silently drops the project's trigger on `auth.users`
and 0004's global function default. `pgrestore.py`'s docstring has the detail; `bk01c` shows
the check catching (2) and `bk01b` catching (3).

## Hosted backup and restore rehearsal

Run on the **coordinator host** (Docker, the repository, `make api-env` done). Nothing in
this part writes to hosted: `pg_dump` and the fingerprint are read-only. The restore target
is a throwaway local container.

### A0 Log the operation

Session record entry: purpose "hosted backup + restore rehearsal before the 0003-0009
apply", cost (none: read-only egress of a few hundred KB), rollback (none needed).

### A1 Inputs, names only

```bash
# coordinator host, repository root
AWS="env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN aws --region us-east-1"
PY=apps/infrx-api/.venv/bin/python
REF=fcbnscgsymzdykendbrc
# Session pooler, port 5432 (IPv4; the direct host is IPv6-only from here - I1B). Not the
# transaction pooler (6543): pg_dump needs a session.
HOSTED="host=aws-0-us-east-2.pooler.supabase.com port=5432 user=postgres.$REF dbname=postgres sslmode=require"
BACKUP="$HOME/infrx-backups/hosted-$(date -u +%Y%m%dT%H%M%SZ)"      # outside the repository
IMAGE=$($PY -c 'import runpy; print(runpy.run_path("infra/runbooks/pgrestore.py")["IMAGE"])')
docker image inspect "$IMAGE" >/dev/null     # the pinned client (E2's pin; hosted is 17.6)
$AWS ssm describe-parameters --parameter-filters Key=Name,Option=BeginsWith,Values=/INFRX-SUPABASE-PROD/ \
     --query 'Parameters[].Name' --output text
PGPASS_PARAM=...        # the name above that holds the database password - a NAME, never a value
```

### A2 The password, into the environment only

```bash
read -r PGPASSWORD < <($AWS ssm get-parameter --name "$PGPASS_PARAM" --with-decryption \
                        --query Parameter.Value --output text); export PGPASSWORD
```

`pgrestore.py` passes it to the client container with `-e PGPASSWORD` (the value never
enters a command line) and psycopg reads it from the environment.

### A3 Dump

```bash
$PY infra/runbooks/pgrestore.py dump --conninfo "$HOSTED" --out "$BACKUP"
```

Prints the server version, the schemas and auth tables it took, and `SHA256SUMS`: paste
both into the operation log. The directory is 0700, the files 0600. **Never commit it**:
it holds the project's users' e-mail addresses (four test accounts today); `.gitignore`
refuses `*.dump` and `infrx-backups/` should one land in a checkout. Retention: A9.

### A4 A scratch Supabase to restore into

```bash
LOCALPW=infrx-i3b-local                       # a throwaway local literal, not a secret
docker run -d --name infrx-i3b-restore -p 127.0.0.1:55470:5432 \
       -e POSTGRES_PASSWORD="$LOCALPW" "$IMAGE"
until docker exec infrx-i3b-restore pg_isready -U postgres -q; do sleep 2; done; sleep 5
docker exec infrx-i3b-restore psql -U supabase_admin -d template1 -v ON_ERROR_STOP=1 \
  -c "select pg_terminate_backend(pid) from pg_stat_activity where datname = 'postgres' and pid <> pg_backend_pid()" \
  -c "create database infrx_i3b_hosted_copy template postgres owner postgres"
LOCAL="host=127.0.0.1 port=55470 user=postgres password=$LOCALPW dbname=infrx_i3b_hosted_copy sslmode=disable"
```

`LOCAL` carries `password=` on purpose, the one exception to [README.md](README.md) rule 2:
`check` needs two passwords at once and `PGPASSWORD` holds hosted's, so the throwaway local
literal travels in the conninfo (and hence in `docker run`'s argument list). It is not a
secret - the container is loopback-only and removed in A9 - and nothing else may.

(55470 is unallocated in `tasklocal` (08 §8); any free loopback port works. The database
name must start `infrx_` like E2's, which is what keeps D1's test clock out of production.)

### A5 Restore

```bash
$PY infra/runbooks/pgrestore.py restore --conninfo "$LOCAL" --from "$BACKUP"
```

It verifies `SHA256SUMS` first and refuses a damaged backup (`bk01d`); it refuses, before
writing anything, a target that is not empty - an `infrx` schema, a table in `public` or an
auth row (`bk01e_a`) - and a target that is the backup's own source (host, port, user and
dbname recorded in `meta.json`, resolved as libpq resolves them: `localhost`, a URI, an
omitted port, `hostaddr=` or the PG* environment all count as the same source - `bk01e_b`,
`bk01e_c`). **The emptiness guard is the operative protection:** the source check is best
effort (two DNS names for one server still look different), and a live database is never
empty. Then it restores the auth
rows, empties the template's default privileges, restores the project through the filtered
table of contents, then replays the auth trigger and the global function default.

### A6 Check - restored and checked, not merely listed

```bash
$PY infra/runbooks/pgrestore.py check --source "$HOSTED" --target "$LOCAL"
```

Exit 0 and `"equal": true` is the pass: every row of every project table and of the auth
tables (count + md5), and the relations, columns, column privileges, policies, functions,
triggers, constraints, indexes, views, schema ACLs and default privileges are identical, and the
wallet detectors show no drift. Any line in `problems` is a failed rehearsal: stop.
ACLs are compared by the privileges they grant (R92): an object whose ACL equals its owner's
default is written by pg_dump as nothing and restored as NULL, which is the same set
(`infrx.job_results` is one), while an ACL emptied by a revoke is still a difference.
The migrated schema has no sequence in `public` or `infrx` today (no serial or identity
column); the check still compares sequences, so the first one a migration adds is covered.

### A7 Rehearse the apply on the restored copy

```bash
for f in apps/app/supabase/migrations/000[3-9]_*.sql; do
  docker exec -i infrx-i3b-restore psql -U postgres -d infrx_i3b_hosted_copy \
         -v ON_ERROR_STOP=1 --single-transaction -f - < "$f" || { echo "FAILED at $f"; break; }
done
# The deployed pre-refactor gateway's own statements (it reaches hosted through PostgREST,
# i.e. as service_role), inside a transaction that is rolled back:
docker exec -i infrx-i3b-restore psql -U postgres -d infrx_i3b_hosted_copy -v ON_ERROR_STOP=1 <<'SQL'
begin;
set local role service_role;
select input_usd_per_m, output_usd_per_m from public.models where id = 'nemostation/marlin-2b';
select id, org_id, revoked_at from public.api_keys limit 1;
update public.api_keys set last_used_at = now() where id = (select id from public.api_keys limit 1);
insert into public.usage_events (id, org_id, api_key_id, model_id, status, stream, prompt_tokens,
  completion_tokens, video_seconds, ttft_ms, latency_ms, cached, cost_usd)
  select gen_random_uuid(), org_id, id, 'nemostation/marlin-2b', 200, false, 1200, 34, 2.0,
         700, 900, false, 0.00025000 from public.api_keys limit 1;
rollback;
select name, enabled from infrx.feature_flags order by name;       -- all as 0006 left them
select * from infrx.wallet_reconciliation where ledger_drift <> 0 or reserved_drift <> 0;
SQL
```

Pass: every migration applied, the four legacy statements succeeded, flags
`credit_admission` and `signup_grant` false and `legacy_usd_admission` true, no drift rows.
`bk02` asserts exactly this on a hosted-shaped copy (four users, two keys, one usage row, no
ledger - I1B's counts) and also that every legacy row is value-identical after the apply
(0003 extends `models.limits` with new keys by design; every original key keeps its value).

### A8 Then, and only then, the hosted apply

A separate logged operation (coordinator; the migrate path is D's files through I2B's
fail-closed migrate script, **PENDING I2B**). Record the backup's `SHA256SUMS` and the A6/A7
output with it. If the apply fails or misbehaves, in order of preference:

1. **Code-only rollback = maintenance**, not schema removal ([rollback.md](rollback.md#maintenance)):
   0003-0009 are additive and the old runtime keeps working on them (A7 proves it).
2. **Schema rollback**, only while no CREDIT row exists: D's reverse steps in
   `research/plan/evidence/d/D1R-e538c9a.md` ("Migration / rollback notes") - D's SQL, run by
   the coordinator, never improvised here.
3. **Disaster: restore the A3 backup into a NEW project**, never over the live one
   (`pgrestore.py restore` into the new project's empty `postgres` database, then repoint
   `/model-inference/supabase_url` and `/model-inference/supabase_service_role_key`).
   **[cost] [irreversible for the old project's later writes]** Not rehearsed against a hosted
   target: ⚠️ TO BE VERIFIED (a managed project's `auth` schema is GoTrue's, not the image's).

### A9 Clean up

```bash
docker rm -f infrx-i3b-restore
unset PGPASSWORD
```

Retention: the backup is kept, 0700 on the coordinator host and nowhere else, until the
hosted apply (A8) has been verified and logged with its `SHA256SUMS`; then it is removed.
A longer period is a coordinator decision recorded in the operation log with its reason.

```bash
# only after A8 is verified and logged
rm -rf -- "$BACKUP" && [ ! -e "$BACKUP" ] && echo "backup removed"
```

Windows: `meas. local` (bk01, bk02: E2's stack, ~50-row databases) - dump, restore and
check each complete in a few seconds; the numbers are in the I3B evidence. Hosted through
the pooler: ⚠️ TO BE VERIFIED (P-18) - A3 and A6 timings are recorded by the coordinator's
first run.

## Box snapshot

The box's root volume `vol-091e45c92f7426291` (DeleteOnTermination now false) has one
snapshot, `snap-08732d3ac6376e850` (2026-09-22, before wave 3). The NVMe instance store
(`/opt/dlami/nvme`: weights, today's `usage.jsonl`) is **never** in a snapshot and is empty
after any stop/start or volume replacement.

### B1 List

```bash
# coordinator host
$AWS ec2 describe-snapshots --owner-ids self --filters Name=volume-id,Values=vol-091e45c92f7426291 \
     --query 'Snapshots[].[SnapshotId,StartTime,State,Description]' --output table
```

### B2 Snapshot before any change

**[cost]** ≈ $0.05/GB-month of changed blocks (session-02 operation log).

```bash
SNAP=$($AWS ec2 create-snapshot --volume-id vol-091e45c92f7426291 \
        --description "infrx pre-op $(date -u +%FT%TZ)" \
        --tag-specifications 'ResourceType=snapshot,Tags=[{Key=infrx,Value=pre-op}]' \
        --query SnapshotId --output text)
$AWS ec2 wait snapshot-completed --snapshot-ids "$SNAP"; echo "$SNAP"
```

### B3 Restore the root volume

**[irreversible for the instance store] [cost]** EC2 replaces the root volume in place (same
instance, ENI, public IP) and restarts the instance; the replaced volume is kept, detached,
so this is itself reversible from that volume.

```bash
TASK=$($AWS ec2 create-replace-root-volume-task --instance-id i-0e8449a4ffca29bab \
        --snapshot-id "$SNAP_TO_RESTORE" --query ReplaceRootVolumeTask.ReplaceRootVolumeTaskId --output text)
until [ "$($AWS ec2 describe-replace-root-volume-tasks --replace-root-volume-task-ids "$TASK" \
          --query 'ReplaceRootVolumeTasks[0].TaskState' --output text)" = succeeded ]; do sleep 15; done
$AWS ec2 wait instance-status-ok --instance-ids i-0e8449a4ffca29bab
```

### B4 Verify

[restart.md triage](restart.md#triage), then the engine's cold start (weights re-download,
[restart.md](restart.md#engine)), then an unauthenticated `POST /v1/chat/completions` must
still answer 401 through Caddy, and [reconcile.md](reconcile.md#drift). Window: `est.`
10-20 min (infra/README.md §6) plus the weight download - ⚠️ TO BE VERIFIED (P-18).

## Model artifacts

I8 (RV-09): the weights live on instance-store NVMe (`/opt/dlami/nvme/marlin2b`), which a
stop/start wipes; the root-volume snapshot does not hold them. The durable copy is a mirror
with a digest manifest (`infra/runbooks/artifacts.py`): every served file (weights,
processor, tokenizer, chat template, configs) by sha256, the pins of
`models/marlin2b/serving-version.json` checked before anything is uploaded, the engine
image by registry digest, the runtime image id and the release it is built from, the env
file by names. **Input: the approved prefix** - `llm-bootcamp-641134885443` is another
project's bucket ([infra/README.md §6](../README.md)); proposed
`s3://llm-bootcamp-641134885443/infrx/artifacts/marlin2b/fd111fca/`, pending the owner's
approval (P-25).

| # | Step (coordinator, one op each) | Service impact | Records |
|---|---|---|---|
| M1 | `infra/rollout/ssm.sh infra/rollout/steps/80-mirror-artifacts.sh RELEASE=<deployed> MIRROR_URL=<approved prefix>` | none (reads the NVMe; ~5 GB PUT in-region) | manifest sha256, `N/N match the manifest`, bucket versioning/public-access/encryption as readable |
| M2 | `infra/rollout/ssm.sh infra/rollout/steps/81-restore-artifacts.sh RELEASE=<deployed> MIRROR_URL=<prefix> MODE=fetch CLEANUP=1` | none | `fetch_s`, `verify_s`, `EQUAL` |
| M3 | **Maintenance window**: `TIMEOUT_S=3600 infra/rollout/ssm.sh infra/rollout/steps/81-restore-artifacts.sh RELEASE=<deployed> MIRROR_URL=<prefix> MODE=swap` | full outage while the engine loads (single GPU) | `fetch_s`, `verify_s`, `engine_load_s` (the cold start), `warm_text_s`, `warm_video_s`, `runtime_ready_s`, `first_usable_s`, `RESTORE_ID` |
| M4 | host: `infra/rollout/verify-journey.sh`, then `drift.py --request-id <id>` | one test job | the journey, `SETTLED` |
| M5 | red anywhere after the swap: `81-restore-artifacts.sh ... MODE=undo RESTORE_ID=<id>`; green: `86-cleanup.sh RESTORE_ID=<id> DRY_RUN=0` after M4 | as M3 | - |

Host loss end to end (RTO) = replacement instance + runtime install (release bundle, W1-W10)
+ M3's `fetch_s + verify_s + engine_load_s + first_usable_s`; ⚠️ TO BE VERIFIED (P-18) until
M3 has run. The processor files have no pin in serving-version.json yet: the manifest
records their digests (`unpinned_processor_files`), WR-I8-5 adds the pins.

## Backup and PITR policy

What the operator side can read, and nothing more (I8): the Management API's backup list
(PITR, WAL-G, completed backups and their age) and pooler config (pool size, max clients),
plus the coordinator's own logical dumps (A3). **Input: a Supabase personal access token**
(`SUPABASE_ACCESS_TOKEN`, read with `read -rs`; the account with the project) - without it
the read is BLOCKED and only the local dumps are reported.

```bash
# coordinator host, read-only
read -rs SUPABASE_ACCESS_TOKEN; export SUPABASE_ACCESS_TOKEN
apps/infrx-api/.venv/bin/python infra/runbooks/supabase_policy.py     # JSON: backups, pooler, rpo
unset SUPABASE_ACCESS_TOKEN
```

The printed `rpo` line is derived only from what was read: PITR on - minutes (est.); daily
backups - at most 24 h (est.); neither - the age of the newest manual dump, unbounded
between dumps (the only dump on record is `hosted-20260924T050746Z`). Enabling PITR is a paid
plan change, separately authorized (infra/README.md §6). The database recovery drill is
Part A above (dump -> scratch restore -> `pgrestore.py check` equal), timed by the
coordinator: that time is the database's RTO lower bound.

### Dump cadence while PITR is off

P-25 (decided 2026-09-25, research/plan/15-pending-inputs.md "Decisions 2026-09-25"): until
the policy read above shows PITR on, the hosted database's recovery point is the newest
verified logical dump, so the coordinator takes one:

- **before every migration or rollout** (the release's W6 is that dump for a rollout that
  applies migrations; a rollout without one still takes A3 first);
- **daily during E4C**;
- **keeping the 7 newest** verified dumps under `$HOME/infrx-backups/`, removing older ones as
  A9 removes them (never committed; they hold the project's users' e-mail addresses).

"Verified" is Part A's restore check: A3 dump, A4/A5 restore into a scratch copy, A6
`pgrestore.py check` exit 0 with `"equal": true`, and the dump's `SHA256SUMS` in the
operation log. A dump that was not restored and checked does not count toward the 7. The
dump itself is operator-run from the coordinator host exactly as A1-A6 print it (names
only; the password as A2 reads it). Enabling PITR is a separate paid decision
(infra/README.md §6); when it is on, this cadence is revisited, not assumed.

## Other layers

| Layer | Backup | Recovery |
|---|---|---|
| Scheduling index (Valkey) | none, by design | rebuilt from PostgreSQL: [index-loss.md](index-loss.md#index-loss) |
| Result/media objects | bucket versioning, PENDING M3 (no S3 adapter or bucket yet) | re-derivable while the source object exists |
| Trace content | T's (versioned bucket, spool on EBS) | T's runbook |
| Usage logs of the legacy gateway | none: instance store today (I1B) | I2B moves `USAGE_LOG` to persistent storage; until then a stop/start loses them - **do not stop the box without copying them** |

## Verification log

- 2026-09-22 (I3B.c): Part A written from, and drilled by, `test_i3b_bk01`-`bk02` through
  `pgrestore.py` on the pinned image (E2's stack): the three plain-restore defects above were
  found by that drill and are handled by the tool. Nothing has been run against hosted or the
  box; all hosted/box windows are ⚠️.
- 2026-09-23 (I3B fix round, RS-1): A5's "never over live data" is now enforced by the tool
  (source identity + empty-target guard before any write), drilled by `bk01e_a`/`bk01e_b`.
- 2026-09-23 (RS-4): A4 states LOCAL's password as the one deliberate non-secret exception;
  the tool drops pg_restore's DETAIL/CONTEXT lines (row data) from its errors (`rb06`).
- 2026-09-23 (RS-5): A9 gains the backup's retention and removal step; `.gitignore` refuses
  `*.dump` and `infrx-backups/`.
- 2026-09-23 (confirmation fold-in, RS-1 residual): the source identity is resolved like
  libpq (bk01e_c); A5 names the emptiness guard as the operative one.
- 2026-09-23 (I3B follow-up, R92): the check normalises NULL ACLs with `acldefault()`
  (relations, sequences, functions, schemas); the E3B2 gate's bk01 red on `infrx.job_results`
  was that spelling, not a lost privilege. Drilled by `bk01g` and `bk01h` (put_result and
  read_result still work as service_role after a restore) on the D harness.
- 2026-09-23 (I3B follow-up round 2): column privileges are a compared family (RST-3,
  0001's column grant on `public.profiles`); bk01f damages a schema grant and a column grant,
  bk01g a sequence grant, and each is named (RST-1/RST-3/RST-4). The D harness runs this
  drill on the Supabase image only; the plain image is refused by name (RST-2).
- 2026-09-24 (I8): "Model artifacts" (mirror, fetch/verify, timed swap, undo; RTO parts named)
  and "Backup and PITR policy" (supabase_policy.py; RPO derived only from what it reads)
  added. Not run: the mirror prefix and the access token are inputs (P-25).
- 2026-09-26 (P25-ENACT): "Dump cadence while PITR is off" added from P-25 (decided
  2026-09-25): a verified dump before every migration or rollout and daily during E4C, the
  7 newest kept, verified by A6. Not run.
