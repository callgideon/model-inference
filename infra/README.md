# Pilot deploy design — free single-GPU pilot

Living design for **I2** to implement. I1 created nothing: every row marked
PROPOSED does not exist.

**Where the facts live.** The I1 inventory,
[`research/plan/evidence/i/I1-4e052f4.md`](../research/plan/evidence/i/I1-4e052f4.md)
(one report, updated in place), owns **every observed fact and the single table of
commands that produced them**. This document is the design: it cites that report's
stable row ids — `O-…` for a row of observed state, `M-…` for a row of the
required-vs-existing matrix — and does **not** restate what a command returned in
its own words, because that duplication is what made these two documents drift
through four review rounds. If a number, port, action list or count appears here
without an id beside it, it is a proposal, an `est.` budget or a rule, not an
observation. Identifiers of things a step operates on (an instance, an ENI, a role,
a parameter name) are named here with their row id. Conventions per `CLAUDE.md`: `est.` stays
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
| `pilot` | The single-GPU free pilot serving real keys | `i-0e8449a4ffca29bab` — row `O-INSTANCE` | Supabase `fcbnscgsymzdykendbrc`, **us-east-2 — HISTORICAL CLAIM (HANDOFF.md §1)**; only the `/INFRX-SUPABASE-PROD/*` parameter *names* are OBSERVED, and a parameter name cannot reveal a project ref or region | PROPOSED `infrx-media`, `infrx-traces` | existing `/model-inference/*` + PROPOSED names in §5 | I2/I3 holding the lock, coordinator-authorized |

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

Every unit marked PROPOSED below is created by **I2**, in `staging` then `pilot`
(matrix row `M-UNITS`); the owner track named in the table owns what runs *inside*
the unit, not the unit file.

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
Drain grace: the vLLM unit's `ExecStop` carries no `-t` today, so in-flight
generation is killed after docker's 10 s default (evidence §1, "systemd units").
I2 sets `docker stop -t 120` with a matching `TimeoutStopSec` on every unit that
can hold an accepted job, so a deploy drains instead of truncating; matrix row
`M-DRAIN` carries the owner and environment.

Root volume budget on the current box: the 300 GiB gp3 volume of row `O-ROOTVOL`.
Allocation `est.`: OS + DLAMI ~120 GiB, trace spool 10 GiB cap, media/staging
60 GiB, compile cache 10 GiB, journal spill and logs 5 GiB — fits with headroom.
That row's `DeleteOnTermination=true` and the zero snapshots/AMIs/backup plans of
row `O-BACKUPS` are the two facts that make §6 mandatory before I2 deploys.

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

**Scratch schema ownership** (matrix row `M-SCRATCH`). D1 authors the
scratch-schema SQL and its drop (D owns all SQL); **I2 executes both under the
deployment lock**, because D1 holds no lock — so the one mutation this probe makes
to the authoritative database has exactly one lock holder. The schema is created immediately before the run and dropped
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

**I2 cannot reach a Pass verdict, and that is expected** (matrix row `M-PROBE`). Both the Pass and Fail
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

These thresholds are `est.` engineering limits proposed by I1, and per the
coordinator's ruling they **stay `est.` and become a release gate only once E4 has
measured them**. I2 runs the probe, records the distribution and the verdict wording
above, and **fails nothing on these numbers** — not the deploy, not a release claim.
Any earlier use of them as a gate is out of order.

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
through the instance role at install or start time. Which names exist today, with
their types, key ids and timestamps, is evidence row `O-PARAMS`; the `State`
column below says only whether this design needs a name created, and by whom.

| Parameter name | State | Consumer |
|---|---|---|
| `/model-inference/marlin2b_api_key` | exists — row `O-PARAMS` | legacy gateway key; must map to an explicit org/key or be disabled at cutover (contracts v1). **Owner: G1** for the mapping-or-disable decision and its enforcement (`handoffs/G-gateway.md`, "Reconcile legacy key mapping for pilot cutover"); **I2** for the installer half — whether `INFRX_MODE=pilot` still writes `GATEWAY_API_KEY` into the env file. Environment: staging then pilot. Matrix row `M-KEYCUT` carries the same split |
| `/model-inference/supabase_url` | exists — row `O-PARAMS` | gateway auth |
| `/model-inference/supabase_service_role_key` | exists — row `O-PARAMS` | gateway auth / usage rows |
| `/model-inference/hf_token` | exists — row `O-PARAMS` | weight download |
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

