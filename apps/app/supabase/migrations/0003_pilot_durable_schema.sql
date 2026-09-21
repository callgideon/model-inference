-- D1: the durable state of the free pilot (research/plan/06-database-map.md).
--
-- Additive only. Nothing here drops a relation, a column or a value; the two
-- `alter column ... type` statements widen money to numeric(20,8) (R11), which adds
-- trailing zeros to the stored scale and changes no value. Rollback notes are in
-- research/plan/evidence/d/.
--
-- Everything new lives in schema `infrx` rather than `public`, for one reason:
-- Supabase grants `anon`, `authenticated` and `service_role` ALL PRIVILEGES on
-- everything created in `public` (and keeps doing so through default privileges), so
-- a relation created there is browser-reachable until a migration remembers to
-- revoke it. A separate schema is deny-by-default; 0004 then revokes explicitly
-- anyway, because "nobody granted it" is not a control.
--
-- Money is numeric(20,8) everywhere (08 §4/R11: scale 1e-8, |value| < 10^12).
-- Timestamps are taken from `infrx.now()` (R7: the database clock, movable only in a
-- task-local test database - see infrx/state/test_clock.sql).

create schema if not exists infrx;

-- ------------------------------------------------------------- the DB clock ---
-- R7/R48-S1: one function is "now" for every durable decision. In production it is
-- `now()` - transaction start, so one transaction sees one instant. In a task-local
-- `infrx_<task>` database it adds the offset of `infrx_test.clock`, which only
-- `infrx/state/test_clock.sql` creates and no deployment applies.
create or replace function infrx.now() returns timestamptz
language plpgsql stable security definer set search_path = infrx, public, pg_temp as $$
declare
  v_offset interval := interval '0';
begin
  -- Production short-circuits here: no subtransaction, no lookup, just now().
  if current_database() like 'infrx@_%' escape '@' then
    begin
      select coalesce(max(offset_s), interval '0') into v_offset from infrx_test.clock;
    exception when undefined_table or invalid_schema_name then
      v_offset := interval '0';
    end;
  end if;
  return now() + v_offset;
end $$;

comment on function infrx.now() is
  'The database clock every durable decision reads (R7). Test-only offset: honoured '
  'only in a task-local infrx_<task> database and only when infrx_test.clock exists, '
  'which no deployment installs.';

-- ------------------------------------------------- append-only enforcement ---
-- 02: "Corrections are compensating entries, never edits/deletes." A row check
-- cannot say that, so the immutable relations get this trigger.
create or replace function infrx.forbid_update_delete() returns trigger
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
begin
  raise exception '% is append-only: % is not permitted',
    tg_table_name, tg_op using errcode = '23514';
end $$;

-- =========================================================== new relations ===

-- price_versions: immutable rows; admission reads the effective one (R45) and the
-- job keeps its snapshot for settlement (R53).
create table infrx.price_versions (
  price_version text primary key,
  model_revision text not null,
  currency text not null default 'USD' check (currency = 'USD'),
  input_rate_per_million numeric(20,8) not null check (input_rate_per_million >= 0),
  output_rate_per_million numeric(20,8) not null check (output_rate_per_million >= 0),
  token_rules_version text not null,
  effective_from timestamptz not null,
  effective_to timestamptz,
  captured_at timestamptz not null default infrx.now(),
  created_by text,
  check (effective_to is null or effective_to > effective_from)
);
create index price_versions_model_effective_idx
  on infrx.price_versions (model_revision, effective_from desc);
create trigger price_versions_immutable before update or delete on infrx.price_versions
  for each row execute function infrx.forbid_update_delete();

-- wallets: the summary row every monetary mutation locks. `available` is generated,
-- so a caller cannot store a third number that disagrees with the other two.
create table infrx.wallets (
  org_id uuid primary key references public.organizations(id) on delete cascade,
  ledger_total numeric(20,8) not null default 0,
  reserved_total numeric(20,8) not null default 0 check (reserved_total >= 0),
  available numeric(20,8) generated always as (ledger_total - reserved_total) stored,
  revision bigint not null default 0,
  updated_at timestamptz not null default infrx.now()
);

-- 02: "New organizations receive a wallet with zero total and no automatic grant."
create or replace function infrx.ensure_wallet() returns trigger
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
begin
  insert into infrx.wallets (org_id) values (new.id) on conflict (org_id) do nothing;
  return new;
end $$;
create trigger organizations_ensure_wallet after insert on public.organizations
  for each row execute function infrx.ensure_wallet();

-- credit_holds: one hold per request, ever (06: "no second active hold per request"),
-- which the primary key states rather than a partial unique index over a state.
create table infrx.credit_holds (
  request_id uuid primary key,
  org_id uuid not null references public.organizations(id) on delete cascade,
  key_id uuid,
  amount numeric(20,8) not null check (amount >= 0),
  state text not null check (state in ('held','settled','released','unknown')),
  reconcile_after timestamptz,
  created_at timestamptz not null default infrx.now(),
  updated_at timestamptz not null default infrx.now(),
  -- The unknown-usage window exists exactly while the hold is unknown: releasing
  -- clears it (02), and no other state may carry one.
  check ((state = 'unknown') = (reconcile_after is not null))
);
create index credit_holds_org_active_idx on infrx.credit_holds (org_id)
  where state in ('held','unknown');
create index credit_holds_reconcile_idx on infrx.credit_holds (reconcile_after)
  where state = 'unknown';

-- capacity_reservations: the admission-time units of 02 §2 (R1 gives preparation its
-- own cap). 06 keys them "request/scope/kind"; the kinds ARE the scopes
-- (`records.ReservationKind`), so the key is (request, kind) and no unused `scope`
-- column is carried. Active counters are the partial indexes below rather than a
-- second relation that can disagree with the rows (02: never counted twice).
create table infrx.capacity_reservations (
  request_id uuid not null,
  kind text not null check (kind in ('preparation','inference','journal_bytes')),
  org_id uuid not null references public.organizations(id) on delete cascade,
  key_id uuid,
  -- Slots, or bytes for journal_bytes.
  amount bigint not null check (amount >= 0),
  active boolean not null default true,
  reserved_at timestamptz not null default infrx.now(),
  released_at timestamptz,
  primary key (request_id, kind),
  check (active = (released_at is null))
);
create index capacity_reservations_active_idx on infrx.capacity_reservations (kind)
  where active;
create index capacity_reservations_org_active_idx
  on infrx.capacity_reservations (org_id, kind) where active;

