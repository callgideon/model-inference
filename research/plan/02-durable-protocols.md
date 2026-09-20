# Durable protocols and failure boundaries

## Acceptance and execution

1. Bound intake, authenticate and validate shape. Stage canonical immutable payload and inline media in durable object storage; validate finalized upload ownership. Public URL sources are fetched during preparation, but the original request payload is durable before acceptance. No successful acceptance may depend on gateway-local temporary bytes. A staging failure creates no job or hold; abandoned staged objects are garbage-collected.
2. In one PostgreSQL transaction, take locks in the documented global order (capacity scope, org, key, wallet; lexicographic IDs within a class). Recheck authorization, entitlement, suspension, limits, idempotency and available balance. Reserve separate preparation and inference admission capacities, reserve maximum credits, insert `preparing` job and preparation dispatch outbox. Only committed state permits 202 or opening accepted SSE. Retry after an ambiguous acknowledgment with the same idempotency key.
3. Preparation uses bounded CPU/network resources and a fenced lease. Store immutable prepared refs before atomically setting `queued` and inserting inference dispatch outbox. Reserve inference capacity at admission, not after expensive media work. Failure settles terminal state and releases both reservations once.
4. Outbox dispatch is at least once. Q indexes stable event/job IDs. PG state/lease claim chooses the actual winner; index membership alone never authorizes execution. A reconciler repairs queued jobs absent from the index and removes stale candidates. Capacity is held in PG, not inferred from queue length.
5. Claim increments generation using DB time; heartbeat and every mutation compare generation, owner, state and lease expiry. No stale worker can append, settle or renew after being fenced. Recovery may requeue only attempts without any committed output/publication marker, within attempt and absolute-deadline limits.
6. StreamStore batches at most 50ms before commit, then relays committed events. First append atomically establishes output ownership/publication marker. After that marker, never regenerate the request after loss: mark terminal failure if continuation cannot be proven. Persisting output before a client reads is enough to prohibit regeneration.
7. Store immutable final result object, then a single transaction inserts terminal outcome, authoritative usage, ledger settlement, capacity releases, terminal journal event and projection outboxes. Emit non-stream success or successful SSE termination only after this commits. Upstream completion markers must not bypass settlement. Orphan result blobs are safe for GC.

Allowed states: `preparing -> queued -> running -> succeeded|failed|cancelled|expired`; prepublication recovery may transition running to queued with generation fencing. Cancellation can terminalize any nonterminal state through one serialized operation. A completed job stays completed if cancellation loses the race. Stale/out-of-order transitions return domain conflicts without side effects.

Terminalization releases execution/preparation capacity and unused journal reservation. Stored unexpired journal bytes remain charged to the global journal budget until pruning; otherwise rapid completions could overrun disk despite an apparently free active-job counter. Reservations and stored bytes must never be counted twice. Admission and append enforce this accounting under the same capacity locking discipline.

## Output and unknown outcomes

PG journal is mandatory in both pilot and fleet, independent of Valkey configuration. Keep chunks for one hour, immutable results for 24h. Enforce per-job and global byte limits and bounded reads; Use the provisional byte limits in contracts v1; F2 encodes them and E validates load before launch. When journal capacity cannot be reserved, reject admission; when an unforeseen write failure occurs, stop execution/relay and fail or reconcile safely. Never continue publishing uncommitted chunks or silently truncate a successful result.

If durability becomes inaccessible, an established stream may report a safe transport/status-unknown error, but must not claim a committed terminal state. Recovery fences execution and reconciles the job. A terminal error event may be followed by the SSE completion sentinel; its outcome remains failure. A single-node outage can sever a physical connection; no zero-drop or zero-5xx claim is made. Clients use owned job status, idempotency and retained event cursors to resolve ambiguity.