**Fail closed in pilot mode** (matrix row `M-FAILCLOSED`, and a **prerequisite of
the role, boundary and key work** — see the ordering hazard above). `install.sh`
today treats *any* SSM failure as a warning, truncates the env file and restarts the
gateway, and the gateway allows every request when neither auth mechanism is
configured, so the stack can come up **fully open** rather than merely
degraded — row `O-FAILOPEN` has the file and line numbers. I2's installer must
abort instead, leaving the previous env file and the running service untouched, and
must **distinguish `AccessDenied` and throttling from `ParameterNotFound`**: the
first two mean "do not deploy", the third is the only case the present warning was
written for. That behaviour is acceptable for a
single-user dev box and unacceptable once promotional credits are enforced. I2
must add an explicit mode:

The mode flag is one operation, matrix row `M-FAILCLOSED`, split as that row says:

- **unset or unrecognised `INFRX_MODE` refuses to start** (matrix row `M-FAILCLOSED`) — the installer writes no
  env file and the units exit non-zero. A default is how an unmetered pilot happens
  by accident, so there is no default.
- `INFRX_MODE=dev` keeps today's permissive behaviour, refuses to bind a public
  interface, and **refuses to install or start the public Caddy site** (no `:80`/
  `:443` listener, no ACME account, no certificate for the public name): a dev host
  that answers on the pilot's DNS name is the same failure as an unmetered pilot.
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

**Why the instance role has to be replaced — the facts, by row id.** The role
`bootcamp-instance-role` that the pilot host uses today grants, on
`Resource: "*"`, an account-wide SSM parameter read, remote-shell
(`ssm:StartSession`) on any managed instance, instance and volume lifecycle,
`ec2:CreateTags`, the cost/quota/pricing verbs and `iam:PassRole` on itself
(evidence row `O-OPS`, which is the verbatim statement list — this document does
not repeat it). **The parameter read arrives twice**: the second source is the
attached AWS managed policy `AmazonSSMManagedInstanceCore`, whose default version
grants `ssm:GetParameter` and `ssm:GetParameters` on `Resource: "*"` in its first
statement (row `O-MANAGED`, which quotes all three statements verbatim). So
dropping the inline statement is **not** sufficient: attaching that managed policy
unmodified to the new role would re-grant exactly the account-wide read the new
role exists to remove. And **no KMS barrier stands behind it** — every SecureString
**in us-east-1**, this project's and the others', is encrypted with the AWS-managed
`alias/aws/ssm` key, which any principal in the account decrypts through SSM (row
`O-PARAMS`). Parameter-read permission therefore *is* decryption permission, which
is why §5 step 5 and matrix row `M-KMS` exist.

**Ordering hazard — fail closed before touching any permission.** Row `O-FAILOPEN`
records what the checkout does today: the installer swallows every
`get-parameter` failure, truncates the env file and restarts the gateway anyway,
and the gateway **allows every request** when neither the legacy key nor the
Supabase URL is configured. So **any** denied or failed parameter read during an
install run publishes an unauthenticated, unmetered gateway on the pilot hostname —
and the first install run after a permission change is exactly when a read is most
likely to be denied. That makes the fail-closed work of matrix row `M-FAILCLOSED` a
**hard prerequisite**, not a parallel task. **This exposure exists today**
independent of anything I2 does: an SSM outage or a throttle during an install run
has the same effect. Order, and it is not negotiable:

**0.** `M-FAILCLOSED` first — the installer aborts without touching the env file and
without restarting when a required parameter cannot be read, and it distinguishes
`AccessDenied`/throttling from `ParameterNotFound` (the first two are failures, the
third is the case the current warning was written for); the runtime refuses to start
in pilot mode without auth and metering configured. Owners: **I2** for the installer,
**G1/F** for the runtime refusal. Then, in this order: **1.** the role and boundary
in `staging`, **2.** verify by simulation and by an install run that a denied read
aborts, **3.** the pilot profile swap, **4.** the hop limit, **5.** the SSH group
change. Steps 3–5 stay in that order for the reason §5 already gives: SSM access
must not be the only path while the others are in progress.

**The fix is a new role, not an edit of this one**, because the role's only
instance profile is attached to the stopped llm-bootcamp box as well as to the
pilot host (row `O-SHAREDPROFILE`): stripping statements in place would silently
change another project's permissions, the same class of mistake as the withdrawn
Elastic-IP item. So I2:

