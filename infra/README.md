# Pilot deploy design — free single-GPU pilot

Living design for **I2** to implement. I1 created nothing: every row marked
PROPOSED does not exist. Observed state, historical claims and proposals are
separated in the I1 inventory, `research/plan/evidence/i/I1-<sha>.md`, which is
the factual basis for this document. Conventions per `CLAUDE.md`: `est.` stays
`est.` until measured, `meas.` carries a source, unknowns are
`⚠️ TO BE VERIFIED` with the estimation method, prices only from
[cloud-pricing.md](../research/cross-cutting/cloud-pricing.md).

Authority: [accepted scope](../research/plan/00-decisions-and-scope.md),
[contracts v1](../research/plan/01-contracts.md),
[durable protocols](../research/plan/02-durable-protocols.md),
[execution protocol](../research/plan/03-execution-protocol.md),
[verification](../research/plan/04-verification.md).
ClickHouse deployment content is owned by T, SQL by D, serve flags by W; this
document only places their artifacts on a host and orders their hooks.

## 1. Environments and the deployment lock

| Environment | Purpose | Compute | PostgreSQL | Object store | Secrets | Who deploys |
|---|---|---|---|---|---|---|
| `local` | Layer 1/2 tests: fakes, real local PG/CH/Valkey/S3-compatible, migrations, RLS | developer host / worktree | local container, per-worktree database name | local container, per-worktree prefix | local `.env` files, never SSM | any track (E2 owns the compose file) |
| `staging` (allocated) | Layer 3 rehearsal of a deploy, migration and recovery drill | PROPOSED allocated GPU instance, separate instance id and EIP | PROPOSED separate Supabase project or schema | PROPOSED separate bucket/prefix | SSM prefix `/infrx/staging/*` PROPOSED | I2/I3 holding the lock |
| `pilot` | The single-GPU free pilot serving real keys | `i-0e8449a4ffca29bab` (g6e.2xlarge, us-east-1d) OBSERVED | Supabase `fcbnscgsymzdykendbrc`, us-east-2 OBSERVED via SSM name only | PROPOSED `infrx-media`, `infrx-traces` | existing `/model-inference/*` + PROPOSED names in §5 | I2/I3 holding the lock, coordinator-authorized |

**Single deployment lock.** One holder at a time may mutate `staging` or
`pilot`. The lock is a coordinator record, not a technical mechanism: the holder
appends `{environment, holder task, base SHA, start UTC, end UTC, actions}` to
their evidence report before touching anything, and no other task runs an
installer, migration or restart in that environment while it is open. A task
without the lock is limited to read-only verbs (the I1 allowlist). Local
integration needs no lock; each worktree uses distinct ports, database names,
object prefixes and temp directories per the execution protocol.

**Environment for every mutable operation** is assigned in the
required-vs-existing matrix of the I1 evidence report; no mutation is performed
by I1.

## 2. Process layout on the pilot host

Current units are `marlin2b-vllm.service`, `marlin2b-gateway.service` and a
`caddy` docker container (OBSERVED in the repository; running state observed
only through the public endpoint). Target layout:

| Process | Unit (PROPOSED unless noted) | Listens | Owner track | Working paths | Needs persistent EBS |
|---|---|---|---|---|---|
| vLLM engine | `marlin2b-vllm.service` (exists) | 127.0.0.1:8000 | W | weights `/opt/dlami/nvme/marlin2b`; torch/compile cache | weights **no** (re-downloadable, instance store is correct); compile cache **yes** → `/var/lib/infrx/vllm-cache` so a stop/start does not recompile |
| Gateway | `marlin2b-gateway.service` (exists) | 127.0.0.1:8001 | G | usage spill, request logs | **yes** → `/var/lib/infrx/usage/` (today `usage.jsonl` / `usage_failed.jsonl` are on instance store and are lost on stop) |
| Worker (claim → engine → journal) | `infrx-worker.service` | none | W + Q | lease/journal client only | no (state is in PG) |
| Preparation / media | `infrx-prepare@1..2.service` or a bounded pool inside the worker; 2 active preparations per host per contracts v1 | none | M | staged payloads, transcodes, media cache | **yes** → `/var/lib/infrx/media/` (immutable staged source must survive a restart until result expiry) |
| Trace spool writer + shipper | `infrx-trace-shipper.service` | none | T | spool segments, parked files | **yes** → `/var/lib/infrx/traces/` (cap 10 GiB, refuse below 2 GiB free, no copytruncate) |
| TLS front door | `caddy` docker container (exists) | 0.0.0.0:80,443 | I | `caddy_data`, `caddy_config` docker volumes | **yes** for `caddy_data` (ACME account and certificate material) |