-- jobs: the request UUID is the primary key (06, R6), the handle is the only
-- customer-facing name (R46).
create table infrx.jobs (
  request_id uuid primary key,
  job_handle text unique not null,
  org_id uuid not null references public.organizations(id) on delete cascade,
  -- r2: the foreign key is composite and RESTRICT, in the tenant-coherence block
  -- below. `on delete set null` could never execute anyway - `jobs_guard` refuses the
  -- referential update - so a key that had admitted a job was simply undeletable with
  -- a misleading error. Pilot policy: keys are revoked, never deleted.
  key_id uuid,
  model_revision text not null,
  execution_mode text not null check (execution_mode in ('sync','stream','async')),
  state text not null
    check (state in ('preparing','queued','running','succeeded','failed','cancelled','expired')),
  operation text not null,
  idempotency_key text,
  -- Immutable input: the canonical body is durable before acceptance (02 §1).
  payload_ref text not null,
  payload_digest text not null check (payload_digest ~ '^sha256:[0-9a-f]{64}$'),
  result_ref text,
  result_expires_at timestamptz,
  max_input_tokens int not null check (max_input_tokens >= 1),
  max_output_tokens int not null check (max_output_tokens >= 1),
  -- R45/R53: the snapshot the store took, kept whole so settlement never re-reads
  -- a rate. `price_version` is the foreign key that makes it traceable.
  price_version text not null references infrx.price_versions(price_version),
  price_snapshot jsonb not null,
  maximum_hold numeric(20,8) not null check (maximum_hold >= 0),
  consent_version int not null,
  trace_mode text not null check (trace_mode in ('off','minimal','full')),
  entitlement_version int not null default 0,
  admitted_at timestamptz not null,
  deadline_at timestamptz not null,
  -- R4: the budgets snapshot, five numbers, immutable after insert.
  budget_preparation_s double precision not null check (budget_preparation_s >= 0),
  budget_queue_wait_s double precision not null check (budget_queue_wait_s >= 0),
  budget_generation_s double precision not null check (budget_generation_s >= 0),
  budget_first_token_s double precision not null check (budget_first_token_s >= 0),
  budget_stall_s double precision not null check (budget_stall_s >= 0),
  -- R20/R38: phase instants derived from the database clock, never past deadline_at.
  preparation_deadline_at timestamptz not null,
  queue_deadline_at timestamptz,
  queue_wait_used_s double precision not null default 0 check (queue_wait_used_s >= 0),
  queued_at timestamptz,
  -- The publication marker (02 §6). Monotonic: see infrx.jobs_guard.
  published boolean not null default false,
  attempts int not null default 0 check (attempts >= 0),
  preparation_attempts int not null default 0 check (preparation_attempts >= 0),
  journal_reserved_bytes bigint not null default 0 check (journal_reserved_bytes >= 0),
  journal_stored_bytes bigint not null default 0 check (journal_stored_bytes >= 0),
  -- Terminal facts, all written by the one settling transaction (02 §7).
  outcome_cause text check (outcome_cause in (
    'completed','client_cancelled','client_disconnected','sync_deadline',
    'queue_wait_expired','deadline_exceeded','invalid_media','preparation_failed',
    'engine_error','engine_incomplete','lost_after_publication','journal_write_failed',
    'retries_exhausted','platform_error')),
  settlement_state text check (settlement_state in (
    'settled','released_free','held_unknown','released_platform_absorbed')),
  usage_certainty text check (usage_certainty in ('authoritative','unknown')),
  debit numeric(20,8) not null default 0 check (debit >= 0),
  settled_at timestamptz,
  reconcile_after timestamptz,
  created_at timestamptz not null default infrx.now(),
  updated_at timestamptz not null default infrx.now(),
  -- Tenant coherence as a foreign-key target: a job-scoped row references
  -- (job, org) together, so no caller-supplied organization can be wrong (R55).
  unique (request_id, org_id),
  constraint jobs_phase_within_deadline
    check (preparation_deadline_at <= deadline_at
           and (queue_deadline_at is null or queue_deadline_at <= deadline_at)),
  constraint jobs_terminal_is_one_fact
    check ((state in ('succeeded','failed','cancelled','expired'))
           = (settled_at is not null)
           and (settled_at is not null) = (outcome_cause is not null)
           and (settled_at is not null) = (settlement_state is not null)),
  -- records.CAUSE_STATES: the pair is one fact. `succeeded`+`engine_error` would be
  -- a free success; `failed`+`completed` would lose a settled debit.
  constraint jobs_cause_matches_state
    check (outcome_cause is null or (case outcome_cause
      when 'completed' then state = 'succeeded'
      when 'client_cancelled' then state = 'cancelled'
      when 'client_disconnected' then state in ('failed','cancelled')
      when 'sync_deadline' then state in ('failed','cancelled')
      when 'queue_wait_expired' then state = 'expired'
      when 'deadline_exceeded' then state in ('failed','expired')
      else state = 'failed' end)),
  -- R21: only a settled outcome carries a debit, and only three causes may be one.
  constraint jobs_debit_only_when_settled
    check (debit = 0 or (settlement_state is not distinct from 'settled'
                         and coalesce(outcome_cause in ('completed','client_cancelled',
                                                        'client_disconnected'), false))),
  -- The 24 h reconciliation window exists exactly while usage is unknown.
  constraint jobs_unknown_needs_reconcile_after
    check ((settlement_state is not distinct from 'held_unknown')
           = (reconcile_after is not null)),
  -- R30: a success the customer cannot fetch is not a success.
  constraint jobs_success_has_result
    check (state <> 'succeeded' or result_ref is not null),
  -- r2, from the review's list of things the schema accepted:
  -- a debit past the reserved envelope is the one number admission promised to bound.
  constraint jobs_debit_within_hold check (debit <= maximum_hold),
  -- 05: a settled debit rests on authoritative usage; held_unknown is its opposite.
  -- r3 (N3): `is distinct from`, throughout. `settlement_state <> 'settled' or
  -- usage_certainty = 'authoritative'` is NULL when either side is NULL, and a CHECK
  -- PASSES on NULL - so a succeeded/completed/settled job with no usage certainty at all
  -- was accepted, which is a settled debit resting on nothing. Every check below that
  -- compares a NULLABLE column inside an OR now uses three-valued-safe operators.
  constraint jobs_settled_usage_is_authoritative
    check (settlement_state is distinct from 'settled'
           or usage_certainty is not distinct from 'authoritative'),
  constraint jobs_unknown_usage_is_not_authoritative
    check (settlement_state is distinct from 'held_unknown'
           or usage_certainty is distinct from 'authoritative'),
  -- R29: a deadline before the admission is a job nothing may ever run.
  constraint jobs_deadline_after_admission check (deadline_at > admitted_at),
  -- R38: the queue budget cannot be overspent by the accounting that tracks it.
  constraint jobs_queue_wait_within_budget
    check (queue_wait_used_s <= budget_queue_wait_s)
);
create index jobs_org_created_idx on infrx.jobs (org_id, created_at desc, request_id);
create index jobs_org_active_idx on infrx.jobs (org_id)
  where state in ('preparing','queued','running');