1. (matrix row `M-ROLE`) creates a **pilot-specific role `infrx-pilot-role` and
   profile `infrx-pilot-profile`** carrying only what the pilot needs, then swaps the
   profile onto `i-0e8449a4ffca29bab` alone by calling
   `ec2:ReplaceIamInstanceProfileAssociation` **as its own admin principal** —
   that call is I2's, not a permission inside the new role, which grants no
   `ec2:*` write at all; `bootcamp-instance-role` is left untouched for its owner;
2. scopes `ssm:GetParameter*` in the new role to `/model-inference/*` **only**
   (not a parent of the staging prefix — hence the sibling `/infrx-staging/*` in
   §1), plus the two new buckets and the observability statement;
3. grants **no** `ssm:StartSession`/`TerminateSession`/`DescribeSessions` — a
   `*`-scoped session-start grant on the serving host is remote shell into any
   managed instance in this account and region;
4. grants **no** instance-lifecycle statements
   (`ec2:Run/Terminate/Stop/StartInstances`, `Create/DeleteVolume`,
   `Attach/DetachVolume`), no `ec2:CreateTags` on `*`, no `iam:PassRole`, and
   none of the cost/quota/pricing statements (they exist for the benchmark work,
   not for serving);
5. keeps `ec2:Describe*`, and **does not attach `AmazonSSMManagedInstanceCore`**.
   Instead it creates a **customer-managed agent policy** by copying row
   `O-MANAGED`'s action lists and removing exactly two actions. That row is the
   verbatim policy document. The property to preserve is **no parameter-read action
   on a `Resource` outside `/model-inference/*`** — not "no wildcards", since the role
   deliberately keeps `ec2:Describe*`: the agent's
   own statement enumerates fifteen `ssm:` actions, of which the new policy keeps
   thirteen and drops `ssm:GetParameter` and `ssm:GetParameters`; the channel
   statements enumerate **four** `ssmmessages:` actions and **six**
   `ec2messages:` actions, which are copied action by action. Copy the names from
   `O-MANAGED`, not from this paragraph, and **write no `ssmmessages:*` or
   `ec2messages:*` wildcard** (those two statements are enumerated in the managed
   policy, so a glob would widen them) — an earlier revision of this step said those two
   statements were wildcards and told I2 to carry them "verbatim", which would have
   shipped a *broader* agent policy than the managed one it replaces (§Verification
   log, fifth pass). The step-2 statement is then the role's **only**
   `ssm:GetParameter*` grant, which is what makes the `/model-inference/*` scoping
   real.

   **Read-only check that the scoping took effect**, run by I2 after the swap and
   recorded in its evidence:
   `iam list-attached-role-policies --role-name infrx-pilot-role` lists **no**
   `AmazonSSMManagedInstanceCore`; `iam list-role-policies` +
   `iam get-role-policy` (and `get-policy-version` for any customer-managed policy
   attached) show every `ssm:GetParameter*` resource under
   `arn:aws:ssm:us-east-1:641134885443:parameter/model-inference/*`, no `*` action
   and no `ssm:*Session`; `ec2 describe-instances --instance-ids
   i-0e8449a4ffca29bab` shows `IamInstanceProfile` = `infrx-pilot-profile`; and
   `ssm describe-instance-information` still reports the host `Online`.

