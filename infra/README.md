# Pilot deploy design — free single-GPU pilot

Living design for **I2** to implement. I1 created nothing: every row marked
PROPOSED does not exist. Observed state, historical claims and proposals are
separated in the I1 inventory,
[`research/plan/evidence/i/I1-4e052f4.md`](../research/plan/evidence/i/I1-4e052f4.md)
(one report for I1, updated in place), which is the factual basis for this
document. Every cross-reference below names the exact row or section it means. Conventions per `CLAUDE.md`: `est.` stays
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
| `staging` (allocated) | Layer 3 rehearsal of a deploy, migration and recovery drill | PROPOSED allocated GPU instance, separate instance id and EIP | PROPOSED separate Supabase project or schema | PROPOSED separate bucket/prefix | SSM prefix `/infrx-staging/*` PROPOSED — a **sibling** of `/model-inference/*`, deliberately not nested under it (see §5: a pilot role narrowed to `/model-inference/*` would otherwise read staging secrets, and a staging host reusing that role would read the pilot journal DSN) | I2/I3 holding the lock; **allocation itself is a coordinator action** |
| `pilot` | The single-GPU free pilot serving real keys | `i-0e8449a4ffca29bab` (g6e.2xlarge, us-east-1d) OBSERVED | Supabase `fcbnscgsymzdykendbrc`, **us-east-2 — HISTORICAL CLAIM (HANDOFF.md §1)**; only the `/INFRX-SUPABASE-PROD/*` parameter *names* are OBSERVED, and a parameter name cannot reveal a project ref or region | PROPOSED `infrx-media`, `infrx-traces` | existing `/model-inference/*` + PROPOSED names in §5 | I2/I3 holding the lock, coordinator-authorized |

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
by I1. Two operations this document proposes are **not** assigned to an
implementation task because they are outside a task's authority: allocating the
`staging` environment (a coordinator action per
[03-execution-protocol.md](../research/plan/03-execution-protocol.md)) and
enabling Supabase PITR (§6 — a paid plan change on the authoritative production
database, **separately authorized**, never implied by this design). Both carry
that marker in the matrix.

## 2. Process layout on the pilot host

Current units are `marlin2b-vllm.service`, `marlin2b-gateway.service` and a
`caddy` docker container (OBSERVED in the repository; running state observed
only through the public endpoint). Target layout:

Every unit marked PROPOSED below is created by **I2**, in `staging` then
`pilot` (matrix row *"New systemd units (worker, preparation, trace shipper)"* in
§5 of the I1 evidence report carries the same assignment); the owner track named
in the table owns what runs *inside* the unit, not the unit file.

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
Drain grace: `marlin2b-vllm.service` today has `ExecStop=docker stop` with no
`-t`, so in-flight generation is killed after the 10 s default (OBSERVED in the
unit file). I2 sets `docker stop -t 120` with a matching `TimeoutStopSec` on
every unit that can hold an accepted job, so a deploy drains instead of
truncating; matrix row *"`ExecStop=docker stop -t 120` + matching
`TimeoutStopSec` …"* carries the owner and environment.

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

**Client round trips are fixed, not incidental.** At a cross-region RTT this
choice alone decides Pass from Marginal, so the headline `commit_rtt_ms` is
measured with **exactly one** client round trip: a single-statement append that
commits implicitly (or an explicitly pipelined `BEGIN`/`INSERT`/`COMMIT` sent in
one flush). The three-round-trip form (separate `BEGIN`, `INSERT`, `COMMIT`
waits) is measured as a **secondary variant** and reported beside it, never
merged into the headline. Every reported number states its round-trip count;
`terminal_txn_ms` is inherently multi-statement and records its own count.

Also record: pooler mode (transaction vs session), pool size, whether prepared
statements are usable through the pooler, and packet loss / retransmits during
the run. Report p50/p95/p99 with sample counts, not means.

**Probe both pooler ports.** The `:5432` above is the port HANDOFF.md line 54
uses, which on Supavisor is **session** mode — correct for `supabase db push`,
but a long-lived session per connection is not the worker's likely path. Port
**`6543` is transaction mode**, which is what a many-worker journal client would
normally use and where prepared statements may be unavailable. Both are measured
and reported separately; if they differ materially, the mode the worker will
actually use is the one the verdict ladder is evaluated against, and the choice
is recorded in the evidence report.

### 3.2 Sample sizes and procedure