create index jobs_key_active_idx on infrx.jobs (key_id)
  where state in ('preparing','queued','running');
create index jobs_queue_deadline_idx on infrx.jobs (queue_deadline_at)
  where state = 'queued';
create index jobs_deadline_idx on infrx.jobs (deadline_at)
  where state in ('preparing','queued','running');
create index jobs_reconcile_idx on infrx.jobs (reconcile_after)
  where settlement_state = 'held_unknown';

-- What a mutation may never change: tenant identity, the immutable input refs, the
-- admitted price and budgets, the absolute deadline - and the publication marker,
-- which only ever goes from false to true (02 §6: after it, never regenerate).
-- `queue_deadline_at` is deliberately NOT here: R38 recomputes it at every queued
-- transition from the unspent remainder.
create or replace function infrx.jobs_guard() returns trigger
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
begin
  -- r2: `is distinct from`, and the list is now everything an admitted job promised.
  -- `<>` is NULL for a nullable column, so the old test let `key_id` and
  -- `idempotency_key` change to or from NULL unnoticed; and the comment claimed price
  -- immutability while `price_snapshot`, `maximum_hold`, `model_revision`, the token
  -- ceilings, `consent_version` and `trace_mode` were all rewritable - which is R53's
  -- whole point (settlement uses the ADMITTED snapshot, so a rewritable snapshot is a
  -- rewritable debit).
  if new.request_id is distinct from old.request_id
     or new.org_id is distinct from old.org_id
     or new.job_handle is distinct from old.job_handle
     or new.key_id is distinct from old.key_id
     or new.payload_ref is distinct from old.payload_ref
     or new.payload_digest is distinct from old.payload_digest
     or new.price_version is distinct from old.price_version
     or new.price_snapshot is distinct from old.price_snapshot
     or new.maximum_hold is distinct from old.maximum_hold
     or new.model_revision is distinct from old.model_revision
     or new.execution_mode is distinct from old.execution_mode
     or new.max_input_tokens is distinct from old.max_input_tokens
     or new.max_output_tokens is distinct from old.max_output_tokens
     or new.consent_version is distinct from old.consent_version
     or new.trace_mode is distinct from old.trace_mode
     or new.operation is distinct from old.operation
     or new.idempotency_key is distinct from old.idempotency_key
     or new.admitted_at is distinct from old.admitted_at
     or new.deadline_at is distinct from old.deadline_at
     or new.preparation_deadline_at is distinct from old.preparation_deadline_at
     or new.budget_preparation_s is distinct from old.budget_preparation_s
     or new.budget_queue_wait_s is distinct from old.budget_queue_wait_s
     or new.budget_generation_s is distinct from old.budget_generation_s
     or new.budget_first_token_s is distinct from old.budget_first_token_s
     or new.budget_stall_s is distinct from old.budget_stall_s then
    raise exception 'job %: tenant identity, input refs, price, ceilings, consent and '
      'budgets are immutable', old.request_id using errcode = '23514';
  end if;
  if old.published and not new.published then
    raise exception 'job %: the publication marker cannot be cleared', old.request_id
      using errcode = '23514';
  end if;
  if old.state in ('succeeded','failed','cancelled','expired') then
    if new.state is distinct from old.state then
      raise exception 'job % is terminal (%): it cannot be resurrected as %',
        old.request_id, old.state, new.state using errcode = '23514';
    end if;
    -- r2: the settled facts are as immutable as the state that carries them. A
    -- rewritable `debit` or `result_ref` on a terminal row is a second settlement
    -- wearing the first one's clothes (02 §7: one usage identity, one settlement).
    if new.outcome_cause is distinct from old.outcome_cause
       or new.settlement_state is distinct from old.settlement_state
       or new.usage_certainty is distinct from old.usage_certainty
       or new.debit is distinct from old.debit
       or new.result_ref is distinct from old.result_ref
       or new.settled_at is distinct from old.settled_at then
      raise exception 'job % is terminal: its settlement is immutable', old.request_id
        using errcode = '23514';
    end if;
  end if;
  -- 02: preparing -> queued -> running -> terminal, plus the prepublication requeue
  -- running -> queued, plus cancellation of any nonterminal state.
  if new.state <> old.state and not (
       (old.state = 'preparing' and new.state in ('queued','failed','cancelled','expired'))
    or (old.state = 'queued' and new.state in ('running','failed','cancelled','expired'))
    or (old.state = 'running' and new.state in ('queued','succeeded','failed','cancelled','expired'))
  ) then
    raise exception 'job %: % -> % is not an allowed transition',
      old.request_id, old.state, new.state using errcode = '23514';
  end if;
  new.updated_at := infrx.now();
  return new;
end $$;
create trigger jobs_guard before update on infrx.jobs
  for each row execute function infrx.jobs_guard();

-- r2: and a settled job cannot be deleted. 06 forbids a destructive downgrade of
-- accepted job data, and a DELETE was the one route around every check above.
create or replace function infrx.jobs_no_delete_when_terminal() returns trigger
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
begin
  if old.state in ('succeeded','failed','cancelled','expired') then
    raise exception 'job % is terminal (%): accepted job data is not deletable',
      old.request_id, old.state using errcode = '23514';
  end if;
  return old;
end $$;
create trigger jobs_no_delete_when_terminal before delete on infrx.jobs
  for each row execute function infrx.jobs_no_delete_when_terminal();

-- attempts: keyed (job, kind, generation) - R46's two independent counters.
create table infrx.attempts (
  job_id uuid not null references infrx.jobs(request_id) on delete cascade,
  kind text not null check (kind in ('preparation','inference')),
  generation int not null check (generation >= 1),
  worker_id text not null,
  retry_ordinal int not null default 0 check (retry_ordinal >= 0),
  acquired_at timestamptz not null,
  expires_at timestamptz not null,
  generation_deadline_at timestamptz not null,
  first_token_deadline_at timestamptz,
  published boolean not null default false,
  first_token_at timestamptz,
  finished_at timestamptz,
  released_at timestamptz,
  primary key (job_id, kind, generation),
  -- R46: a preparation lease has no first token to wait for; an inference one does.
  check ((kind = 'preparation') = (first_token_deadline_at is null)),
  check (first_token_deadline_at is null
         or first_token_deadline_at <= generation_deadline_at),
  check (expires_at > acquired_at)
);
-- 06: "one active generation per job" - per kind, since the two phases are separate.
create unique index attempts_one_active_per_kind_idx on infrx.attempts (job_id, kind)
  where released_at is null;
create index attempts_expiring_idx on infrx.attempts (expires_at)
  where released_at is null;