6. **puts the durable outbound control in a permissions boundary** (matrix row
   `M-BOUNDARY`, **required**, I2, `staging` then `pilot`, **after step 0 below**).
   Step 5 narrows an identity policy and that is all it does: re-attaching
   `AmazonSSMManagedInstanceCore`, adding a broad convenience policy or attaching a
   second policy **to this role** restores the account-wide parameter read. So
   `infrx-pilot-role` is created **with a permissions boundary**, and the boundary
   has **two** statements, because a boundary is not a deny-list:

   - an **`Allow`** broad enough to cover everything the role legitimately does —
     either `Action: "*"` on `Resource: "*"`, or an explicit allow-list containing
     the agent actions of step 5, the S3 actions for the two buckets, the
     logs/metrics actions, `ec2:Describe*`, the scoped parameter reads and
     **`kms:Decrypt`**;
   - an explicit **`Deny`** on `ssm:GetParameter`, `ssm:GetParameters`,
     `ssm:GetParametersByPath`, `ssm:GetParameterHistory` with
     `NotResource: arn:aws:ssm:us-east-1:641134885443:parameter/model-inference/*`.

   **Why both:** an effective permission is the **intersection** of the boundary and
   the identity policies, so a boundary containing only a `Deny` permits *nothing*
   and the host would do nothing at all. For the same reason **`kms:Decrypt` must be
   in the `Allow`**: a key-policy grant to the role is itself limited by the
   boundary, so omitting it would silently break step 7 and every SecureString read.

   **Read-only check:** `iam get-role --role-name infrx-pilot-role` shows
   `PermissionsBoundary`; `iam get-policy-version` on the boundary policy shows both
   statements; `iam simulate-principal-policy` (a read verb, in I2's allowlist, not
   I1's) returns a **deny** for `ssm:GetParameter` on
   `…:parameter/INFRX-SUPABASE-PROD/db_password` and an **allow** on
   `…:parameter/model-inference/pg_journal_url`. I2 records the simulation result —
   that is what turns "isolated" from a claim into an observation.

   **What the boundary does not cover, stated so nobody over-trusts it.** It binds
   *this role*: it stops any policy later attached **to `infrx-pilot-role`** from
   restoring the read, and nothing else. It does **not** stop the instance profile
   being swapped back to `bootcamp-instance-profile`, which carries no boundary (row
   `O-PROFILE`), or to any other role's profile; it does **not** stop static
   credentials being placed on the host; and it does **not** stop an administrator
   removing it (`iam:DeleteRolePermissionsBoundary`,
   `iam:PutRolePermissionsBoundary`). Those are detection questions, and this design
   proposes no control for them.

7. **adds the inbound control on the pilot's own secrets** (matrix row `M-KMS`,
   recommended, I2, `staging` then `pilot`). Steps 5 and 6 stop the pilot host
   reading *other* projects' parameters. They do nothing about the opposite
   direction: **other principals reading ours.** Three roles in this account hold
   `ssm:GetParameter`/`GetParameters` on `Resource: "*"` today (row `O-MPATTACH` —
   the pilot's, an SSM role of the account owner's and CallGideon's ECS instance
   role), and since every SecureString in us-east-1 is on the AWS-managed
   `alias/aws/ssm` key (row `O-PARAMS`), each of them decrypts ours for free. The
   parameter that matters most is the PROPOSED journal DSN: a credential for the
   authoritative database. So the pilot's own parameters — the existing names under
   **`/model-inference/*`**, re-created **at the same names**, because every consumer
   already reads there (the §5 table, the §3.4 probe, `M-DSN`, step 5's scope,
   `install.sh` lines 17–19 and the on-the-box `hf_token` flow) — become SecureString
   under a **customer-managed key** `alias/infrx-pilot`, whose key policy is written
   out rather than defaulted:
   - **no account-root `kms:*` delegation statement**, so no IAM policy anywhere can
     grant use of this key and the key policy is the whole story;
   - `kms:Decrypt` + `kms:DescribeKey` for `infrx-pilot-role`. That is also what the
     documented `hf_token` flow needs, because `models/marlin2b/README.md` line 27
     runs **on the box as the instance role** ("the instance role can read it") — not
     as an admin principal, as an earlier revision of this section said. The
     client-side flow at line 51 of that file runs as whichever **operator** principal
     the reader uses, so that principal needs the same two actions or the documented
     command stops working;
   - `kms:Encrypt`, `kms:GenerateDataKey*`, `kms:ReEncrypt*` for the admin principal
     that runs `ssm put-parameter --key-id` when a value is set or rotated; the
     runtime gets none of these, because it never writes a parameter;
   - `kms:*` for a **named** key administrator: with no root delegation, an unnamed
     administrator cannot rotate, tag or schedule deletion of the key, and a key
     nobody can administer is a future outage.

   If I2 keeps the root delegation statement instead, the property that remains is
   only that decryption needs **both** an IAM allow and a key-policy allow — a
   principal with `ssm:GetParameter*` on `*` but no key-policy grant is still
   refused — and I2 records that it took the weaker form and why. `staging` keeps its own role, boundary and key on
   the sibling prefix `/infrx-staging/*` (§1); nothing nests under the pilot prefix. **Read-only check:** `ssm describe-parameters` shows
   `KeyId=alias/infrx-pilot` on the `/model-inference/*` parameters and
   `alias/aws/ssm` unchanged on every other prefix; `kms get-key-policy` and `kms list-grants` show exactly the principals
   above. Nothing here touches `rey-aws-ssm-role` or `gideon-ecsInstanceRole`: they
   belong to other work, and this design proposes nothing about them.