**Host and target are fixed.** The number that gates release measures the pilot
path: the probe runs **on the pilot GPU host** `i-0e8449a4ffca29bab`
(us-east-1d) — the same AZ, ENI and pooler the worker will use — against the
**authoritative pilot PostgreSQL**, writing only to D1's scratch schema, never a
live table. If `staging` is allocated first, the identical script runs there as a
rehearsal against the staging database using `/infrx-staging/pg_journal_url`;
a staging number is a rehearsal, never the release evidence, because it does not
measure the pilot AZ↔pooler path. Warm the pool, discard the first 50 samples,
then: ≥ 2,000 `commit_rtt_ms` samples at concurrency 1, ≥ 2,000 at
concurrency 8, and ≥ 300 `terminal_txn_ms` samples; repeat the whole run at
three separated times of day to expose cross-region variance. A single burst is
not evidence; `04-verification.md` already rejects small samples for tail
claims.

**What each metric needs, and when it can be measured.** `connect_ms`,
`commit_rtt_ms` and `terminal_txn_ms` need only D1's tables replicated in the
scratch schema, so they are measurable as soon as D1 exists — that is the first
run, and the verdict ladder's `commit_rtt_ms` clauses can be evaluated from it
alone. `first_progress_ms` and `append_rate_per_s` include the real admission
and worker path, so they are **not** measurable by a standalone script: they are
measured once G/Q/W are integrated, by E4 on the same host, and until then the
ladder is evaluated on the `commit_rtt_ms` and `terminal_txn_ms` clauses with
the two missing metrics recorded as "not run", never as passed.

**Scratch schema ownership.** D1 authors the scratch-schema SQL and its drop
(D owns all SQL); **I2 executes both under the deployment lock**, because D1
holds no lock. The schema is created immediately before the run and dropped
immediately after; its name is recorded in the lock record.

### 3.3 Pass / fail thresholds

Derived from contracts v1 (SSE first progress target 2 s including persistence;
journal batch window 50 ms; lease 120 s with 40 s heartbeat) — the rule is that
the 50 ms batch window, not the network, must remain the dominant term:

Evaluate in this order and stop at the first match, so **every** result lands in
exactly one verdict (the earlier table left gaps — e.g. p50 38 ms with p95
47 ms matched nothing):

| Order | Verdict | Condition | Action |
|---|---|---|---|
| 1 | **Fail** | `commit_rtt_ms` p95 > 120 ms, **or** `first_progress_ms` p95 > 1,500 ms, **or** `terminal_txn_ms` p95 > 600 ms, **or** sustained append rate < 120 committed batches/s per host at concurrency 8 | do not claim the pilot envelope; take a §3.5 option before I2 release |
| 2 | **Pass** | `commit_rtt_ms` p50 ≤ 25 ms **and** p95 ≤ 50 ms **and** p99 ≤ 150 ms, **and** `first_progress_ms` p95 ≤ 800 ms, **and** `terminal_txn_ms` p95 ≤ 250 ms, **and** sustained ≥ 160 committed batches/s | keep PG in us-east-2 for the pilot; record as `meas.` |
| 3 | **Marginal** | everything else (by construction: not Fail, not Pass) | pilot may proceed only with a recorded contract note and the locality work scheduled before fleet; no throughput claim above the measured rate |

The append-rate numbers are derived, not free-hand: the 50 ms batch window means
one committed batch per job per 50 ms, i.e. **20/s per concurrent job**, so 8
concurrent jobs need **160/s** to keep the window rather than the database as
the limiting term; < 120/s means the host cannot sustain the window for even 6
of the 8 jobs. "The engine's event rate" in the earlier wording is exactly this
160/s figure.

`terminal_txn_ms` is derived the same way: the terminal transaction is
inherently multi-statement and its statements are dependent, so it costs
`est.` 5 round trips rather than one — at the Pass `commit_rtt_ms` p95 of 50 ms
that is ≤ 250 ms, and > 600 ms means the network, not the work, dominates the
transaction that gates success reporting.

**I2 cannot reach a Pass verdict, and that is expected.** Both the Pass and Fail
rows contain `first_progress_ms` and `append_rate_per_s` clauses, which §3.2 shows
are not measurable before G/Q/W are integrated. So I2's outcome is at best
*"not Fail on the `commit_rtt_ms` and `terminal_txn_ms` clauses, two metrics not
run"* — never "Pass" — and **E4 is the only task that can record a Pass**, on the
full metric set. I2 writes the verdict in exactly those words; a Pass claimed
from a partial metric set is a fabricated gate. (If the coordinator wants an
earlier signal, a standalone script *can* measure a synthetic append rate — one
worker, 8 concurrent writers, no engine — which bounds the database side of the
160/s figure without proving the integrated path; it would be labelled
`synthetic append rate`, not `append_rate_per_s`.)

These thresholds are `est.` engineering limits proposed by I1; the coordinator
confirms them — together with the §3.1 round-trip form — against contracts v1
before I2 treats them as a gate.

### 3.4 Who runs it, when, with which secrets