Isolation rules for I2: the gateway process never writes engine or spool paths;
the spool writer is the only writer under `/var/lib/infrx/traces`; media
preparation runs as a separate process so a decode stall cannot block the
gateway event loop; no process runs as root. Readiness (`/readyz`, protected)
reports per-component state; public `/health` stays generic per contracts v1.

Root volume budget on the current box: 300 GiB gp3 (OBSERVED,
`vol-091e45c92f7426291`, 3000 IOPS / 125 MiB/s, unencrypted,
`DeleteOnTermination=true`). Allocation `est.`: OS + DLAMI ~120 GiB, trace spool
10 GiB cap, media/staging 60 GiB, compile cache 10 GiB, journal spill and logs
5 GiB — fits with headroom. `DeleteOnTermination=true` and the absence of any
snapshot (OBSERVED) are the two facts that make §6 mandatory before I2 deploys.

## 3. Cross-region journal latency — probe design

GPU compute is us-east-1d; PostgreSQL authority is Supabase us-east-2 through
the pooler `aws-0-us-east-2.pooler.supabase.com:5432` (HISTORICAL CLAIM from
HANDOFF.md §2; not re-observed by I1 — reading the Supabase project was outside
the allowed verbs). `04-verification.md` makes this an explicit early
measurement gate; `02-durable-protocols.md` §6 batches journal appends at most
50 ms before commit and requires commit before relay, so the commit round trip
is on the client-visible path of every streamed event.

### 3.1 What to measure

| Metric | Definition | Why |
|---|---|---|
| `connect_ms` | TCP + TLS + pooler session establishment, cold | sizes worker startup and pool refill, not the steady path |
| `commit_rtt_ms` | `BEGIN` → append of one batch of journal chunks (representative: 8 events, ≤ 64 KiB total) → `COMMIT` returning, measured client-side | the term added to every SSE event after the 50 ms window |
| `append_rate_per_s` | sustained committed batches/s from one worker with 8 concurrent jobs | proves the engine (TPOT 6–8 ms `meas.`, HANDOFF §4) cannot outrun the journal |
| `first_progress_ms` | ingress → first committed progress event, including admission transaction | the contracts v1 SSE target is measured *including persistence* |
| `terminal_txn_ms` | the single terminal transaction (outcome + usage + settlement + releases + outbox) | it gates success reporting |

Also record: pooler mode (transaction vs session), pool size, whether prepared
statements are usable through the pooler, and packet loss / retransmits during
the run. Report p50/p95/p99 with sample counts, not means.

### 3.2 Sample sizes and procedure

Run from the pilot AZ (us-east-1d) against the real pooler, against a scratch
schema owned by D, never against live tables. Warm the pool, discard the first
50 samples, then: ≥ 2,000 `commit_rtt_ms` samples at concurrency 1, ≥ 2,000 at
concurrency 8, and ≥ 300 `terminal_txn_ms` samples; repeat the whole run at
three separated times of day to expose cross-region variance. A single burst is
not evidence; `04-verification.md` already rejects small samples for tail
claims.

### 3.3 Pass / fail thresholds

Derived from contracts v1 (SSE first progress target 2 s including persistence;
journal batch window 50 ms; lease 120 s with 40 s heartbeat) — the rule is that
the 50 ms batch window, not the network, must remain the dominant term:

| Verdict | Condition | Action |
|---|---|---|
| Pass | `commit_rtt_ms` p50 ≤ 25, p95 ≤ 50, p99 ≤ 150; `first_progress_ms` p95 ≤ 800; sustained ≥ 160 committed batches/s per host at concurrency 8 | keep PG in us-east-2 for the pilot; record as `meas.` |
| Marginal | p95 in 50–120 ms, or first progress p95 in 800–1,500 ms | pilot may proceed only with a recorded contract note and the locality work scheduled before fleet; no throughput claim above the measured rate |
| Fail | p95 > 120 ms, or `first_progress_ms` p95 > 1,500 ms, or append rate below the engine's event rate | do not claim the pilot envelope; take a §3.5 option before I2 release |

These thresholds are `est.` engineering limits proposed by I1; the coordinator
confirms them against contracts v1 before I2 treats them as a gate.

### 3.4 Who runs it, when, with which secrets

I2 runs the probe **before** any release claim, as the first action under the
deployment lock on a fresh allocated environment; E4 re-runs it as part of
pilot evidence and publishes the distribution. The probe reads the connection
string at runtime from SSM (`/INFRX-SUPABASE-PROD/db_password` plus the pooler
host, or the PROPOSED single `/model-inference/pg_journal_url`), holds it only
in process memory, and writes only aggregate timings — never the URL, password
or any row content — into its evidence report. The probe script lives under
`infra/` and takes the DSN from an environment variable name, never a literal.

