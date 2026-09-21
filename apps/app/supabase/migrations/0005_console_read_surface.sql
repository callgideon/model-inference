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

-- ========================================= columns the console reads and D writes ===

-- Per-key trace opt-in. `api_keys` had a broad owner UPDATE grant (0001's
-- `api_keys_update_owner` covers every column), so the grant is narrowed to the two
-- columns the console actually writes: a trace-mode change is a consent decision and
-- belongs in `consent_history` through the settings service, not in a row update.
alter table public.api_keys
  add column if not exists trace_mode text;
alter table public.api_keys
  add constraint api_keys_trace_mode_check
    check (trace_mode is null or trace_mode in ('off','minimal','full'));
revoke update on public.api_keys from anon, authenticated;
grant update (name, revoked_at) on public.api_keys to authenticated;

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

-- wallet_summary: `06`'s derived `available` included, so no caller subtracts.
create or replace view public.wallets as
select w.org_id, w.ledger_total, w.reserved_total, w.available, w.revision, w.updated_at
from infrx.wallets w
where public.is_org_member(w.org_id) or public.is_operator() or public.is_service_client();

-- ledger_page. `actor` is projected per viewer (R41/R50): a customer reads `platform`
-- for anything the platform did, an operator reads the principal.
create or replace view public.console_ledger as
select l.id, l.org_id, l.created_at, l.delta_usd as delta, l.kind, l.reason, l.ref,
       case when l.by_operator and not (public.is_operator() or public.is_service_client())
            then 'platform'
            else coalesce(l.operator_principal, p.email, l.created_by::text) end as actor,
       l.by_operator
from public.credit_ledger l
left join public.profiles p on p.id = l.created_by
where public.is_org_member(l.org_id) or public.is_operator() or public.is_service_client();

-- usage_page / usage_summary / usage_daily, as one relation: C1's three-way join
-- reached `infrx.credit_holds`, which no browser role may see.
create or replace view public.console_usage as
select e.id as request_id, e.org_id, e.created_at, e.model_id as model,
       e.api_key_id as key_id, k.name as key_name, e.execution_mode, e.job_state,
       e.outcome as terminal_cause, e.status as http_status, e.prompt_tokens,
       e.completion_tokens, e.usage_certainty, e.settlement_state, e.settlement_regime,
       e.cost_usd as cost, h.amount as max_hold, e.trace_mode, e.price_version
from public.usage_events e
left join public.api_keys k on k.id = e.api_key_id
left join infrx.credit_holds h on h.request_id = e.id and h.state in ('held','unknown')
where public.is_org_member(e.org_id) or public.is_operator() or public.is_service_client();

-- settings_get: the organization's current trace and evaluation settings, which are
-- the head of its consent history rather than a second mutable row that can disagree.
create or replace view public.org_settings as
select c.org_id, c.trace_mode, c.content_retention_days, c.evaluation_consent,
       c.consent_version as version, c.effective_at
from infrx.consent_history c
where c.revoked_at is null
  and (public.is_org_member(c.org_id) or public.is_operator() or public.is_service_client())
  and c.consent_version = (select max(h.consent_version) from infrx.consent_history h
                           where h.org_id = c.org_id and h.revoked_at is null);

-- consent_history: the audit trail, with the actor masked like every other principal.
create or replace view public.consent_history as
select c.org_id, c.consent_version as version, c.effective_at as changed_at,
       c.trace_mode, c.content_retention_days, c.evaluation_consent,
       case when c.by_operator and not (public.is_operator() or public.is_service_client())
            then 'platform' else c.actor_principal end as changed_by,
       c.by_operator, c.revoked_at
from infrx.consent_history c
where public.is_org_member(c.org_id) or public.is_operator() or public.is_service_client();

-- feedback_by_request. The value variants collapse to one jsonb column, which is what
-- `Feedback.value: boolean | number | string` is on the wire. Labels are excluded for
-- every viewer (R35/R49): `calibration_set` is therefore always false here, and it is
-- kept in the projection so a caller reading it cannot conclude the opposite.
create or replace view public.feedback as
select f.feedback_id as id, f.org_id, f.request_id, f.created_at, f.channel, f.author_role,
       case when f.by_operator and not (public.is_operator() or public.is_service_client())
            then 'platform' else f.author_principal end as author_principal,
       f.name,
       coalesce(to_jsonb(f.value_bool), to_jsonb(f.value_int), to_jsonb(f.value_text)) as value,
       f.comment, f.calibration_set, f.rubric_version, f.by_operator
from infrx.feedback f
where not f.calibration_set
  and (public.is_org_member(f.org_id) or public.is_operator() or public.is_service_client());

-- calibration.list (R19/R35): operator data, and the only view that shows a label.
create or replace view public.calibration_labels as
select f.feedback_id as id, f.org_id, f.request_id, f.created_at, f.author_principal,
       f.value_text as label, f.rubric_version, f.comment
from infrx.feedback f
where f.calibration_set and (public.is_operator() or public.is_service_client());