I2 runs the probe **before** any release claim, as its first action under the
deployment lock on the pilot host (§3.2 fixes host and target; a staging
rehearsal first if that environment is allocated); E4 re-runs it — including the
two integrated metrics I2 cannot measure — as part of pilot evidence and
publishes the distribution.

Secret order is fixed, because §5 replaces the instance role with one whose only
`ssm:GetParameter*` grant is scoped to `/model-inference/*`, and the probe must
not be the reason that scoping is skipped: I2 **first** creates the PROPOSED name
`/model-inference/pg_journal_url` (value supplied out of band), **then** runs the
probe, which reads only that one parameter. The probe never reads
`/INFRX-SUPABASE-PROD/*`. That prefix is outside the pilot role's reach **only
once §5 step 5 is carried out**: today's role — and equally any new role that
attaches `AmazonSSMManagedInstanceCore` unmodified — can read every parameter in
the account, because that managed policy grants `ssm:GetParameter`/`GetParameters`
on `Resource: "*"` itself (§5). The probe holds the DSN in process memory only,
takes it from an environment variable name and never a literal, and writes only
aggregate timings — never the URL, password or any row content — into its
evidence report. The probe script lives under `infra/`.

### 3.5 Locality options if it fails

| Option | Change | Cost / risk |
|---|---|---|
| A. PG authority to us-east-1 | new Supabase project (or self-managed PG) in us-east-1; migrate schema, data and auth | **separately authorized / coordinator** — a new paid project plus a migration of the authoritative production database. Console auth and existing balances move with it; DEC-08 forbids re-debiting history, so the migration must copy the ledger verbatim; largest change, best latency |
| B. GPU to us-east-2 | relaunch the pilot host in us-east-2 | **separately authorized / coordinator** — it replaces the serving host. g6e AZ offerings in us-east-2 ⚠️ TO BE VERIFIED (method: `ec2 describe-instance-type-offerings --region us-east-2`; I1 queried us-east-1 only); new EIP and DNS record; weights re-download |
| C. Widen the batch window | 50 ms → 100–200 ms, coalesce by bytes as well as time | needs a coordinator contract revision; raises event lag, keeps one region; cheapest |
| D. Reduce commit count | one commit per N events with a bytes trigger, terminal transaction unchanged | same contract revision as C; does not help `first_progress_ms` |

All four are contingencies, not scheduled operations: none is owned by an
implementation task, and none is performed unless the probe returns Fail and the
coordinator selects it.

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
| Accepted jobs, leases, journal, ledger, holds, feedback | PostgreSQL (us-east-2 — HISTORICAL CLAIM, HANDOFF.md §1; not observable from this host) | none |
| Immutable staged payloads, results, trace content | S3 (PROPOSED `infrx-media`, `infrx-traces`) | none |
| Trace spool segments (post-fsync), usage spill, compile cache, ACME material | `/var/lib/infrx/*` on the root EBS volume | none on stop; **total on termination** while `DeleteOnTermination=true` |
| Weights, transcode scratch, engine working files | `/opt/dlami/nvme/*` (instance store) | total, by design; rebuildable |

## 5. Secret injection

No secret value appears in the repository, a log, an evidence report or a
process argument. The runtime reads SSM parameter **names**; values arrive
through the instance role at install or start time.

| Parameter name | State | Consumer |
|---|---|---|
| `/model-inference/marlin2b_api_key` | OBSERVED (SecureString) | legacy gateway key; must map to an explicit org/key or be disabled at cutover (contracts v1). **Owner: G1** for the mapping-or-disable decision and its enforcement (`handoffs/G-gateway.md`, "Reconcile legacy key mapping for pilot cutover"); **I2** for the installer half — whether `INFRX_MODE=pilot` still writes `GATEWAY_API_KEY` into the env file. Environment: staging then pilot. Matrix row *"Legacy `marlin2b_api_key` cutover — map to an explicit org/key or disable"* in §5 of the I1 evidence report carries the same split |
| `/model-inference/supabase_url` | OBSERVED (String) | gateway auth |
| `/model-inference/supabase_service_role_key` | OBSERVED (SecureString) | gateway auth / usage rows |
| `/model-inference/hf_token` | OBSERVED (SecureString) | weight download |
| `/model-inference/pg_journal_url` | PROPOSED | worker/gateway journal + ledger DSN (I2) |
| `/model-inference/clickhouse_dsn`, `/model-inference/clickhouse_writer_password` | PROPOSED, names owned by T | trace projection |
| `/model-inference/anthropic_api_key` | PROPOSED | judge, J, live budget default zero |