### 3.5 Locality options if it fails

| Option | Change | Cost / risk |
|---|---|---|
| A. PG authority to us-east-1 | new Supabase project (or self-managed PG) in us-east-1; migrate schema, data and auth | console auth and existing balances move with it; DEC-08 forbids re-debiting history, so the migration must copy the ledger verbatim; largest change, best latency |
| B. GPU to us-east-2 | relaunch the pilot host in us-east-2 | g6e AZ offerings in us-east-2 ⚠️ TO BE VERIFIED (method: `ec2 describe-instance-type-offerings --region us-east-2`; I1 queried us-east-1 only); new EIP and DNS record; weights re-download |
| C. Widen the batch window | 50 ms → 100–200 ms, coalesce by bytes as well as time | needs a coordinator contract revision; raises event lag, keeps one region; cheapest |
| D. Reduce commit count | one commit per N events with a bytes trigger, terminal transaction unchanged | same contract revision as C; does not help `first_progress_ms` |

Rejected: buffering journal events locally and acknowledging before the PG
commit. DEC-09 and durable protocol §6 require commit before relay; a local
buffer would make output ownership unprovable after loss.

## 4. Storage placement rule

Durable-by-default: anything whose loss would break accounting, an accepted job,
an output journal, a settled ledger row or a retention promise lives in
PostgreSQL (authority) or S3 (immutable content). The host keeps only
rebuildable caches and bounded spools. The I1 evidence report carries the
per-artifact durable/ephemeral table; the rule for I2 is:

| Class | Location | Loss on host stop |
|---|---|---|
| Accepted jobs, leases, journal, ledger, holds, feedback | PostgreSQL (us-east-2) | none |
| Immutable staged payloads, results, trace content | S3 (PROPOSED `infrx-media`, `infrx-traces`) | none |
| Trace spool segments (post-fsync), usage spill, compile cache, ACME material | `/var/lib/infrx/*` on the root EBS volume | none on stop; **total on termination** while `DeleteOnTermination=true` |
| Weights, transcode scratch, engine working files | `/opt/dlami/nvme/*` (instance store) | total, by design; rebuildable |

## 5. Secret injection

No secret value appears in the repository, a log, an evidence report or a
process argument. The runtime reads SSM parameter **names**; values arrive
through the instance role at install or start time.

| Parameter name | State | Consumer |
|---|---|---|
| `/model-inference/marlin2b_api_key` | OBSERVED (SecureString) | legacy gateway key; must map to an explicit org/key or be disabled at cutover (contracts v1) |
| `/model-inference/supabase_url` | OBSERVED (String) | gateway auth |
| `/model-inference/supabase_service_role_key` | OBSERVED (SecureString) | gateway auth / usage rows |
| `/model-inference/hf_token` | OBSERVED (SecureString) | weight download |
| `/model-inference/pg_journal_url` | PROPOSED | worker/gateway journal + ledger DSN (I2) |
| `/model-inference/clickhouse_dsn`, `/model-inference/clickhouse_writer_password` | PROPOSED, names owned by T | trace projection |
| `/model-inference/anthropic_api_key` | PROPOSED | judge, J, live budget default zero |
| `/model-inference/price_table_version` | PROPOSED (String, not a secret) | admission refuses without a price snapshot |

**Fail closed in pilot mode.** `install.sh` today treats a missing SSM
parameter as a warning and writes a partial env file, so the gateway can come
up with the legacy key alone — no Supabase auth and no usage rows (OBSERVED in
`apps/infrx-api/deploy/install.sh`). That behaviour is acceptable for a
single-user dev box and unacceptable once promotional credits are enforced. I2
must add an explicit mode:

- `INFRX_MODE=dev` keeps today's permissive behaviour and refuses to bind a
  public interface.
- `INFRX_MODE=pilot` asserts at startup that the auth backend, journal DSN,
  price table version and ledger reachability are all present, and **exits
  non-zero** otherwise; the installer refuses to write an env file that is
  missing any pilot-required name, instead of warning.
- No default falls back to unauthenticated or unmetered: an absent
  authentication backend is a startup failure in pilot mode, not an open door.
- `/readyz` stays 503 until admission, ledger and journal all answer; the
  systemd unit does not report `active` before that.

The instance role `bootcamp-instance-role` currently grants
`ssm:GetParameter*` on `Resource: "*"` and broad `ec2:*Instances` with
`iam:PassRole` on itself (OBSERVED). I2 should narrow parameter access to
`/model-inference/*` and drop the instance-lifecycle statements from the pilot
role; both are configuration changes for I2 under the lock, not I1.