**Not a choice between the two.** The boundary of step 6 is required: it is what
keeps step 5's narrowing true against a later policy attached to this role. The key
of step 7 is the additional inbound control, recommended before the journal DSN
exists. Earlier revisions of this section got both wrong in turn — first offering
them as alternatives and calling the key the thing that makes the isolation survive
(a key on our own parameters is not on anyone else's decryption path), then
specifying the boundary as a `Deny` alone (which would have permitted nothing at
all). The §Verification log records both corrections.

An IAM role is **account-global**, so "staging then pilot" is not a meaningful
environment for it: the role and profile are created once and the *association*
is swapped per host — staging gets its own role scoped to `/infrx-staging/*`.
Matrix row `M-ROLE` records it that way, `M-KMS` carries the key, and `M-IMDS`
carries the IMDS change.

Compounding these (matrix row `M-IMDS`): the host enforces IMDSv2 but runs at
**hop limit 2** (row `O-IMDS`), so a process inside a bridged docker container — including the unpinned
`vllm/vllm-openai:nightly` image — reaches the instance-role credentials and today
inherits the account-wide SSM read. I2 sets the hop limit to 1 in the same change
as the profile swap (`ec2 modify-instance-metadata-options`, a mutation, under the
lock).

**What hop limit 1 does and does not close** (still matrix row `M-IMDS`). It stops
a *bridged* container reaching IMDS, because the bridge consumes the single hop,
and it does **not** stop a container started with `--network host`. That is not
hypothetical here: per row `O-NETMODE` the repository starts **Caddy with
`--network host`** and the **engine bridged**. So after the flip the engine
container is cut off from the instance role and **the Caddy container still is
not** — a residual exposure that the flip does not address, and the one an attacker
with code execution in the TLS front door would use. I2's mitigation, in the same
change or recorded as deferred with the residual risk: run Caddy bridged with
published ports instead of host network, or, if host network stays, accept that the
boundary `Deny` of step 6 — not the hop limit — is what bounds what those
credentials can read. Nothing on the box is known to *need* role credentials from
inside a container (the installer reads SSM on the host and writes
`/etc/marlin2b-gateway.env`; the engine needs only weights already on disk). The
network modes above are the **checkout's** (`O-NETMODE`); what the **running** box
does was not inspected (Limits item 4), so I2 verifies under the lock that no
container fetches credentials from IMDS *before* flipping the hop limit. Flipping it blind can break the engine start, and a broken engine on
the serving host is worse than the exposure it closes for the minutes it takes to
notice.

**SSH: host-scoped for the same reason as the role.** Per row `O-SG`, the only
tcp/22 rule on either of the pilot's groups is in `bootcamp-sg`, which is attached
to the stopped llm-bootcamp box's ENI as well as the pilot host's; `marlin2b-gateway`
carries only the two web ports and is attached to the pilot ENI alone. Revoking the
rule in place would close SSH on another project's instance — the same defect as
editing `bootcamp-instance-role`. So I2 changes the **pilot ENI's group set**
instead (`ec2 modify-network-interface-attribute --groups`, a mutation, under the
lock): either `marlin2b-gateway` alone, since the SSM agent is `Online` (row
`O-SSMAGENT`) and SSH is not required, or `marlin2b-gateway` plus a new
`infrx-pilot-sg` with tcp/22 from an admin CIDR if key access is kept.
`bootcamp-sg` itself is not modified, and no rule of it is changed. **This is an
ingress change only:** both groups allow all egress (`O-SG`), so it restricts
nothing outbound, and an outbound control would be a separate proposal with its own
matrix row. Matrix row `M-SSH` records it host-scoped. **Order:** this is the last of the three
host changes, because removing SSH before the profile swap and the hop-limit flip
removes the fallback if SSM access breaks; I2 confirms `ssm
describe-instance-information` still reports `Online` immediately beforehand.

## 6. Backup and restore per durable layer

No EC2 snapshot, AMI or AWS Backup plan exists in us-east-1 (row `O-BACKUPS`);
creating the first one is matrix row `M-SNAPSHOT`, and the two buckets are
`M-MEDIA` and `M-TRACES`. All values below are `est.` placeholders until I3
measures them; I3 owns the drills
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

1. **Snapshot** the root volume and record the snapshot id in the lock record
   (matrix row `M-SNAPSHOT`).
2. **Pause admission** (maintenance mode accepting no new work, existing jobs
   draining).
3. **D's PostgreSQL migrations** (matrix row `M-PGSCHEMA`) — numbered files, one per change, applied
   through the pooler URL. D owns the files and the sequence numbers; the
   deploy calls D's entry point and fails the deploy on a non-zero exit.
   Additive expand/contract only, forward-compatible with the currently
   deployed runtime.
4. **T's ClickHouse DDL** (matrix row `M-CHDDL`) — same contract, T's entry point, after PG so a trace
   projection never references a column that does not exist yet.
5. **Restart** engine, worker, preparation, spool shipper, gateway in that
   order; each waits for the previous readiness signal.
6. **Resume admission** only after `/readyz` reports every component healthy.

A migration that is not forward-compatible with the running runtime is a
two-deploy change, never one.

## 8. Rollback rule

From [03-execution-protocol.md](../research/plan/03-execution-protocol.md). The
runbook that turns these rules into a drilled procedure is matrix row
`M-RUNBOOKS` (I3); nothing below is an operation I2 performs outside the lock:

1. Pause admission; drain and fence in-flight work; reconcile durable jobs
   (matrix row `M-RUNBOOKS` owns writing and drilling this as a procedure).
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
the pilot — row `O-TLS` records the live issuer as of 17:47Z); Auto Scaling group and launch
template; baked AMI; on-demand capacity reservation; ElastiCache Valkey as a
scheduling index; multi-AZ or second worker. I4 starts after I3 and E4, from
measured pilot data, per the handoff. Capacity purchases are separately
authorized and are not implied by any design in this document.