An earlier draft of this table proposed `/model-inference/price_table_version`.
**Withdrawn**, not reassigned: contracts v1 makes the `PriceSnapshot` immutable
and admission resolve it by model and effective time from D1's `price_versions`
table, so a deploy-time version pin in SSM would be a second, conflicting price
authority. The fail-closed check below therefore asserts against PostgreSQL, and
there is no new parameter to own. Every remaining name has one consumer and one
creating task.

**Fail closed in pilot mode.** `install.sh` today treats a missing SSM
parameter as a warning and writes a partial env file, so the gateway can come
up with the legacy key alone — no Supabase auth and no usage rows (OBSERVED in
`apps/infrx-api/deploy/install.sh`). That behaviour is acceptable for a
single-user dev box and unacceptable once promotional credits are enforced. I2
must add an explicit mode:

- `INFRX_MODE=dev` keeps today's permissive behaviour and refuses to bind a
  public interface.
- `INFRX_MODE=pilot` asserts at startup that the auth backend, the journal DSN
  and ledger reachability are present **and that a usable price version for the
  served model resolves from D1's `price_versions`** (contracts v1: a missing
  model or rate rejects admission), and **exits non-zero** otherwise; the
  installer refuses to write an env file that is missing any pilot-required
  name, instead of warning. Owners: I2 for the installer refusal, G1/F for the
  runtime assertion, D1 for the table it reads.
- No default falls back to unauthenticated or unmetered: an absent
  authentication backend is a startup failure in pilot mode, not an open door.
- `/readyz` stays 503 until admission, ledger and journal all answer; the
  systemd unit does not report `active` before that.

The instance role `bootcamp-instance-role` currently grants, on
`Resource: "*"` (OBSERVED 19:14:44Z, `iam get-role-policy --role-name
bootcamp-instance-role --policy-name bootcamp-ops`; the full statement list is in
§2 of the I1 evidence report): `ssm:GetParameter`, `GetParameters`,
`GetParametersByPath`, **`ssm:StartSession`, `ssm:TerminateSession`,
`ssm:DescribeSessions`**, `ec2:Describe*`, `ec2:RunInstances`,
`TerminateInstances`, `Stop/StartInstances`, `Create/DeleteVolume`,
`Attach/DetachVolume`, **`ec2:CreateTags`**, `servicequotas:GetServiceQuota` /
`ListServiceQuotas` / `GetAWSDefaultServiceQuota` /
`ListRequestedServiceQuotaChangeHistoryByQuota`, `ce:GetCostAndUsage`,
`pricing:GetProducts` and `sts:GetCallerIdentity`, plus `iam:PassRole` on itself.