## 6. Backup and restore per durable layer

No EC2 snapshot, AMI or AWS Backup plan exists in the account (OBSERVED). All
values below are `est.` placeholders until I3 measures them; I3 owns the drills
and replaces them with `meas.`.

| Layer | Backup mechanism | RPO | RTO | Restore test owner |
|---|---|---|---|---|
| PostgreSQL (jobs, leases, journal, ledger, holds, feedback) | Supabase-managed backups; PITR needed. Plan and retention **not observable from this host** (Supabase Management API is outside I1's allowed verbs) | `est.` ≤ 24 h today, target ≤ 5 min with PITR | `est.` ⚠️ TO BE VERIFIED (method: timed restore of a scratch project by I3) | I3 |
| Result / media objects (PROPOSED `infrx-media`) | S3 durability + versioning enabled at creation + lifecycle per retention policy (results 24 h, processing cache 7 d) | 0 | minutes `est.` | I3 |
| Trace content (PROPOSED `infrx-traces`) | S3 versioning + per-object retention tags (≤ 90 d content, 13 mo metadata) | 0 | minutes `est.` | I3 with T |
| Trace spool on EBS | not backed up by design; durability begins after fsync, host/volume loss is out of scope per durable protocol | `est.` ≤ 2 s of unsynced events | shipper drain time, `est.` ⚠️ TO BE VERIFIED | I3 with T |
| ClickHouse projections | rebuildable from S3 content + PG truth; plus T's own backup to S3 | `est.` 24 h | `est.` ⚠️ TO BE VERIFIED | T, drilled by I3 |
| Pilot host root volume | EBS snapshot before every deploy; `DeleteOnTermination=true` must be flipped or the snapshot is the only copy | one deploy cycle | `est.` 10–20 min from snapshot | I2 creates, I3 restores |
| Weights | reproducible from Hugging Face (gated) and the S3 mirror prefix | n/a | download time, `est.` ⚠️ TO BE VERIFIED (no measured cold start exists) | I2 |

## 7. Migration ordering hooks

The deploy script contains no SQL and no DDL. It invokes, in order, under the
deployment lock:

1. **Snapshot** the root volume and record the snapshot id in the lock record.
2. **Pause admission** (maintenance mode accepting no new work, existing jobs
   draining).
3. **D's PostgreSQL migrations** — numbered files, one per change, applied
   through the pooler URL. D owns the files and the sequence numbers; the
   deploy calls D's entry point and fails the deploy on a non-zero exit.
   Additive expand/contract only, forward-compatible with the currently
   deployed runtime.
4. **T's ClickHouse DDL** — same contract, T's entry point, after PG so a trace
   projection never references a column that does not exist yet.
5. **Restart** engine, worker, preparation, spool shipper, gateway in that
   order; each waits for the previous readiness signal.
6. **Resume admission** only after `/readyz` reports every component healthy.

A migration that is not forward-compatible with the running runtime is a
two-deploy change, never one.

## 8. Rollback rule

From [03-execution-protocol.md](../research/plan/03-execution-protocol.md):

1. Pause admission; drain and fence in-flight work; reconcile durable jobs.
2. Deploy the previous **compatible** runtime. Rebuild queue indices from
   PostgreSQL; disabling Valkey is not a data migration and cannot discard jobs.
3. If no compatible runtime exists, serve **maintenance 503** until one does.
   Once enforced credits are enabled, reverting to the original unmetered
   gateway is unsafe and is not a rollback option — it would serve work that
   cannot be reserved or settled.
4. Never drop ledger, journal or job tables to roll back code. Feature-disable
   may stop new activity but preserves durable state and retention obligations.
5. Every rollback appends to the lock record: trigger, actions, durable state
   before and after, and whether any accepted job changed state.

## 9. Deferred to I4 (not pilot scope)

ALB across ≥ 2 AZs, ACM certificate and WAF (Caddy with Let's Encrypt stays for
the pilot — OBSERVED issuer on the live endpoint); Auto Scaling group and launch
template; baked AMI; on-demand capacity reservation; ElastiCache Valkey as a
scheduling index; multi-AZ or second worker. I4 starts after I3 and E4, from
measured pilot data, per the handoff. Capacity purchases are separately
authorized and are not implied by any design in this document.

## Verification log

- 2026-09-20: Authored by I1 from read-only AWS observation, the live public
  endpoints and the repository's deploy files. No resource was created and no
  configuration changed. Every threshold here is `est.` until I2/I3/E4 measure
  it; the cross-region journal probe (§3) and the fail-closed pilot mode (§5)
  are the two items that gate an I2 release claim.