-- staged_media / uploads: one relation, keyed (org, handle), because finalized
-- content is immutable and tenant scoped (contracts README) and an upload becomes
-- one of these rows rather than living in a second table with the same columns.
create table infrx.staged_media (
  org_id uuid not null references public.organizations(id) on delete cascade,
  handle text not null,
  kind text not null check (kind in ('inline','url','upload')),
  state text not null check (state in ('created','finalized','aborted','expired')),
  digest text check (digest ~ '^sha256:[0-9a-f]{64}$'),
  bytes bigint check (bytes >= 0),
  mime text,
  storage_ref text not null,
  profile_version text not null default 'v1',
  duration_s double precision check (duration_s is null or duration_s >= 0),
  max_bytes bigint check (max_bytes is null or max_bytes > 0),
  expires_at timestamptz,
  created_at timestamptz not null default infrx.now(),
  finalized_at timestamptz,
  primary key (org_id, handle),
  -- A finalized object is content-addressed; an unfinalized one has nothing to
  -- address yet (R22: an expired upload window is its own answer).
  check ((state = 'finalized') = (digest is not null and bytes is not null
                                  and mime is not null and finalized_at is not null))
);
create index staged_media_expiry_idx on infrx.staged_media (expires_at)
  where state in ('created','finalized');

create or replace function infrx.staged_media_guard() returns trigger
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
begin
  if old.state = 'finalized' and (
       coalesce(new.digest, '') <> coalesce(old.digest, '')
       or coalesce(new.bytes, -1) <> coalesce(old.bytes, -1)
       or coalesce(new.mime, '') <> coalesce(old.mime, '')
       or new.storage_ref <> old.storage_ref) then
    raise exception 'media %/% is finalized: its content is immutable',
      old.org_id, old.handle using errcode = '23514';
  end if;
  return new;
end $$;
create trigger staged_media_guard before update on infrx.staged_media
  for each row execute function infrx.staged_media_guard();

-- job_media: the sources a job was admitted with and what preparation produced.
-- The composite foreign key is R55: the organization comes from the job row, so
-- attach(jobA, refs_of_org_B) cannot satisfy its own tenant check.
create table infrx.job_media (
  job_id uuid not null,
  org_id uuid not null,
  handle text not null,
  role text not null check (role in ('source','prepared')),
  position int not null default 0 check (position >= 0),
  attached_at timestamptz not null default infrx.now(),
  primary key (job_id, role, handle),
  foreign key (job_id, org_id) references infrx.jobs (request_id, org_id) on delete cascade,
  foreign key (org_id, handle) references infrx.staged_media (org_id, handle)
);

-- idempotency: org + operation + key, with the canonical payload digest that decides
-- replay versus 409, and the tombstone expiry that answers an expired key explicitly.
create table infrx.idempotency (
  org_id uuid not null references public.organizations(id) on delete cascade,
  operation text not null,
  key text not null,
  payload_digest text not null check (payload_digest ~ '^sha256:[0-9a-f]{64}$'),
  request_id uuid,
  feedback_id text,
  grant_ref text,
  created_at timestamptz not null default infrx.now(),
  -- Null while the referenced work is still active (06: "no active-job expiry").
  expires_at timestamptz,
  primary key (org_id, operation, key),
  -- Exactly one result: a replay must return the thing it returned, and R54 forbids
  -- returning a row of another kind.
  check (num_nonnulls(request_id, feedback_id, grant_ref) = 1)
);
create index idempotency_expiry_idx on infrx.idempotency (expires_at)
  where expires_at is not null;

-- stream_chunks: the PostgreSQL output journal. The primary key IS the cursor
-- (08 §3: `<generation>-<sequence>`), so a bounded ordered scan needs no extra index.
create table infrx.stream_chunks (
  job_id uuid not null references infrx.jobs(request_id) on delete cascade,
  generation int not null check (generation >= 1),
  sequence int not null check (sequence >= 1),
  event_type text not null
    check (event_type in ('progress','delta','usage','error','terminal')),
  payload jsonb not null,
  -- JOURNAL_EVENT_MAX_BYTES (R25): an event over the limit is `journal_write_failed`,
  -- never a silently stored one, and `bytes` may not understate what was stored.
  bytes int not null check (bytes >= 0 and bytes <= 1048576),
  committed_at timestamptz not null default infrx.now(),
  expires_at timestamptz not null,
  primary key (job_id, generation, sequence),
  check (octet_length(payload::text) <= 1048576),
  check (bytes >= octet_length(payload::text) - 2)
);
-- R30: the terminal event is derived from the stored outcome, once.
create unique index stream_chunks_one_terminal_idx on infrx.stream_chunks (job_id)
  where event_type = 'terminal';
create index stream_chunks_expiry_idx on infrx.stream_chunks (expires_at);

-- outbox: at-least-once dispatch and projection. An acknowledgment cannot erase
-- source truth, so the row is kept and marked rather than deleted by the consumer.
create table infrx.outbox (
  event_id uuid primary key,
  aggregate_id uuid not null,
  -- r2 (ruling 7): 06 says every tenant record carries `org_id`, and this one did not,
  -- so a projection consumer had to trust the payload to know whose event it was.
  org_id uuid not null references public.organizations(id) on delete cascade,
  kind text not null check (kind in ('prepare_dispatch','inference_dispatch',
    'usage_projection','trace_projection','feedback_projection','judge_projection',
    'callback_delivery')),
  version int not null default 1 check (version >= 1),
  payload jsonb not null default '{}',
  available_at timestamptz not null,
  claimed_at timestamptz,
  claimed_by text,
  acknowledged_at timestamptz,
  attempts int not null default 0 check (attempts >= 0),
  last_error text,
  created_at timestamptz not null default infrx.now(),
  -- 02: bounded canonical refs, not payloads. Large bodies live in object storage.
  check (length(payload::text) <= 4096)
);
-- r3 ruling: `aggregate_id` may name a job, a feedback entry or a judge run, so it cannot
-- carry a foreign key - but when it names a JOB, that job must belong to the event's
-- organization. A projection consumer reads the aggregate to find its subject.
create or replace function infrx.outbox_aggregate_tenant() returns trigger
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
begin
  if exists (select 1 from infrx.jobs j where j.request_id = new.aggregate_id
             and j.org_id <> new.org_id) then
    raise exception 'outbox event % names job %, which is not org %''s',
      new.event_id, new.aggregate_id, new.org_id using errcode = '23503';
  end if;
  return new;
end $$;
create trigger outbox_aggregate_tenant before insert or update on infrx.outbox
  for each row execute function infrx.outbox_aggregate_tenant();

create index outbox_pending_idx on infrx.outbox (available_at, event_id)
  where acknowledged_at is null;