**Two grants, not one — and the managed policy is the one that matters.**
`bootcamp-ops` is the inline policy; the role **also** has the AWS managed policy
`AmazonSSMManagedInstanceCore` attached (OBSERVED 17:44:30Z), whose default
version **v2** carries `ssm:GetParameter` and `ssm:GetParameters` on
`Resource: "*"` in its first statement (OBSERVED 19:32:42Z–19:32:55Z:
`iam get-policy --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore`
→ `DefaultVersionId=v2`, then `iam get-policy-version --version-id v2`; itemised
in §Commands of the I1 evidence report, matrix row *"Pilot-specific instance role
+ profile …"*). So dropping the inline statement is **not** sufficient: attaching
that managed policy unmodified to the new role would re-grant the exact
account-wide parameter read the new role exists to remove. There is no KMS
barrier behind it either — every SecureString under `/INFRX-SUPABASE-PROD/*`,
`/callgideon/*` and `/model-inference/*` is encrypted with `alias/aws/ssm`, the
AWS-managed key, which any principal in the account decrypts through SSM
(OBSERVED 19:33:11Z, `ssm describe-parameters` querying name and `KeyId` for the
SecureString parameters — names and key ids only, **no value was read**).

**The fix is a new role, not an edit of this one.** `bootcamp-instance-role` has
exactly one instance profile, `bootcamp-instance-profile` (OBSERVED 19:14:45Z),
and that profile is attached to **both** `i-0e8449a4ffca29bab` (the pilot host)
and the stopped `i-03723906646f2bb05` (`Project=llm-bootcamp`, not part of this
project) — both OBSERVED 19:14:35Z–19:14:36Z. Stripping statements in place would
silently change another project's permissions, the same class of mistake as the
withdrawn Elastic-IP item. So I2:

1. creates a **pilot-specific role `infrx-pilot-role` and profile
   `infrx-pilot-profile`** carrying only what the pilot needs, then swaps the
   profile onto `i-0e8449a4ffca29bab` alone by calling
   `ec2:ReplaceIamInstanceProfileAssociation` **as its own admin principal** —
   that call is I2's, not a permission inside the new role, which grants no
   `ec2:*` write at all; `bootcamp-instance-role` is left untouched for its owner;
2. scopes `ssm:GetParameter*` in the new role to `/model-inference/*` **only**
   (not a parent of the staging prefix — hence the sibling `/infrx-staging/*` in
   §1), plus the two new buckets and the observability statement;
3. grants **no** `ssm:StartSession`/`TerminateSession`/`DescribeSessions` — a
   `*`-scoped session-start grant on the serving host is remote shell into any
   managed instance in the account;
4. grants **no** instance-lifecycle statements
   (`ec2:Run/Terminate/Stop/StartInstances`, `Create/DeleteVolume`,
   `Attach/DetachVolume`), no `ec2:CreateTags` on `*`, no `iam:PassRole`, and
   none of the cost/quota/pricing statements (they exist for the benchmark work,
   not for serving);
5. keeps `ec2:Describe*`, and **does not attach `AmazonSSMManagedInstanceCore`**.
   Instead it carries a **custom minimal agent policy**: that managed policy's
   `ssmmessages:*` and `ec2messages:*` statements verbatim, plus its first
   statement with **`ssm:GetParameter` and `ssm:GetParameters` removed** — the
   agent itself needs only `UpdateInstanceInformation`, `ListAssociations`,
   `ListInstanceAssociations`, `DescribeAssociation`, `GetDocument`,
   `DescribeDocument`, `GetManifest`, `GetDeployablePatchSnapshotForInstance`,
   `PutInventory`, `PutComplianceItems`, `PutConfigurePackageResult`,
   `UpdateAssociationStatus` and `UpdateInstanceAssociationStatus`, none of which
   reads a parameter. The step-2 statement is then the role's **only**
   `ssm:GetParameter*` grant, which is what makes the `/model-inference/*`
   scoping real. Two alternatives, if something later forces the managed policy
   back on: an explicit `Deny` on `ssm:GetParameter*` with
   `NotResource arn:aws:ssm:us-east-1:641134885443:parameter/model-inference/*`
   (a `Deny` beats any `Allow`), or a customer-managed KMS key on the pilot's own
   parameters — the only option that also survives a future policy edit, at the
   cost of a key to manage. **I2 picks one and records which in its evidence.**

An IAM role is **account-global**, so "staging then pilot" is not a meaningful
environment for it: the role and profile are created once and the *association*
is swapped per host — staging gets its own role scoped to `/infrx-staging/*`.
Matrix row *"Pilot-specific instance role + profile …"* records it that way, and
matrix row *"Set `HttpPutResponseHopLimit` to 1 …"* carries the IMDS change.

Compounding these: the instance has IMDSv2 required
(`HttpTokens=required`, `HttpEndpoint=enabled`) but
**`HttpPutResponseHopLimit=2`** (OBSERVED 19:14:35Z), so a process inside a
bridged docker container — including the unpinned `vllm/vllm-openai:nightly`
image — reaches the instance-role credentials and today inherits account-wide SSM
read. I2 sets the hop limit to 1 in the same change as the profile swap
(`ec2 modify-instance-metadata-options`, a mutation, under the lock).

**Hardening fact I2 must check first, in this order:** hop limit 1 is precisely
what stops a *bridged* container reaching IMDS (the bridge consumes the single
hop), and it does **not** stop a container started with `--network host`. Nothing
on the box is known to need role credentials from inside a container — the
installer reads SSM on the host and writes `/etc/marlin2b-gateway.env`, and the
engine container needs only weights already on disk — but the vLLM and Caddy
containers' network mode was not inspected (remote execution was forbidden to
I1). So I2 verifies, under the lock, that no container fetches credentials from
IMDS *before* flipping the hop limit; if one does, the credential path moves to
an injected env file or the host network first. Flipping it blind can break the
engine start, and a broken engine on the serving host is worse than the exposure
it closes for the minutes it takes to notice.

**SSH: host-scoped for the same reason as the role.** The only tcp/22 ingress in
the two pilot security groups lives in `sg-0145dcf39dfe8194e` (`bootcamp-sg`);
`sg-050d7b384ad79856d` (`marlin2b-gateway`) carries only tcp/80 and tcp/443
(OBSERVED 19:32:45Z, `ec2 describe-security-groups --group-ids` both). And
`bootcamp-sg` is attached to **both** the pilot host's ENI
`eni-0eedf581ac04cd880` and the stopped llm-bootcamp box's
`eni-08a7db926a0d98572` (OBSERVED 19:32:57Z,
`ec2 describe-network-interfaces --filters Name=group-id,Values=sg-0145dcf39dfe8194e`).
Revoking the rule in place would close SSH on another project's instance — the
same defect as editing `bootcamp-instance-role`. So I2 changes the **pilot ENI's
group set** instead (`ec2 modify-network-interface-attribute --groups`, a
mutation, under the lock): either `marlin2b-gateway` alone, since the SSM agent is
`Online` and SSH is not required, or `marlin2b-gateway` plus a new
`infrx-pilot-sg` with tcp/22 from an admin CIDR if key access is kept.
`bootcamp-sg` itself is not modified. Matrix row *"Restrict tcp/22 from
`0.0.0.0/0` …"* records it host-scoped. **Order:** this is the last of the three
host changes, because removing SSH before the profile swap and the hop-limit flip
removes the fallback if SSM access breaks; I2 confirms `ssm
describe-instance-information` still reports `Online` immediately beforehand.

