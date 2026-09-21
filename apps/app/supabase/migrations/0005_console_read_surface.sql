-- D1: the console's read surface over the pilot schema.
--
-- C1 froze thirteen named PostgreSQL reads (`apps/app/lib/services/query.ts`) and the
-- coordinator ruled that they go through D1-owned SQL reached with the existing
-- `@supabase/supabase-js` client - no new console dependency - in the style of
-- `org_usage_summary`/`org_balance`. This file is that surface.
--
-- How the views are safe, since they are the one place where a browser role reaches
-- pilot data at all:
--
--   * they live in `public` (PostgREST exposes only `public`) and read `infrx` with the
--     **view owner's** rights, because a `security_invoker` view would be denied the
--     schema and there would be nothing to grant short of opening `infrx` itself;
--   * therefore each view carries its own tenant predicate - the same
--     `is_org_member`/`is_operator` guard the existing reporting functions use - so
--     owner's rights never mean "every tenant's rows";
--   * `is_service_client()` lets a server action using the platform key read what it
--     already could through the tables it bypasses RLS on, and nothing else;
--   * operator principals are masked **in SQL** (R41/R50), so a customer session reads
--     `platform` even if a caller forgets to project. This is why `credit_ledger` does
--     not simply gain an `actor` column: a stored, unmasked actor is readable by every
--     member through the existing `credit_ledger_select` policy, which is the leak R41
--     exists to close.
--   * `public.feedback` never carries a calibration label, for anyone (R35/R49); labels
--     are read only through `public.calibration_labels`, which is operator-only.

-- ------------------------------------------------------- who is asking, in SQL ---
-- SECURITY **INVOKER**, deliberately: a definer function would read `current_user` as
-- its own owner (`postgres`, a superuser), so `pg_has_role` would answer true for every
-- caller and every view below would return every tenant's rows. `pg_has_role` and
-- `current_user` need no privilege of their own, so there is nothing to elevate for.
create or replace function public.is_service_client() returns boolean
language sql stable security invoker set search_path = public, pg_temp as $$
  -- True for the platform key (PostgREST sets `role` from the JWT) and for a
  -- superuser applying migrations; false for `anon` and `authenticated`.
  select pg_has_role(current_user, 'service_role', 'usage');
$$;
revoke all on function public.is_service_client() from public;
grant execute on function public.is_service_client() to authenticated, service_role;