create index outbox_aggregate_idx on infrx.outbox (aggregate_id, kind);
create index outbox_org_idx on infrx.outbox (org_id, created_at desc, event_id);
-- The composite target the delivery rows point at (ruling 7).
create unique index outbox_event_org_idx on infrx.outbox (event_id, org_id);

-- feedback: one signal per row (R3), one persisted shape for labels too (R43).
create table infrx.feedback (
  feedback_id text primary key,
  org_id uuid not null,
  request_id uuid not null,
  author_principal text not null,
  author_role text not null check (author_role in ('customer','operator','judge')),
  channel text not null check (channel in ('api','console')),
  name text not null
    check (name in ('thumb','rating','correction','comment','calibration_label')),
  value_bool boolean,
  value_int int,
  value_text text,
  comment text check (comment is null or length(comment) <= 4000),
  calibration_set boolean not null default false,
  rubric_version int check (rubric_version between 1 and 1000),
  -- R50: server-set, never client-settable, and the marker R41's masking keys on.
  by_operator boolean not null default false,
  idempotency_key text,
  created_at timestamptz not null default infrx.now(),
  -- Ownership is durable job identity, and the pair is the tenant check (02, R55).
  foreign key (request_id, org_id) references infrx.jobs (request_id, org_id) on delete cascade,
  -- R3/R43: the name fixes the value's type and range, and exactly one variant is set.
  -- r3 (N3): `coalesce(…, false)`. With `name = 'rating'` and the text variant set
  -- instead of the integer one, `value_int between 1 and 5` is NULL, `true and NULL` is
  -- NULL, and the row was accepted - a rating whose value is a string.
  constraint feedback_value_matches_name
    check (num_nonnulls(value_bool, value_int, value_text) = 1 and coalesce(case name
      when 'thumb' then value_bool is not null
      when 'rating' then value_int between 1 and 5
      when 'calibration_label' then value_text in
        ('correct','partially_correct','incorrect','unusable')
      else value_text is not null and length(btrim(value_text)) > 0 end, false)),
  constraint feedback_text_bounded
    check (value_text is null or length(value_text) <= 4000),
  -- R43: the three calibration facts cannot disagree.
  constraint feedback_calibration_is_one_fact
    check ((name = 'calibration_label') = calibration_set
           and (name = 'calibration_label') = (rubric_version is not null)),
  -- R54: a label is an operator's verdict.
  constraint feedback_label_is_operator_authored
    check (not calibration_set or author_role = 'operator'),
  -- R55: the converse, for any row - the marker is what the masking keys on.
  constraint feedback_operator_is_marked
    check (author_role <> 'operator' or by_operator)
);
create index feedback_org_created_idx on infrx.feedback (org_id, created_at desc, feedback_id);
create index feedback_request_idx on infrx.feedback (request_id) where not calibration_set;
create index feedback_calibration_idx on infrx.feedback (request_id) where calibration_set;
-- One verdict per operator, request and rubric version; a second is a replay.
create unique index feedback_one_label_per_rubric_idx
  on infrx.feedback (request_id, rubric_version, author_principal) where calibration_set;

-- consent_history: immutable audit records; only a revocation may be added to a row.
create table infrx.consent_history (
  org_id uuid not null references public.organizations(id) on delete cascade,
  consent_version int not null check (consent_version >= 1),
  trace_mode text not null check (trace_mode in ('off','minimal','full')),
  -- R43: 1..90. Zero days is not a retention policy - "keep nothing" is off mode.
  content_retention_days int not null check (content_retention_days between 1 and 90),
  evaluation_consent boolean not null,
  actor_principal text not null,
  by_operator boolean not null default false,
  effective_at timestamptz not null,
  revoked_at timestamptz,
  created_at timestamptz not null default infrx.now(),
  primary key (org_id, consent_version),
  check (revoked_at is null or revoked_at >= effective_at),
  -- 02: "no inferred evaluation consent" - it requires full-mode capture.
  check (not evaluation_consent or trace_mode = 'full')
);
create index consent_history_current_idx on infrx.consent_history (org_id, effective_at desc)
  where revoked_at is null;

create or replace function infrx.consent_guard() returns trigger
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'consent history is immutable' using errcode = '23514';
  end if;
  -- r2 (ruling 9): `is distinct from`, and `created_at` is in the list. With `<>` a
  -- comparison against NULL is NULL, so `set revoked_at = null` slipped past the
  -- revocation test below - a revocation could be undone, which is the one thing a
  -- consent record exists to make impossible - and `created_at` was rewritable.
  if new.org_id is distinct from old.org_id
     or new.consent_version is distinct from old.consent_version
     or new.trace_mode is distinct from old.trace_mode
     or new.content_retention_days is distinct from old.content_retention_days
     or new.evaluation_consent is distinct from old.evaluation_consent
     or new.actor_principal is distinct from old.actor_principal
     or new.by_operator is distinct from old.by_operator
     or new.effective_at is distinct from old.effective_at
     or new.created_at is distinct from old.created_at then
    raise exception 'consent %/% is immutable; record a new version instead',
      old.org_id, old.consent_version using errcode = '23514';
  end if;
  if old.revoked_at is not null and new.revoked_at is distinct from old.revoked_at then
    raise exception 'consent %/% is already revoked', old.org_id, old.consent_version
      using errcode = '23514';
  end if;
  return new;
end $$;
create trigger consent_history_guard before update or delete on infrx.consent_history
  for each row execute function infrx.consent_guard();

-- judge_budgets / reservations: per organization AND period (06). D6 owns the period
-- definition and the limit source; the schema keys them so it can.
create table infrx.judge_budgets (
  org_id uuid not null references public.organizations(id) on delete cascade,
  period_start timestamptz not null,
  period_end timestamptz not null,
  limit_usd numeric(20,8) not null check (limit_usd >= 0),
  reserved_usd numeric(20,8) not null default 0 check (reserved_usd >= 0),
  settled_usd numeric(20,8) not null default 0 check (settled_usd >= 0),
  updated_at timestamptz not null default infrx.now(),
  primary key (org_id, period_start),
  check (period_end > period_start)
);

create table infrx.judge_runs (
  run_id uuid primary key,
  org_id uuid not null references public.organizations(id) on delete cascade,
  consent_version int not null,
  rubric_version int not null check (rubric_version between 1 and 1000),
  model_revision text not null,
  reserved_cost numeric(20,8) not null default 0 check (reserved_cost >= 0),
  actual_cost numeric(20,8) check (actual_cost is null or actual_cost >= 0),
  -- 02: one unique submission intent, persisted before egress.
  submit_intent uuid unique,
  external_batch_id text,
  state text not null check (state in ('dry_run','reserved','submitting','submitted',
    'ambiguous','collecting','settled','quarantined','cancelled')),
  created_at timestamptz not null default infrx.now(),
  reconciled_at timestamptz,
  foreign key (org_id, consent_version)
    references infrx.consent_history (org_id, consent_version),
  -- A run that reached the provider has an intent; one past submission has an id.
  check (state in ('dry_run','reserved','cancelled') or submit_intent is not null),
  check (state not in ('submitted','collecting','settled') or external_batch_id is not null)
);
create index judge_runs_org_state_idx on infrx.judge_runs (org_id, state, created_at desc);