## 6. Backup and restore per durable layer

No EC2 snapshot, AMI or AWS Backup plan exists in the account (OBSERVED). All
values below are `est.` placeholders until I3 measures them; I3 owns the drills
and replaces them with `meas.`.

| Layer | Backup mechanism | RPO | RTO | Restore test owner |
|---|---|---|---|---|
| PostgreSQL (jobs, leases, journal, ledger, holds, feedback) | Supabase-managed backups. Plan and retention **not observable from this host** (Supabase Management API is outside I1's allowed verbs). PITR is proposed, and **enabling it is a paid plan change on the production database: separately authorized, coordinator-owned**, not an I2/I3 action | ⚠️ TO BE VERIFIED (method: read the project's plan and backup settings in I2/I3. If the plan includes daily backups the RPO is `est.` ≤ 24 h; **if the plan has no scheduled backups the current RPO is unbounded**. Target ≤ 5 min, reachable only with PITR enabled) | `est.` ⚠️ TO BE VERIFIED (method: timed restore of a scratch project by I3) | I3 measures; PITR enablement separately authorized |
| Result / media objects (PROPOSED `infrx-media`) | S3 durability + versioning enabled at creation + lifecycle per retention policy (results 24 h, processing cache 7 d) | 0 | minutes `est.` | I3 |
| Trace content (PROPOSED `infrx-traces`) | S3 versioning + per-object retention tags (≤ 90 d content, 13 mo metadata) | 0 | minutes `est.` | I3 with T |
| Trace spool on EBS | not backed up by design; durability begins after fsync, host/volume loss is out of scope per durable protocol | `est.` ≤ 2 s of unsynced events | shipper drain time, `est.` ⚠️ TO BE VERIFIED | I3 with T |
| ClickHouse projections | rebuildable from S3 content + PG truth; plus T's own backup to S3 | `est.` 24 h | `est.` ⚠️ TO BE VERIFIED | T, drilled by I3 |
| Pilot host root volume | EBS snapshot before every deploy; `DeleteOnTermination=true` must be flipped or the snapshot is the only copy | one deploy cycle | `est.` 10–20 min from snapshot | I2 creates, I3 restores |
| Weights | reproducible from the **gated Hugging Face repo only**: per HANDOFF.md and CLAUDE.md (HISTORICAL CLAIM, not re-observed — I1 listed top-level prefixes only) the `weights/` mirror in `llm-bootcamp-641134885443` holds `deepseek-v41` and **no Marlin copy**, so a cold start depends on Hugging Face availability and a valid `HF_TOKEN`. Mirroring Marlin to the bucket would remove that dependency, but it is **not proposed and not assigned here**: it writes into `llm-bootcamp-641134885443`, which is another project's bucket, so it needs that owner's agreement and a matrix row of its own before anyone runs it. Out of pilot scope while HF plus a valid `HF_TOKEN` is deemed sufficient | n/a | download time, `est.` ⚠️ TO BE VERIFIED (no measured cold start exists) | I2 |

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

**Pre-existing resources of these kinds are not this project's.** Each fact below
was re-observed in the third pass and is itemised with its command and UTC time in
§Commands of the I1 evidence report:

- `autoscaling describe-auto-scaling-groups` (OBSERVED 19:14:59Z): **9** Auto
  Scaling groups, every one `gideon-{chat-agent,platform-api,voice-agent}-{dev,exp,prod}-asg`,
  all at desired capacity 0.
- `ec2 describe-launch-templates` (OBSERVED 19:14:56Z–19:14:57Z): **10** launch
  templates — the nine matching `gideon-*` plus `b300-deepseek-bench`, which
  belongs to the llm-bootcamp/DeepSeek benchmark work, not to the infrx pilot. Its
  three versions carry **no `IamInstanceProfile`**
  (`ec2 describe-launch-template-versions`, OBSERVED 19:15:00Z), which is why the
  §5 profile swap has a blast radius of the two g6e instances only.