-- ------------------------------------------- whose principal may be read (r2) ---
-- Ruling 1 (R59), and B2(a): a principal leaves this database towards a customer
-- session ONLY when it is provably a member of that organization. The previous
-- masking keyed on `by_operator`, which FAILED OPEN three ways: it defaults false for
-- every historical row, the deployed console's `addCredit` writes `created_by =
-- <operator uuid>` without setting it, and a caller that forgets it gets no masking at
-- all. Membership is a fact in `org_members`; a marker is a promise somebody has to
-- keep.
--
-- `security invoker`, so the `org_members` lookup runs under the caller's RLS: a
-- session can only prove membership of an organization it can already see, which makes
-- the closed direction the default.
create or replace function public.principal_uuid(p_principal text) returns uuid
language sql immutable as $$
  -- A principal may be a user id, a key id, an operator address or a service name. Only
  -- the UUID shape can name a member, and a regex is cheaper (and safer) than an
  -- exception block per row.
  select case when p_principal ~
    '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
    then p_principal::uuid end;
$$;

create or replace function public.visible_principal(p_org uuid, p_principal text)
returns text language sql stable security invoker set search_path = public, pg_temp as $$
  select case
    when p_principal is null then null
    -- An operator or the platform key reads the real principal (R49: operators see
    -- real principals on ordinary entries).
    when public.is_operator() or public.is_service_client() then p_principal
    -- A customer session reads a principal only when it is one of their own members.
    when exists (select 1 from public.org_members m
                 where m.org_id = p_org
                   and m.user_id = public.principal_uuid(p_principal)) then p_principal
    else 'platform' end;
$$;
revoke all on function public.principal_uuid(text) from public;
revoke all on function public.visible_principal(uuid, text) from public;
grant execute on function public.principal_uuid(text) to authenticated, service_role;
grant execute on function public.visible_principal(uuid, text)
  to authenticated, service_role;

-- ========================================= columns the console reads and D writes ===

-- Per-key trace opt-in. A trace-mode change is a consent decision and belongs in
-- `consent_history` through the settings service, never in a row update - so 0004
-- grants INSERT and UPDATE on this table column by column (ruling 5): a browser role
-- can write `name`, `revoked_at` and the five key-creation columns, and `trace_mode`
-- takes its default. A table-level INSERT was the hole B4 found: `insert into
-- api_keys(..., trace_mode) values (..., 'full')` set the consent with no history row.
alter table public.api_keys
  add column if not exists trace_mode text;
alter table public.api_keys
  add constraint api_keys_trace_mode_check
    check (trace_mode is null or trace_mode in ('off','minimal','full'));

-- The two usage facts the console shows that 0003 did not carry. `outcome` is 06's
-- name for what C1 calls `terminal_cause`; the numeric `status` stays untouched.
alter table public.usage_events
  add column if not exists execution_mode text,
  add column if not exists trace_mode text;
alter table public.usage_events
  add constraint usage_events_execution_mode_check
    check (execution_mode is null or execution_mode in ('sync','stream','async')),
  add constraint usage_events_trace_mode_check
    check (trace_mode is null or trace_mode in ('off','minimal','full'));

-- What the judge list shows beyond the run's own state. D6 writes them.
alter table infrx.judge_runs
  add column if not exists mode text not null default 'dry_run',
  add column if not exists judge_model_version text,
  add column if not exists limited_evaluation_count int not null default 0,
  add column if not exists quarantine_reason text;
alter table infrx.judge_runs
  add constraint judge_runs_mode_check check (mode in ('dry_run','live')),
  add constraint judge_runs_limited_count_check check (limited_evaluation_count >= 0),
  -- 02: a quarantined run records why, and nothing else claims to be quarantined.
  add constraint judge_runs_quarantine_reason_check
    check ((state = 'quarantined') = (quarantine_reason is not null));

-- ================================================================ read surface ===

-- Money crosses this boundary as TEXT, not numeric (ruling 10). PostgREST renders a
-- numeric column unquoted, so `JSON.parse` in the browser turns `0.00000001` into a
-- double and `Money` stops being exact; `numeric(20,8)::text` always carries its eight
-- fractional digits, which is the `Money` the contracts freeze (08 §4). Timestamps stay
-- `timestamptz` with microsecond precision, which PostgREST renders as
-- `2026-09-21T06:31:26.123456+00:00` - half of C's keyset cursor, so it must not be
-- reduced to a string here.
--
-- Every view below is `security_barrier` (ruling 6 / B5): they are owner's-rights views
-- over `infrx`, and without the barrier a caller's own function in the WHERE clause is
-- cheap enough to be evaluated BEFORE the tenant predicate - the reviewer's `cost 1e-7`
-- plpgsql probe printed every organization's wallet that way. The barrier keeps
-- leakproof operators (`org_id = $1`, `created_at < $2`) pushable, so the index plans
-- hold.

-- wallet_summary: `06`'s derived `available` included, so no caller subtracts.
create or replace view public.wallets with (security_barrier = true) as
select w.org_id, w.ledger_total::text as ledger_total,
       w.reserved_total::text as reserved_total, w.available::text as available,
       w.revision, w.updated_at
from infrx.wallets w
where public.is_org_member(w.org_id) or public.is_operator() or public.is_service_client();

-- ledger_page. `actor` is masked by PROVEN MEMBERSHIP (ruling 1), not by a marker.
create or replace view public.console_ledger with (security_barrier = true) as
select l.id, l.org_id, l.created_at, l.delta_usd::text as delta, l.kind, l.reason, l.ref,
       public.visible_principal(l.org_id,
         coalesce(p.email, l.created_by::text)) as actor,
       l.by_operator
from public.credit_ledger l
left join public.profiles p on p.id = l.created_by
where public.is_org_member(l.org_id) or public.is_operator() or public.is_service_client();

-- usage_page / usage_summary / usage_daily, as one relation: C1's three-way join
-- reached `infrx.credit_holds`, which no browser role may see. `key_id` and `key_name`
-- are both NULL exactly when the key is gone (LEFT JOIN, and the console renders
-- "(deleted key)"), which is the truth rather than a fabricated id.
create or replace view public.console_usage with (security_barrier = true) as
select e.id as request_id, e.org_id, e.created_at, e.model_id as model,
       k.id as key_id, k.name as key_name, e.execution_mode, e.job_state,
       e.outcome as terminal_cause, e.status as http_status, e.prompt_tokens,
       e.completion_tokens, e.usage_certainty, e.settlement_state, e.settlement_regime,
       e.cost_usd::text as cost, h.amount::text as max_hold, e.trace_mode, e.price_version
from public.usage_events e
left join public.api_keys k on k.id = e.api_key_id
left join infrx.credit_holds h on h.request_id = e.id and h.state in ('held','unknown')
where public.is_org_member(e.org_id) or public.is_operator() or public.is_service_client();

-- settings_get: the organization's current trace and evaluation settings, which are
-- the head of its consent history rather than a second mutable row that can disagree.
create or replace view public.org_settings with (security_barrier = true) as
select c.org_id, c.trace_mode, c.content_retention_days, c.evaluation_consent,
       c.consent_version as version, c.effective_at
from infrx.consent_history c
where c.revoked_at is null
  and (public.is_org_member(c.org_id) or public.is_operator() or public.is_service_client())
  and c.consent_version = (select max(h.consent_version) from infrx.consent_history h
                           where h.org_id = c.org_id and h.revoked_at is null);

-- consent_history: the audit trail, with the actor masked by the same rule.
create or replace view public.consent_history with (security_barrier = true) as
select c.org_id, c.consent_version as version, c.effective_at as changed_at,
       c.trace_mode, c.content_retention_days, c.evaluation_consent,
       public.visible_principal(c.org_id, c.actor_principal) as changed_by,
       c.by_operator, c.revoked_at
from infrx.consent_history c
where public.is_org_member(c.org_id) or public.is_operator() or public.is_service_client();

-- feedback_by_request. The value variants collapse to one jsonb column, which is what
-- `Feedback.value: boolean | number | string` is on the wire. Labels are excluded for
-- every viewer (R35/R49): `calibration_set` is therefore always false here, and it is
-- kept in the projection so a caller reading it cannot conclude the opposite.
create or replace view public.feedback with (security_barrier = true) as
select f.feedback_id as id, f.org_id, f.request_id, f.created_at, f.channel, f.author_role,
       public.visible_principal(f.org_id, f.author_principal) as author_principal,
       f.name,
       coalesce(to_jsonb(f.value_bool), to_jsonb(f.value_int), to_jsonb(f.value_text)) as value,
       f.comment, f.calibration_set, f.rubric_version, f.by_operator
from infrx.feedback f
where not f.calibration_set
  and (public.is_org_member(f.org_id) or public.is_operator() or public.is_service_client());

-- calibration.list (R19/R35): operator data, and the only view that shows a label.
create or replace view public.calibration_labels with (security_barrier = true) as
select f.feedback_id as id, f.org_id, f.request_id, f.created_at, f.author_principal,
       f.value_text as label, f.rubric_version, f.comment
from infrx.feedback f
where f.calibration_set and (public.is_operator() or public.is_service_client());

-- judge_runs_page. R13: owner and operator only. `samples` is capped and newest first
-- (ruling 10): a run with ten thousand samples must not turn one page of a list into a
-- multi-megabyte document.
create or replace view public.console_judge_runs with (security_barrier = true) as
select r.run_id as id, r.org_id, r.created_at, r.state, r.mode, r.rubric_version,
       r.model_revision as judge_model, r.judge_model_version,
       coalesce(s.sample_count, 0) as sample_count, r.limited_evaluation_count,
       r.reserved_cost::text as budget_reserved, r.actual_cost::text as budget_settled,
       c.effective_at as consent_snapshot_at, r.external_batch_id, r.quarantine_reason,
       coalesce(s.samples, '[]'::jsonb) as samples
from infrx.judge_runs r
left join infrx.consent_history c
  on c.org_id = r.org_id and c.consent_version = r.consent_version
left join lateral (
  select count(*) over () as sample_count, capped.samples
  from (select jsonb_agg(jsonb_build_object('sample_id', j.sample_id,
                                            'rubric_version', j.rubric_version,
                                            'request_id', j.request_id,
                                            'scores', j.scores)) as samples
        from (select * from infrx.judge_samples j0 where j0.run_id = r.run_id
              order by j0.sample_id desc limit 50) j) capped
) s on true
where public.is_org_owner(r.org_id) or public.is_operator() or public.is_service_client();

-- admin_orgs_page. Operator only, deliberately untenanted (R26).
--
-- B1: this view used `infrx.now()`, whose EXECUTE is revoked from `authenticated` (and
-- must stay revoked - it is the store's clock). A function in a view body is checked
-- against the CALLER, so an operator session got `42501 permission denied for function
-- now` instead of rows, and a non-operator got the same error instead of an empty
-- result. The 30-day activity window is a reporting window over `created_at`: wall
-- clock is the honest source for it and nothing durable depends on it. One owner row per
-- organization, chosen deterministically, because a second owner used to duplicate the
-- organization and break C's keyset pagination.
create or replace view public.console_admin_orgs with (security_barrier = true) as
select o.id as org_id, o.name, owner.email as owner_email, o.created_at, o.suspended,
       o.suspension_reason, w.ledger_total::text as ledger_total,
       w.reserved_total::text as reserved_total,
       coalesce(u.requests_30d, 0) as requests_30d, e.model_ids,
       jsonb_strip_nulls(jsonb_build_object(
         'max_concurrent_requests', e.max_concurrent_requests,
         'max_requests_per_minute', e.max_requests_per_minute,
         'max_video_seconds', e.max_video_seconds)) as limits,
       e.updated_at as entitlements_updated_at, e.updated_by as entitlements_updated_by
from public.organizations o
left join infrx.wallets w on w.org_id = o.id
left join infrx.org_entitlements e on e.org_id = o.id
left join lateral (
  select owner_profile.email
  from public.org_members m
  join public.profiles owner_profile on owner_profile.id = m.user_id
  where m.org_id = o.id and m.role = 'owner'
  order by m.created_at, owner_profile.email
  limit 1) owner on true
left join lateral (
  select count(*) as requests_30d from public.usage_events ev
  where ev.org_id = o.id and ev.created_at >= now() - interval '30 days') u on true
where public.is_operator() or public.is_service_client();

-- admin_audit_page (R34). Operator only; immutable at the table.
create or replace view public.operator_audit with (security_barrier = true) as
select a.id, a.at, a.actor_principal, a.action, a.target_org_id, a.reason, a.before,
       a.after, a.idempotency_key
from infrx.audit_entries a
where public.is_operator() or public.is_service_client();

grant select on public.wallets, public.console_ledger, public.console_usage,
                public.org_settings, public.consent_history, public.feedback,
                public.calibration_labels, public.console_judge_runs,
                public.console_admin_orgs, public.operator_audit
  to authenticated, service_role;
-- A view is read-only here whatever PostgREST offers: a simple updatable view would
-- otherwise let a browser role write straight through it into `infrx` - an owner ran
-- `update public.wallets set ledger_total = 1000000` before this revoke existed, which
-- is the whole money invariant defeated by a missing line.
revoke insert, update, delete, truncate on public.wallets, public.console_ledger,
                public.console_usage, public.org_settings, public.consent_history,
                public.feedback, public.calibration_labels, public.console_judge_runs,
                public.console_admin_orgs, public.operator_audit
  from anon, authenticated;

-- ------------------------------------------------- the wallet summary function ---
-- The function `apps/app/lib/credits.ts` calls (falling back to `org_balance` until it
-- exists). SECURITY INVOKER with the same explicit guard as `org_balance`, so a
-- non-member gets a clear error instead of an empty result; it reads `public.wallets`,
-- whose owner's rights reach `infrx`.
--
-- r2 (ruling 10): every figure is TEXT. This supersedes the numeric(20,8) signature
-- agreed earlier - PostgREST would render those as JSON numbers and the browser would
-- parse them into doubles, which is exactly how a money column stops being exact.
create or replace function public.org_wallet_summary(p_org uuid)
returns table (
  ledger_total text,
  reserved_total text,
  loaded text,
  spent text)
language plpgsql stable security invoker set search_path = public, pg_temp as $$
begin
  if not (public.is_org_member(p_org) or public.is_operator()
          or public.is_service_client()) then
    raise exception 'not a member of organization %', p_org using errcode = '42501';
  end if;

  return query
  select coalesce(w.ledger_total, 0::numeric(20,8))::text,
         coalesce(w.reserved_total, 0::numeric(20,8))::text,
         coalesce(l.loaded, 0)::numeric(20,8)::text,
         coalesce(l.spent, 0)::numeric(20,8)::text
  from (select 1) one
  left join (select org_id, ledger_total::numeric(20,8) as ledger_total,
                    reserved_total::numeric(20,8) as reserved_total
             from public.wallets) w on w.org_id = p_org
  left join (select sum(delta_usd) filter (where delta_usd > 0) as loaded,
                    -sum(delta_usd) filter (where delta_usd < 0) as spent
             from public.credit_ledger where org_id = p_org) l on true;
end $$;

grant execute on function public.org_wallet_summary(uuid) to authenticated, service_role;

comment on function public.org_wallet_summary(uuid) is
  'Wallet summary for one organization: the stored totals plus the loaded/spent split '
  'apps/app/lib/credits.ts renders. Money as text (ruling 10). Guarded like org_balance.';

-- ------------------------------------------- the two aggregates C cannot express ---
-- Ruling 10: PostgREST cannot aggregate over a view, so the usage summary and the daily
-- buckets are RPCs. Both are SECURITY INVOKER over `public.console_usage`, so the
-- view's tenant predicate applies to the caller as well as the explicit guard below -
-- two independent reasons a cross-tenant call returns nothing, and the guard turns the
-- second one into an error instead of an empty page. Money is text, as everywhere.
create or replace function public.console_usage_summary(
  p_org uuid,
  p_from timestamptz,
  p_to timestamptz,
  p_model text default null,
  p_key uuid default null)
returns table (
  requests bigint,
  failed_requests bigint,
  prompt_tokens bigint,
  completion_tokens bigint,
  cost text,
  pending_reconciliation text,
  platform_absorbed_requests bigint)
language plpgsql stable security invoker set search_path = public, pg_temp as $$
begin
  if not (public.is_org_member(p_org) or public.is_operator()
          or public.is_service_client()) then
    raise exception 'not a member of organization %', p_org using errcode = '42501';
  end if;
  return query
  select count(*)::bigint,
         count(*) filter (where u.http_status >= 400)::bigint,
         coalesce(sum(u.prompt_tokens), 0)::bigint,
         coalesce(sum(u.completion_tokens), 0)::bigint,
         coalesce(sum(u.cost::numeric(20,8)), 0)::numeric(20,8)::text,
         -- What an unknown-usage hold is still reserving: the figure a customer needs to
         -- understand why their available balance is lower than their total (02).
         coalesce(sum(u.max_hold::numeric(20,8))
                  filter (where u.usage_certainty = 'unknown'
                          and u.max_hold is not null), 0)::numeric(20,8)::text,
         count(*) filter (where u.settlement_state = 'released_platform_absorbed')::bigint
  from public.console_usage u
  where u.org_id = p_org
    and u.created_at >= p_from and u.created_at <= p_to
    and (p_model is null or u.model = p_model)
    and (p_key is null or u.key_id = p_key);
end $$;

-- UTC date buckets, newest first, hard-bounded: a caller asking for ten years gets the
-- most recent 400 days rather than a statement that never returns.
create or replace function public.console_usage_daily(
  p_org uuid,
  p_from timestamptz,
  p_to timestamptz,
  p_model text default null,
  p_key uuid default null)
returns table (
  day date,
  requests bigint,
  prompt_tokens bigint,
  completion_tokens bigint,
  cost text)
language plpgsql stable security invoker set search_path = public, pg_temp as $$
begin
  if not (public.is_org_member(p_org) or public.is_operator()
          or public.is_service_client()) then
    raise exception 'not a member of organization %', p_org using errcode = '42501';
  end if;
  return query
  select (u.created_at at time zone 'utc')::date as day,
         count(*)::bigint,
         coalesce(sum(u.prompt_tokens), 0)::bigint,
         coalesce(sum(u.completion_tokens), 0)::bigint,
         coalesce(sum(u.cost::numeric(20,8)), 0)::numeric(20,8)::text
  from public.console_usage u
  where u.org_id = p_org
    and u.created_at >= p_from and u.created_at <= p_to
    and (p_model is null or u.model = p_model)
    and (p_key is null or u.key_id = p_key)
  group by 1
  order by 1 desc
  limit 400;
end $$;

grant execute on function public.console_usage_summary(uuid, timestamptz, timestamptz,
                                                       text, uuid)
  to authenticated, service_role;
grant execute on function public.console_usage_daily(uuid, timestamptz, timestamptz,
                                                     text, uuid)
  to authenticated, service_role;