Known authoritative usage on customer cancellation may consume promotional credits. Platform-caused failures, rejected requests and invalid preparation are free. Successful terminalization produces one usage identity and one settlement even when outboxes replay. Unknown usage holds enter reconciliation; after 24h, only once execution is fenced and job terminal, release permanently as platform-absorbed. Late evidence records internal cost but never creates a delayed customer debit. Alert on repeated unknown outcomes or reconciliation backlog.

## Promotional credit migration

D owns all migrations. Extend `models.limits`; do not add a duplicate column. Preserve numeric `usage_events.status` HTTP field and add textual outcome separately. Expand precision transactionally and preserve prior ledger entries exactly. Import existing ledger totals into wallet summaries with a reconciliation query. Mark historical usage as outside the new settlement regime; never replay it into debits. New organizations receive a wallet with zero total and no automatic grant.

Grant operations need a stable idempotency key, operator identity, allowed grant kind, nonempty reason and append-only audit record. Corrections are compensating entries, never edits/deletes. Admission snapshots prices before dispatch, not at usage shipping. Unknown/unpriced models fail closed. RLS alone is insufficient when broad existing update grants cover new protected fields: explicitly restrict column grants and expose narrowly authorized RPCs/services. Browser roles must not write balances, holds, entitlements, suspension, job ownership, author provenance or platform roles.

## Trace loss and projections

Capture is outside the mandatory accounting transaction. Do not make full trace content an acceptance dependency. Per process: provisional 256 MiB combined active/queued capture budget, including an 8 MiB metadata reserve; maximum 10, 000 queued records. Account bytes while content is being accumulated, not only when enqueued. On content-budget breach discard the whole incomplete content capture and mark loss; no retained partial content pretending to be complete. Metadata exhaustion drops with counters.

Dedicated bounded spool writer/executor keeps disk work off the event loop; separate shipper drains acknowledged segments. Host spool cap 10 GiB with at least 2 GiB free disk. Flush/fsync batches at most every 2s; report in-memory, appended and fsynced states separately. Durability begins only after fsync on persistent storage. Crash may lose unsynced events; host/volume loss is outside local-spool durability. No copytruncate. Pause/drop capture at limits while inference continues. A claimed 24h recovery window requires measured ingress to fit actual spool bytes.

ClickHouse is an eventually consistent projection. Execute candidate DDL against the pinned server: stable event IDs, nonnullable monotonic version and deduplicating views/queries are required. Retries must not double-count scores, feedback, usage or traces. TTL parts are physical cleanup, not an authorization boundary. Every content and query path filters logical expiry and tenant. Off-mode jobs are excluded from trace coverage denominators; trace loss and eligible counts are visible separately from accepted/failed/rejected requests.

## Feedback and judge

Feedback acceptance is a PG transaction plus outbox. Ownership checks use durable job/usage identity, not eventual ClickHouse arrival. Store channel (`api`/`console`) independently from author role (`customer`/`operator`/`judge`). Console-origin input is not automatically an operator label. Calibration-set membership requires explicit platform-operator authorization; clients cannot stamp provenance.

Judge workflow: select consenting full traces -> dry-run estimate -> reserve worst-case budget transactionally -> persist unique submission intent -> submit once -> record provider ID -> collect stable sample scores -> settle and project. Current consent plus audit history are required; revoked/expired/missing content is skipped before egress. Reserve maximum output including configured reasoning tokens using versioned provider rates; include outstanding runs in available budget. Pricing estimates without a hard maximum cannot authorize live submission.

Submission timeout without a known provider ID is `ambiguous`, with its reservation held and run quarantined. Disable automatic create retries unless documented provider idempotency makes retries safe. Reconcile using provider evidence; do not create a second billable batch to resolve uncertainty. Late results deduplicate by run/sample/rubric version. No media means no groundedness pass: record limited evaluation. Validate JSON structure and allowed scores before projection. Default live budget is zero and default mode dry-run.

## Verification log

- 2026-09-20: Review fixes codified for durable acceptance, first-output fencing, terminal settlement, trace loss and external submission ambiguity. No live fault tests performed in this documentation change.