- `ec2 describe-capacity-reservations` (OBSERVED 19:14:54Z, tags and creation date
  19:15:10Z): exactly one reservation, `cr-04397f3102a3955b7`,
  **ReservationType=capacity-block**, `p6-b300.48xlarge`, us-east-1b, state
  `scheduled`, **start 2026-09-21T11:30:00Z, end 2026-09-22T11:30:00Z**, tags
  `Name=b300-bootcamp`, `Purpose=llm-bootcamp`, created 2026-09-18T18:06:31Z. No
  `ReservationType=default` on-demand capacity reservation exists.

None of these is created, used, extended, modified or cancelled by any task in
this document. The Capacity Block is surfaced to the coordinator as handback item
12 of the I1 evidence report, because it is a prepaid commitment in the same
project family that starts within a day; acting on it is separately authorized and
nothing here proposes anything about it.

## Verification log

- 2026-09-20: Authored by I1 from read-only AWS observation, the live public
  endpoints and the repository's deploy files. No resource was created and no
  configuration changed. Every threshold here is `est.` until I2/I3/E4 measure
  it; the cross-region journal probe (§3) and the fail-closed pilot mode (§5)
  are the two items that gate an I2 release claim.
- 2026-09-20 (review fixes, still read-only; no new resource, no configuration
  change): §1 pilot row relabelled — the Supabase project ref and **us-east-2
  region are a HISTORICAL CLAIM from HANDOFF.md §1**, not OBSERVED; only the
  `/INFRX-SUPABASE-PROD/*` parameter *names* were seen, and a name cannot carry
  a project ref or region. The §3 probe and the launch risk rest on that claim,
  so it is now labelled as one. §1 also records that allocating `staging` is a
  coordinator action and enabling Supabase PITR is separately authorized;
  the staging SSM prefix is `/model-inference/staging/*`, consistent with the
  §5 role narrowing — **superseded by the third and fourth entries below: the
  staging prefix is the sibling `/infrx-staging/*`, because a pilot role narrowed
  to `/model-inference/*` would otherwise read staging secrets.** §2 gains the `docker stop -t 120` drain-grace rule. §3.1
  fixes the measured transaction at one client round trip (three-round-trip form
  reported separately). §3.3 is now an ordered, exhaustive Fail→Pass→Marginal
  ladder with the append-rate thresholds derived from the 50 ms window
  (< 120/s Fail, ≥ 160/s Pass). §3.4 fixes the secret order: I2 creates
  `/model-inference/pg_journal_url` first and the probe reads only that,
  never `/INFRX-SUPABASE-PROD/*`. §6 marks the PostgreSQL RPO
  ⚠️ TO BE VERIFIED with a method, and records that the RPO is unbounded if the
  project's plan has no scheduled backups.
- 2026-09-20 (second review pass, still read-only — seven further `describe-*` /
  `get-role-policy` / `list-users` calls, no resource created, modified or
  deleted, no secret value read, no remote command): §9 now records the
  **OBSERVED** launch-template, ASG and capacity-reservation state instead of
  leaving those kinds unexamined — including the pre-existing `b300-deepseek-bench`
  launch template and the scheduled p6-b300 **Capacity Block**
  (`cr-04397f3102a3955b7`, 2026-09-21T11:30Z → 2026-09-22T11:30Z), both out of
  pilot scope and untouched. §5 withdraws the proposed
  `/model-inference/price_table_version` (contracts v1 resolves `PriceSnapshot`
  from D1's `price_versions` by effective time; an SSM pin would be a second
  price authority) and instead assigns the fail-closed price check to
  I2/G1/D1; the legacy `marlin2b_api_key` cutover is split explicitly between
  **G1** (mapping or disable) and **I2** (installer), with an environment. §5
  also names the three `ssm:*Session` grants and `ec2:CreateTags` that the
  earlier narrowing instruction would have left on `Resource: "*"`, and the
  IMDS `HttpPutResponseHopLimit=2` that lets containers reach the role.
  §1 moves the staging secret prefix to the sibling `/infrx-staging/*` so a role
  narrowed to `/model-inference/*` cannot read staging secrets. §3.2/§3.4 fix the
  probe's host (the pilot GPU instance) and target (the authoritative pilot PG,
  D1's scratch schema executed by I2 under the lock) and state which two metrics
  need the integrated path and so are E4's, not I2's; §3.3 gains a
  `terminal_txn_ms` threshold with its derivation. §3.5 options A and B are
  marked separately authorized. §2 assigns the new units to I2 with an
  environment; §6 corrects the weights row (no Marlin copy in the S3 mirror) and
  §4 labels the us-east-2 PostgreSQL location a HISTORICAL CLAIM.