**Pre-existing resources of these kinds are not this project's.** An ALB with its
target groups, ACM certificates, Auto Scaling groups, launch templates and one
scheduled `capacity-block` capacity reservation already exist in us-east-1,
belonging to CallGideon and to the llm-bootcamp/DeepSeek benchmark work. **The
counts, identifiers, states and dates are matrix row `M-FLEET` and handback item 12
of the I1 evidence report and are deliberately not repeated here**, so the two files
cannot come to disagree about a number again.

**None of them is created, used, extended, modified or cancelled by any task in
this document**, and nothing here proposes anything about the Capacity Block —
acting on another project's prepaid commitment is separately authorized. Genuinely
absent for this project, per the same row: no on-demand capacity reservation, no
WAF web ACL, no ElastiCache cluster, no self-owned AMI, no infrx load balancer or
target group.

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
  isolate; still read-only — the calls are itemised in §Commands of the evidence
  report and counted in its §Checks:
  `iam get-policy`, `iam get-policy-version`, `ec2 describe-security-groups`,
  `ec2 describe-network-interfaces`, `ssm describe-parameters` and
  `ec2 describe-instances` (the last is the one the security-group row cites for
  the pilot ENI's group set, and an earlier revision of this entry omitted it and
  said "five"); the counts and the closing time are whatever §Commands of the I1
  evidence report says, which is now the only place either is stated. **No**
  resource created,
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
- 2026-09-20 (fifth review pass — convergence; still read-only: `sts` /
  `iam` / `ec2` / `autoscaling` / `ssm` / `route53` / `s3api` / `elasticache` /
  `elbv2` / `acm` / `wafv2` / `rds` / `backup` / `cloudwatch` / `service-quotas` /
  `sesv2` read calls at 20:40:04Z–20:43:17Z, counted and itemised in §Commands and
  §Checks of the I1 evidence report rather than here; **no** resource created, modified or
  deleted, **no** secret value read, **no** remote command, **no** HTTP request, no
  Cost Explorer call):
  - **§5 step 5 no longer tells I2 to write a wildcard.** It said to carry
    `AmazonSSMManagedInstanceCore`'s "`ssmmessages:*` and `ec2messages:*`
    statements verbatim". Re-reading that policy with no `--query` shows it contains
    **no wildcard action**: the channel statements enumerate four `ssmmessages:` and
    six `ec2messages:` actions. The instruction would have shipped a *broader* agent
    policy than the managed one it replaces. Step 5 now builds the customer-managed
    policy by copying evidence row `O-MANAGED`'s enumerated actions and dropping
    exactly `ssm:GetParameter` and `ssm:GetParameters`, and says so explicitly.
  - **Step 6 is new: how the isolation is actually achieved, not just today.**
    Because every SecureString in the account is on the AWS-managed `alias/aws/ssm`
    key (row `O-PARAMS`), the identity-policy narrowing is the only barrier and any
    later re-grant of `ssm:GetParameter*` on `*` silently restores the cross-project
    read. The durable form — a customer-managed key `alias/infrx-pilot` whose key
    policy names only `infrx-pilot-role`, the pilot parameters re-created under it,
    staging on the sibling `/infrx-staging/*` with its own key and role — is a
    **PROPOSAL assigned to I2 in `staging` then `pilot`**, with matrix row `M-KMS`
    and a read-only verification (`ssm describe-parameters` shows the pilot `KeyId`;
    `kms get-key-policy` shows only the pilot role). Steps 5 and 6 each carry the
    read-only check that proves they took effect.
  - **This document stops restating observed facts.** Every fact it needs now
    resolves through a stable evidence row id (`O-…` for observed state, `M-…` for a
    matrix row): the IAM statement lists, the per-group ingress and ENI attachments,
    the volume attributes, the IMDS options, the parameter inventory and §9's
    ASG/launch-template/Capacity-Block listing are cited, not repeated. Four review
    rounds of drift all came from the same duplication.
  - **The two documents can no longer disagree about what was run.** This log's
    fourth-pass entry said "five" calls where the evidence report itemised six and
    omitted the `ec2 describe-instances` call that the security-group row depends
    on; it now says six, names it, and defers the count and the window to the single
    §Commands table. The HTTP request budget is stated **only** in §Commands, which
    records it as closed by the coordinator; nothing here restates it.
  - New facts that reached the design: both pilot security groups allow **all
    egress**, so the tcp/22 change is an ingress control only and `marlin2b-gateway`
    alone cuts no outbound traffic; `marlin2b-gateway` is attached to the pilot ENI
    alone, which is what makes it safe to keep; the managed-policy **attachment** is
    cited at its real observation time rather than 17:44:30Z.
  - Unchanged: no resource was created and no configuration changed by I1; the
    §3.3 thresholds stay `est.` and become a gate only after **E4** measures them
    (coordinator ruling, evidence Limits item 9); prices are still only what
    `cloud-pricing.md` carries, i.e. none for g6e, with the method recorded.