create table infrx.judge_reservations (
  run_id uuid primary key references infrx.judge_runs(run_id) on delete cascade,
  org_id uuid not null,
  period_start timestamptz not null,
  amount numeric(20,8) not null check (amount >= 0),
  settled_amount numeric(20,8) check (settled_amount is null or settled_amount >= 0),
  price_version text references infrx.price_versions(price_version),
  state text not null check (state in ('held','settled','released')),
  created_at timestamptz not null default infrx.now(),
  foreign key (org_id, period_start)
    references infrx.judge_budgets (org_id, period_start),
  -- 02: a settlement may not exceed the reservation it was authorized by.
  check (settled_amount is null or settled_amount <= amount)
);
create index judge_reservations_budget_idx
  on infrx.judge_reservations (org_id, period_start) where state = 'held';

create table infrx.judge_samples (
  run_id uuid not null references infrx.judge_runs(run_id) on delete cascade,
  -- r2 (ruling 7): the sample's tenant, so `request_id` can be proved to belong to it.
  org_id uuid not null references public.organizations(id) on delete cascade,
  sample_id text not null,
  rubric_version int not null check (rubric_version between 1 and 1000),
  request_id uuid,
  scores jsonb,
  collected_at timestamptz,
  created_at timestamptz not null default infrx.now(),
  -- 02: late results deduplicate by run/sample/rubric version.
  primary key (run_id, sample_id, rubric_version)
);

-- callbacks: a registered HTTPS destination per organization, deliveries keyed by
-- the stable event id so a retry cannot double-deliver.
create table infrx.callback_destinations (
  destination_id uuid primary key,
  org_id uuid not null references public.organizations(id) on delete cascade,
  url text not null check (url like 'https://%'),
  signing_key_ref text not null,
  signing_key_version int not null default 1 check (signing_key_version >= 1),
  active boolean not null default true,
  created_at timestamptz not null default infrx.now(),
  created_by text,
  unique (org_id, url),
  unique (destination_id, org_id)
);

create table infrx.callback_deliveries (
  event_id uuid not null,
  destination_id uuid not null,
  -- r2 (ruling 7): one tenant for the pair, so ORG_A's event cannot be delivered to
  -- ORG_B's registered destination - which is an egress of one tenant's data to
  -- another's endpoint, not merely a bookkeeping error.
  org_id uuid not null,
  state text not null check (state in ('pending','delivered','failed','dead_letter')),
  attempts int not null default 0 check (attempts >= 0),
  next_attempt_at timestamptz,
  last_status int,
  dead_letter_reason text,
  created_at timestamptz not null default infrx.now(),
  delivered_at timestamptz,
  primary key (event_id, destination_id),
  foreign key (event_id, org_id) references infrx.outbox (event_id, org_id)
    on delete cascade,
  foreign key (destination_id, org_id)
    references infrx.callback_destinations (destination_id, org_id) on delete cascade,
  check ((state = 'delivered') = (delivered_at is not null)),
  check ((state = 'dead_letter') = (dead_letter_reason is not null))
);
create index callback_deliveries_ready_idx on infrx.callback_deliveries (next_attempt_at)
  where state = 'pending';

-- org_entitlements (R24). `model_ids` is NULLABLE and distinguishes '{}':
--   null = the platform default set, '{}' = nothing entitled, a list = exactly it.
-- A `not null default '{}'` column would silently deny every organization, so the
-- test asserts the absence of both. The three limits are typed columns from the
-- closed ENTITLEMENT_LIMIT_NAMES set, not a free JSON bag.
create table infrx.org_entitlements (
  org_id uuid primary key references public.organizations(id) on delete cascade,
  model_ids text[],
  max_concurrent_requests int check (max_concurrent_requests between 0 and 1000000),
  max_requests_per_minute int check (max_requests_per_minute between 0 and 1000000),
  max_video_seconds int check (max_video_seconds between 0 and 1000000),
  entitlement_version int not null default 1 check (entitlement_version >= 1),
  reason text check (reason is null or length(reason) <= 500),
  updated_at timestamptz not null default infrx.now(),
  updated_by text
);

-- audit_entries (R34): append-only, one row per operator action, before/after states.
create table infrx.audit_entries (
  id uuid primary key,
  at timestamptz not null default infrx.now(),
  actor_principal text not null,
  action text not null check (action in
    ('admin_grant','admin_set_suspension','admin_set_entitlements','calibration_label')),
  target_org_id uuid references public.organizations(id) on delete set null,
  reason text not null check (length(reason) between 1 and 500),
  before jsonb,
  after jsonb,
  idempotency_key text
);
create index audit_entries_target_idx on infrx.audit_entries (target_org_id, at desc, id);
create trigger audit_entries_immutable before update or delete on infrx.audit_entries
  for each row execute function infrx.forbid_update_delete();

-- ==================================== additive changes to existing relations ===