-- judge_runs_page. R13: owner and operator only.
create or replace view public.console_judge_runs as
select r.run_id as id, r.org_id, r.created_at, r.state, r.mode, r.rubric_version,
       r.model_revision as judge_model, r.judge_model_version,
       coalesce(s.sample_count, 0) as sample_count, r.limited_evaluation_count,
       r.reserved_cost as budget_reserved, r.actual_cost as budget_settled,
       c.effective_at as consent_snapshot_at, r.external_batch_id, r.quarantine_reason,
       coalesce(s.samples, '[]'::jsonb) as samples
from infrx.judge_runs r
left join infrx.consent_history c
  on c.org_id = r.org_id and c.consent_version = r.consent_version
left join (select j.run_id, count(*) as sample_count,
                  jsonb_agg(jsonb_build_object('sample_id', j.sample_id,
                                               'rubric_version', j.rubric_version,
                                               'request_id', j.request_id,
                                               'scores', j.scores)
                            order by j.sample_id) as samples
           from infrx.judge_samples j group by j.run_id) s on s.run_id = r.run_id
where public.is_org_owner(r.org_id) or public.is_operator() or public.is_service_client();

-- admin_orgs_page. Operator only, deliberately untenanted (R26).
create or replace view public.console_admin_orgs as
select o.id as org_id, o.name, owner.email as owner_email, o.created_at, o.suspended,
       o.suspension_reason, w.ledger_total, w.reserved_total,
       coalesce(u.requests_30d, 0) as requests_30d, e.model_ids,
       jsonb_strip_nulls(jsonb_build_object(
         'max_concurrent_requests', e.max_concurrent_requests,
         'max_requests_per_minute', e.max_requests_per_minute,
         'max_video_seconds', e.max_video_seconds)) as limits,
       e.updated_at as entitlements_updated_at, e.updated_by as entitlements_updated_by
from public.organizations o
left join infrx.wallets w on w.org_id = o.id
left join infrx.org_entitlements e on e.org_id = o.id
left join (select m.org_id, owner_profile.email
           from public.org_members m
           join public.profiles owner_profile on owner_profile.id = m.user_id
           where m.role = 'owner') owner on owner.org_id = o.id
left join (select ev.org_id, count(*) as requests_30d from public.usage_events ev
           where ev.created_at >= infrx.now() - interval '30 days'
           group by ev.org_id) u on u.org_id = o.id
where public.is_operator() or public.is_service_client();

-- admin_audit_page (R34). Operator only; immutable at the table.
create or replace view public.operator_audit as
select a.id, a.at, a.actor_principal, a.action, a.target_org_id, a.reason, a.before,
       a.after, a.idempotency_key
from infrx.audit_entries a
where public.is_operator() or public.is_service_client();

grant select on public.wallets, public.console_ledger, public.console_usage,
                public.org_settings, public.consent_history, public.feedback,
                public.calibration_labels, public.console_judge_runs,
                public.console_admin_orgs, public.operator_audit
  to authenticated, service_role;
-- A view is read-only here whatever PostgREST offers: an updatable simple view would
-- otherwise let a browser role write through it into `infrx`.
revoke insert, update, delete on public.wallets, public.console_ledger,
                public.console_usage, public.org_settings, public.consent_history,
                public.feedback, public.calibration_labels, public.console_judge_runs,
                public.console_admin_orgs, public.operator_audit
  from anon, authenticated;

-- ------------------------------------------------- the wallet summary function ---
-- The signature C1 asked for, and the one `apps/app/lib/credits.ts` already calls
-- (falling back to `org_balance` until it exists). SECURITY INVOKER with the same
-- explicit guard as `org_balance`, so "empty result" is a clear error instead: it
-- reads `public.wallets`, whose owner's rights reach `infrx`.
create or replace function public.org_wallet_summary(p_org uuid)
returns table (
  ledger_total numeric(20,8),
  reserved_total numeric(20,8),
  loaded numeric(20,8),
  spent numeric(20,8))
language plpgsql stable security invoker set search_path = public, pg_temp as $$
begin
  if not (public.is_org_member(p_org) or public.is_operator()
          or public.is_service_client()) then
    raise exception 'not a member of organization %', p_org using errcode = '42501';
  end if;

  return query
  select coalesce(w.ledger_total, 0)::numeric(20,8),
         coalesce(w.reserved_total, 0)::numeric(20,8),
         coalesce(l.loaded, 0)::numeric(20,8),
         coalesce(l.spent, 0)::numeric(20,8)
  from (select 1) one
  left join public.wallets w on w.org_id = p_org
  left join (select sum(delta_usd) filter (where delta_usd > 0) as loaded,
                    -sum(delta_usd) filter (where delta_usd < 0) as spent
             from public.credit_ledger where org_id = p_org) l on true;
end $$;

grant execute on function public.org_wallet_summary(uuid) to authenticated, service_role;

comment on function public.org_wallet_summary(uuid) is
  'Wallet summary for one organization: the stored totals plus the loaded/spent split '
  'apps/app/lib/credits.ts renders. Guarded like org_balance.';