- 2026-09-20 (sixth review pass — the isolation of the fifth pass protected the
  wrong direction; still read-only: one `iam list-entities-for-policy` call at
  21:14:38Z plus two checkout reads, all itemised in §Commands of the I1 evidence
  report; **no** resource created, modified or deleted, **no** secret value read,
  **no** remote command, **no** HTTP request, no Cost Explorer call):
  - **§5's durable control was pointed the wrong way, and is replaced.** The threat
    step 6 named is **outbound** — the pilot host regaining `ssm:GetParameter*` on
    `*` and reading other projects' secrets, all of which sit on the AWS-managed
    `alias/aws/ssm` key. A customer-managed key on the **pilot's own** parameters is
    not on that decryption path, so every regression scenario the step listed would
    still have succeeded with it implemented. The outbound control is now an explicit
    `Deny` on all four parameter-read actions — `ssm:GetParameter`,
    `ssm:GetParameters`, `ssm:GetParametersByPath`, `ssm:GetParameterHistory` — in a
    **permissions boundary** on `infrx-pilot-role`, so no later `Allow` on that role
    can restore the read; it is **required**, matrix row `M-BOUNDARY`, I2, `staging`
    then `pilot`, verified by `iam get-role`, `iam get-policy-version` and an
    `iam simulate-principal-policy` result I2 records.
  - **The key stays, restated as the inbound control** (step 7, `M-KMS`): it protects
    the pilot's own secrets — above all the PROPOSED journal DSN — from the other
    principals that can read them today, which row `O-MPATTACH` now names:
    `AmazonSSMManagedInstanceCore` is attached to **three roles**, ours and two that
    are not, and each decrypts `alias/aws/ssm` for free. Its key policy is specified
    statement by statement instead of defaulted: no account-root `kms:*` delegation,
    so the key policy is the whole story; `kms:Decrypt`/`DescribeKey` for the runtime
    role and for the admin principal that runs the documented `--with-decryption`
    operator flows; `kms:Encrypt`/`GenerateDataKey*`/`ReEncrypt*` for the admin
    principal that runs `ssm put-parameter --key-id`, and none for the runtime, which
    never writes a parameter; `kms:*` for a **named** administrator, because with no
    root delegation an unnamed one cannot rotate or delete the key. If I2 keeps the
    root delegation it records the weaker property that remains. **I2 no longer
    "picks one":** the boundary is required, the key is the additional inbound
    control.
  - **Hop limit 1 does not close what the fifth pass implied it closed.** Row
    `O-NETMODE` records from the checkout that Caddy runs `--network host`
    (`install.sh` line 38) and the engine bridged (`serve.sh` lines 31–32), so after
    the flip the engine container loses IMDS and the TLS front door keeps it. §5 now
    states that residual exposure and assigns the mitigation — Caddy bridged with
    published ports, or an explicit acceptance that `M-BOUNDARY` is what bounds those
    credentials — to I2.
  - §3.3 aligned with the coordinator's ruling: the probe thresholds stay `est.` and
    become a gate **only after E4 measures them**; I2 records numbers and fails
    nothing on them. §5's mode flag gains the two refusals that were missing: an
    **unset or unrecognised `INFRX_MODE` refuses to start** (no default), and `dev`
    also refuses to install or serve the public Caddy site. §9 no longer restates the
    fleet counts, which live only in `M-FLEET`. Regional observations say "in
    us-east-1" rather than "in the account", SSM parameters being regional.
  - **Process, from here on:** corrections are **appended** log entries, never
    in-place edits of earlier ones. Earlier entries *were* edited in place, and this
    is the disclosure: this document's fourth-pass entry was rewritten at the fifth
    pass (the "five calls" count), and the evidence report's fourth-pass entry was
    edited at its fifth-pass commit (a range endpoint) and its fifth-pass entry at
    the sixth (hand-typed counts removed in favour of the committed checker). Nothing
    else was altered after the fact, and nothing will be.
  - Counts, SHAs and "the checks pass" are no longer written here at all: the
    evidence report's §Checks quotes the verbatim output of
    `research/plan/evidence/i/check_i1.py`, and the implementation SHA is stated only
    in its §Source.