- 2026-09-20 (third review pass — consistency with the I1 evidence report; still
  read-only, no resource created, modified or deleted, no secret value read, no
  remote command, no HTTP request):
  - **Every fact the second pass cited without a command is re-observed and
    itemised.** That pass recorded "seven further `describe-*` calls" in this log
    but listed none of them, so §5 and §9 carried OBSERVED times
    (18:27Z, 18:28:03Z, 18:29:06Z) that no `§Commands` row backed. All of those
    facts were re-run at 19:14:34Z–19:15:10Z, each with its command and exit code
    now in §Commands of the evidence report, and the timestamps here were updated
    to the re-observation. Every one confirmed unchanged.
  - **§5 no longer edits another project's IAM role in place.**
    `bootcamp-instance-profile` is attached to the stopped `i-03723906646f2bb05`
    as well as to the pilot host (OBSERVED 19:14:35Z–19:14:45Z), so "narrow /
    remove / drop statements on `bootcamp-instance-role`" would have changed the
    llm-bootcamp box's permissions. I2 now creates `infrx-pilot-role` +
    `infrx-pilot-profile` and swaps the association on the pilot host only; the
    role is account-global, so the matrix row records "account-global; association
    swapped per host" rather than "staging then pilot".
  - §5's over-grant listing was incomplete: `servicequotas:*`,
    `pricing:GetProducts` and `sts:GetCallerIdentity` are also on `Resource: "*"`
    and are now named, and the new role grants none of them.
  - §5 adds the **hop-limit hardening order** for I2: hop limit 1 blocks a bridged
    container but not `--network host`, and no container's network mode was
    inspected by I1, so I2 confirms no container reads IMDS credentials *before*
    flipping it.
  - §3.1 adds the **transaction-mode pooler port `6543`** beside HANDOFF's session-mode
    `:5432`; both are probed and reported, and the mode the worker will use is the
    one the ladder is evaluated against.
  - §3.3 states explicitly that **I2 cannot reach a Pass verdict** — both Pass and
    Fail rows contain the two metrics only the integrated path can produce — so
    I2 records "not Fail on the measurable clauses, two metrics not run" and E4 is
    the only task that can record a Pass. A standalone synthetic append rate is
    offered as an optional earlier signal, labelled as synthetic.
  - §9 restates the ASG / launch-template / Capacity Block observations as an
    itemised list with commands and times, and points at the handback item that
    actually carries the Capacity Block (item 12).
  - Cross-references made resolvable: the §2 and §5 pointers now name the exact
    evidence matrix rows (*"New systemd units (worker, preparation, trace
    shipper)"*, *"Legacy `marlin2b_api_key` cutover …"*), which the same pass adds
    to the evidence report, and the header links the report by its real filename.
- 2026-09-20 (fourth review pass — the secret isolation of §5 did not actually
  isolate; still read-only: five `iam get-policy` / `get-policy-version` /
  `describe-security-groups` / `describe-network-interfaces` /
  `describe-parameters` calls at 19:32:42Z–19:33:30Z, **no** resource created,
  modified or deleted, **no** secret value read, **no** remote command, **no**
  HTTP request):
  - **§5 no longer attaches `AmazonSSMManagedInstanceCore` to the new role.** Its
    default version v2 grants `ssm:GetParameter` and `ssm:GetParameters` on
    `Resource: "*"` (OBSERVED 19:32:42Z–19:32:55Z), so the previous step 5
    ("keeps … `AmazonSSMManagedInstanceCore`") re-granted the account-wide
    parameter read that step 2's `/model-inference/*` scoping exists to remove —
    the narrowing would have taken no effect, and every SecureString under
    `/INFRX-SUPABASE-PROD/*` and `/callgideon/prod/*` would still have been
    readable from the pilot host. There is no KMS barrier: all of them use the
    AWS-managed `alias/aws/ssm` (OBSERVED 19:33:11Z, names and key ids only).
    Step 5 is now a custom minimal agent policy with those two actions removed,
    with the explicit-`Deny` and customer-managed-key alternatives named; the
    managed policy's grant is recorded as OBSERVED with its command.
  - **§3.4's claim that `/INFRX-SUPABASE-PROD/*` "stays outside the pilot role's
    reach" was false** as the design stood and now says under which condition it
    becomes true.
  - **§5 adds the tcp/22 hardening, host-scoped.** The only port-22 rule is in
    `bootcamp-sg`, which is attached to the stopped llm-bootcamp box's ENI as well
    as the pilot host's (OBSERVED 19:32:45Z, 19:32:57Z), so revoking it in place
    would have repeated the `bootcamp-instance-role` mistake on a security group.
    I2 changes the pilot ENI's group set instead, last of the three host changes.
  - §5 step 1 no longer reads as if the new role carried
    `ec2:ReplaceIamInstanceProfileAssociation`; §6's weights row marks the S3
    mirror of Marlin as **not proposed and not assigned** (it would write into
    another project's bucket).