-- Suspension lives on the organization (R33: it gates new work and configuration
-- changes only, and never alters existing terminal accounting). 0004 revokes the
-- column grants that would otherwise let an owner clear their own suspension.
--
-- r2 (review R59 ruling 2): there is NO `suspended_by` column and `suspension_reason`
-- is a closed CODE, not free text. `public.organizations` is a relation members
-- SELECT (0001's `organizations_select`), and the deployed console may `select *`, so
-- an operator's identity or their prose stored here reaches every member of the
-- suspended organization. The operator principal and their free-text reason live in
-- `infrx.audit_entries` (R34), which no browser role can read.
alter table public.organizations
  add column if not exists suspended boolean not null default false,
  add column if not exists suspended_at timestamptz,
  add column if not exists suspension_reason text;
alter table public.organizations
  add constraint organizations_suspension_reason_check
    check (suspension_reason is null or suspension_reason in
           ('abuse','nonpayment','security','operator_request','other')),
  -- A suspension a customer cannot date or explain is a support ticket nobody can
  -- answer, so the marker and its two facts travel together.
  add constraint organizations_suspension_is_complete
    check (suspended = (suspended_at is not null)
           and suspended = (suspension_reason is not null));

-- credit_ledger: precision expanded, provenance added. numeric(14,6) ->
-- numeric(20,8) widens the stored scale (1.500000 -> 1.50000000) and changes no
-- value; the upgrade test compares numerically and by sum.
--
-- r2 (ruling 2): NO `operator_principal` column either, for the same reason - members
-- already hold SELECT on this table. `by_operator` stays, because a boolean carries no
-- more than the literal `platform` the masked view shows anyway (ruling 3); who the
-- operator was is in `infrx.audit_entries`, linked by `operation_id`.
alter table public.credit_ledger
  alter column delta_usd type numeric(20,8);
alter table public.credit_ledger
  add column if not exists operation_id text,
  add column if not exists request_id uuid,
  add column if not exists by_operator boolean not null default false,
  add column if not exists idempotency_key text;
-- A stable operation id makes an operator grant idempotent (02) without a second
-- ledger row. Null for the history that predates it.
create unique index credit_ledger_operation_idx on public.credit_ledger (operation_id)
  where operation_id is not null;
create index credit_ledger_request_idx on public.credit_ledger (request_id)
  where request_id is not null;
-- r2 (ruling 7): tenant coherence for the pilot rows. Historical rows carry no
-- `request_id`, so the composite key is simply not enforced for them - no history is
-- rewritten and the constraint is VALID from the start.
alter table public.credit_ledger
  add constraint credit_ledger_request_belongs_to_org
    foreign key (request_id, org_id) references infrx.jobs (request_id, org_id);
-- r2 (ruling 7): money signs by kind, and one usage debit per request. Added NOT VALID
-- because production history is not this repository's to re-examine: every future row
-- and every update is checked, no existing row is touched or rewritten, and a
-- `validate constraint` can be run once history has been reviewed.
alter table public.credit_ledger
  add constraint credit_ledger_delta_is_nonzero check (delta_usd <> 0) not valid,
  add constraint credit_ledger_sign_matches_kind
    check (case kind when 'grant' then delta_usd > 0
                     when 'purchase' then delta_usd > 0
                     when 'usage' then delta_usd < 0
                     else true end) not valid,
  -- An operator-made entry is the one an audit entry explains, and `operation_id` is
  -- the link (02: a grant needs a stable idempotency key and an audit record).
  add constraint credit_ledger_operator_entry_is_traceable
    check (not by_operator or operation_id is not null) not valid;
create unique index credit_ledger_one_usage_per_request
  on public.credit_ledger (request_id) where kind = 'usage' and request_id is not null;
-- 02: append-only. Corrections are compensating entries, never edits.
create trigger credit_ledger_append_only before update or delete on public.credit_ledger
  for each row execute function infrx.forbid_update_delete();

-- r2 (ruling 8): the wallet total moves in the same transaction as the ledger row,
-- whatever writes it. The deployed console's `addCredit` inserts through the service
-- role and knows nothing about `infrx.wallets`; before this trigger that insert left
-- `wallet_reconciliation.ledger_drift` nonzero and `org_wallet_summary` disagreeing
-- with `org_balance` until somebody noticed. D5's settlement does the same thing -
-- insert the ledger row and let the trigger move the total - so there is exactly one
-- writer of `ledger_total` and no writer can diverge from the ledger.
create or replace function infrx.ledger_moves_wallet() returns trigger
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
begin
  insert into infrx.wallets (org_id, ledger_total, revision, updated_at)
  values (new.org_id, new.delta_usd, 1, infrx.now())
  on conflict (org_id) do update
    set ledger_total = infrx.wallets.ledger_total + new.delta_usd,
        revision = infrx.wallets.revision + 1,
        updated_at = infrx.now();
  return null;
end $$;
create trigger credit_ledger_moves_wallet after insert on public.credit_ledger
  for each row execute function infrx.ledger_moves_wallet();

-- usage_events: the numeric HTTP status stays exactly as it is (02); the terminal
-- outcome is a separate text column. `settlement_regime` marks every row that
-- predates the pilot as outside the settlement regime, so nothing can replay it
-- into a debit: the default is 'legacy', and only D5's settlement writes 'pilot'.
alter table public.usage_events
  alter column cost_usd type numeric(20,8);
alter table public.usage_events
  add column if not exists settlement_regime text not null default 'legacy',
  add column if not exists outcome text,
  add column if not exists job_state text,
  add column if not exists settlement_state text,
  add column if not exists usage_certainty text,
  add column if not exists price_version text,
  add column if not exists settlement_version int;
alter table public.usage_events
  add constraint usage_events_regime_check
    check (settlement_regime in ('legacy','pilot')),
  add constraint usage_events_outcome_check
    check (outcome is null or outcome in (
      'completed','client_cancelled','client_disconnected','sync_deadline',
      'queue_wait_expired','deadline_exceeded','invalid_media','preparation_failed',
      'engine_error','engine_incomplete','lost_after_publication','journal_write_failed',
      'retries_exhausted','platform_error')),
  add constraint usage_events_settlement_state_check
    check (settlement_state is null or settlement_state in (
      'settled','released_free','held_unknown','released_platform_absorbed')),
  add constraint usage_events_certainty_check
    check (usage_certainty is null or usage_certainty in ('authoritative','unknown')),
  -- A pilot row is the one settlement of one request; the legacy regime had no
  -- such identity and must not be given one retroactively.
  add constraint usage_events_pilot_is_settled
    check (settlement_regime = 'legacy'
           or (outcome is not null and settlement_state is not null
               and settlement_version is not null));
-- r2: `usage_events_pilot_settlement_idx (id, settlement_version)` was redundant with
-- the primary key on `id` and is gone. One settlement per request is the PK.

-- r2 (ruling 7): tenant coherence for the pilot regime only. A foreign key cannot be
-- conditional and legacy `id`s are not job ids, so the rule is a trigger scoped to
-- `settlement_regime = 'pilot'`: no historical row is examined, rewritten or dropped.
create or replace function infrx.usage_pilot_row_matches_job() returns trigger
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
begin
  if new.settlement_regime = 'pilot' then
    if not exists (select 1 from infrx.jobs j
                   where j.request_id = new.id and j.org_id = new.org_id) then
      raise exception 'usage row % is not the settlement of a job owned by org %',
        new.id, new.org_id using errcode = '23503';
    end if;
    -- r3 ruling: and the key it names is that organization's. A metered row attributed to
    -- another tenant's key is another tenant's key name on this tenant's usage page.
    if new.api_key_id is not null
       and not exists (select 1 from public.api_keys k
                       where k.id = new.api_key_id and k.org_id = new.org_id) then
      raise exception 'usage row % names api key %, which is not org %''s',
        new.id, new.api_key_id, new.org_id using errcode = '23503';
    end if;
  end if;
  return new;
end $$;
create trigger usage_events_pilot_tenant before insert or update on public.usage_events
  for each row execute function infrx.usage_pilot_row_matches_job();

-- models.limits is EXTENDED, not duplicated (02): the pilot's per-model controls go
-- into the existing jsonb, leaving the values already there untouched. It is a
-- function because re-running `0002_seed_models.sql` (which the supabase README
-- documents) sets `limits = excluded.limits` and wipes the merge: an operator who does
-- that runs `select infrx.extend_model_limits();
-- 0004 revokes every `infrx` function from the browser roles; this one is meant to be
-- callable by a platform client, so it is granted there.` to put it back.
create or replace function infrx.extend_model_limits() returns int
language sql security definer set search_path = infrx, public, pg_temp as $$
  with merged as (
    update public.models
    set limits = jsonb_build_object('max_output_tokens', coalesce(max_output_tokens, 2048),
                                    'max_context_tokens', context_tokens) || limits
    returning 1)
  select count(*)::int from merged;
$$;
select infrx.extend_model_limits();
-- 0004 revokes every `infrx` function from the browser roles; this one is meant to be
-- callable by a platform client, so it is granted there.

-- ==================================================== tenant coherence (r2) ===
-- Ruling 7: every relation that names two tenant-bearing things proves they are the
-- same tenant with a composite foreign key, rather than trusting whoever inserted the
-- row. Each of these was accepted before, as `service_role`, with two organizations
-- mixed in one row - which is the class of defect R55 exists to remove, and no amount
-- of care in D2-D6 would have caught it after the fact.
--
-- The composite targets first (all trivially unique: each is a primary key plus its
-- organization), then the references.
alter table public.api_keys add constraint api_keys_org_id_key unique (org_id, id);
alter table infrx.feedback add constraint feedback_org_id_key unique (feedback_id, org_id);
alter table infrx.judge_runs add constraint judge_runs_org_id_key unique (run_id, org_id);

alter table infrx.jobs
  add constraint jobs_key_belongs_to_org
    foreign key (org_id, key_id) references public.api_keys (org_id, id)
    on delete restrict;

alter table infrx.credit_holds
  add constraint credit_holds_job_belongs_to_org
    foreign key (request_id, org_id) references infrx.jobs (request_id, org_id)
    on delete restrict,
  add constraint credit_holds_key_belongs_to_org
    foreign key (org_id, key_id) references public.api_keys (org_id, id)
    on delete restrict;

alter table infrx.capacity_reservations
  add constraint capacity_reservations_job_belongs_to_org
    foreign key (request_id, org_id) references infrx.jobs (request_id, org_id)
    on delete restrict,
  add constraint capacity_reservations_key_belongs_to_org
    foreign key (org_id, key_id) references public.api_keys (org_id, id)
    on delete restrict;

-- An idempotency row refers to exactly one result (the row check above); whichever it
-- is, it belongs to the row's organization. A null reference is not constrained, which
-- is what makes one composite key per kind work.
alter table infrx.idempotency
  add constraint idempotency_job_belongs_to_org
    foreign key (request_id, org_id) references infrx.jobs (request_id, org_id)
    on delete restrict,
  add constraint idempotency_feedback_belongs_to_org
    foreign key (feedback_id, org_id) references infrx.feedback (feedback_id, org_id)
    on delete restrict;

alter table infrx.judge_reservations
  add constraint judge_reservations_run_belongs_to_org
    foreign key (run_id, org_id) references infrx.judge_runs (run_id, org_id)
    on delete cascade;

alter table infrx.judge_samples
  add constraint judge_samples_run_belongs_to_org
    foreign key (run_id, org_id) references infrx.judge_runs (run_id, org_id)
    on delete cascade,
  add constraint judge_samples_request_belongs_to_org
    foreign key (request_id, org_id) references infrx.jobs (request_id, org_id)
    on delete restrict;

-- ===================================================== statement-level guards ===
-- Ruling 4 / B3: RLS does not apply to TRUNCATE and a row trigger never sees it, so
-- `truncate public.credit_ledger cascade` erased the ledger as `anon`. The privilege
-- goes (0004), and the relations whose whole point is that nothing is ever removed get
-- a statement trigger that refuses it for EVERY role, `service_role` and the migration
-- owner included: a privilege can be re-granted by accident, a trigger cannot.
create or replace function infrx.forbid_truncate() returns trigger
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
begin
  raise exception '% is append-only: TRUNCATE is never permitted', tg_table_name
    using errcode = '23514';
end $$;

do $$
declare
  r text;
begin
  foreach r in array array[
      'public.credit_ledger', 'public.usage_events', 'infrx.jobs', 'infrx.attempts',
      'infrx.credit_holds', 'infrx.capacity_reservations', 'infrx.stream_chunks',
      'infrx.outbox', 'infrx.idempotency', 'infrx.feedback', 'infrx.consent_history',
      'infrx.price_versions', 'infrx.audit_entries', 'infrx.wallets',
      'infrx.judge_runs', 'infrx.judge_reservations', 'infrx.judge_samples',
      'infrx.callback_deliveries']
  loop
    execute format('create trigger %I before truncate on %s for each statement '
                   'execute function infrx.forbid_truncate()',
                   replace(r, '.', '_') || '_no_truncate', r);
  end loop;
end $$;

-- ===================================================== wallet summary import ===
-- Every existing organization gets a wallet whose ledger_total is the sum of its
-- immutable ledger history; an organization with no history gets zero. New
-- organizations get zero through infrx.ensure_wallet(). No grant is created here:
-- importing a summary is not a top-up.
insert into infrx.wallets (org_id, ledger_total)
select o.id, coalesce(sum(l.delta_usd), 0)
from public.organizations o
left join public.credit_ledger l on l.org_id = o.id
group by o.id
on conflict (org_id) do update set ledger_total = excluded.ledger_total;

-- The reconciliation query 06 requires, as a view so it can be run at any time:
-- a summary must equal the immutable ledger plus the active holds, for ever.
create or replace view infrx.wallet_reconciliation as
select w.org_id,
       w.ledger_total,
       coalesce(l.total, 0) as ledger_sum,
       w.reserved_total,
       coalesce(h.total, 0) as active_holds,
       w.ledger_total - coalesce(l.total, 0) as ledger_drift,
       w.reserved_total - coalesce(h.total, 0) as reserved_drift
from infrx.wallets w
left join (select org_id, sum(delta_usd) as total from public.credit_ledger group by org_id) l
  on l.org_id = w.org_id
left join (select org_id, sum(amount) as total from infrx.credit_holds
           where state in ('held','unknown') group by org_id) h
  on h.org_id = w.org_id;

comment on view infrx.wallet_reconciliation is
  'Wallet summary versus the immutable ledger and active holds. A row with a nonzero '
  'ledger_drift or reserved_drift is a reconciliation failure (06).';