- 2026-09-20 (seventh review pass — the boundary as specified could not work; **no
  AWS call was needed or made this pass**, only two reads of the checkout at
  `25b9829`; **no** resource created, modified or deleted, **no** secret value read,
  **no** remote command, **no** HTTP request):
  - **§5 step 6 would have permitted nothing.** A permissions boundary is an
    intersection, not a deny-list, so a boundary carrying only a `Deny` leaves the
    role unable to do anything — the host would not start. Step 6 now specifies an
    `Allow` (either `Action: "*"` on `Resource: "*"` or an enumerated list including
    the agent actions, the bucket and logs/metrics actions, `ec2:Describe*`, the
    scoped parameter reads and **`kms:Decrypt`**) **plus** the explicit `Deny` on the
    four parameter-read actions with `NotResource` on the pilot path, and says why
    `kms:Decrypt` must be in the `Allow`: a key-policy grant to the role is itself
    limited by the boundary, so omitting it would break step 7 silently.
  - **Step 6 also states what the boundary does not cover**: it binds this role only —
    not a profile swap back to `bootcamp-instance-profile` (which has no boundary), not
    static credentials on the host, not an administrator deleting the boundary.
  - **One parameter path: `/model-inference/*`, at the existing names.** The sixth
    pass's `/model-inference/pilot/*` is withdrawn — every consumer, including
    `install.sh` lines 17–19 and the on-the-box `hf_token` flow, already reads
    `/model-inference/*`, so step 7 re-keys the parameters **in place**. `staging`
    keeps its own role, boundary and key on the sibling `/infrx-staging/*`. The
    `hf_token` flow's principal is corrected: it runs **as the instance role** on the
    box, not as an admin principal; the client-side flow at line 51 runs as the
    operator's principal and needs the same two KMS actions.
  - **New ordering hazard, stated before the numbered steps and made a prerequisite.**
    Row `O-FAILOPEN`: today's installer swallows every parameter-read failure,
    truncates the env file and restarts the gateway, and the gateway allows every
    request when neither auth mechanism is configured — so a denied read during an
    install run publishes an unauthenticated, unmetered gateway on the pilot hostname,
    and that is true **today**, without any I2 change. `M-FAILCLOSED` is therefore
    step **0**: fail closed (distinguishing `AccessDenied`/throttle from
    `ParameterNotFound`) → role and boundary in staging → verify by simulation and by
    an install run that a denial aborts → pilot swap → hop limit → SSH.
  - N3: the claim "no wildcard actions" is replaced by the property that matters —
    **no parameter-read action on a `Resource` outside `/model-inference/*`** — since
    the role deliberately keeps `ec2:Describe*`. The IMDS paragraph now agrees with
    `O-NETMODE` that the network modes are the checkout's and the running box was not
    inspected.
  - Counts are gone from this log: the fifth-pass entry's call count and the
    fourth-pass entry's "six" are deleted, because the number belongs to §Commands and
    §Checks of the evidence report, whose checker prints it. The evidence report's
    seventh-pass entry records that the "57" stated in both fifth-pass entries was
    wrong (55), why, and the complete list of log entries that were edited in place
    before this rule took effect.
