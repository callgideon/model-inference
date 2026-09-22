-- D1R: admission-pin resolution and the CREDIT read surface (research/plan/11 §D1R item 4).
--
-- The same rules as 0005: money leaves SQL as TEXT (R59-9), every customer-readable view
-- is `security_barrier` and carries its own tenant predicate (R59-5), operator principals
-- are masked by proven membership (R59-1), and every new public object has Supabase's
-- default ACL revoked BEFORE the intended grant (R59-4). `infrx` stays hidden from
-- PostgREST (db-schemas = public); the platform role reaches pin resolution and the grant
-- through narrow `infrx` functions only. Units are never combined (R73): the CREDIT
-- wallet summary and the legacy USD statement are separate calls with separate units.
-- Re-runnable (create or replace).

-- ======================================================= admission-pin resolution ===
-- `model -> deployment_revision -> serving_version + rate_card_version + policy_version`
-- (07), resolved once at admission and frozen on the job (D2 writes them; R78 keeps them).
-- `p_model` is the alias (`nemostation/marlin-2b`) or an R62 pin
-- (`nemostation/marlin-2b@2026-09-01`). The current listing is the highest effective
-- version; a pin takes the highest effective version whose serving revision carries that
-- label. Refusals do not confirm what exists (R70):
--   not_found (P0002)       unknown alias or label, a private/dev artifact (never listed),
--                           a deployment that is not active (retired, draining, proposed)
--   invalid_request (22023) the listed card is not yet effective: unpriced is unserveable
--                           (R69), never free
--   maintenance (55000)     CREDIT admission is not enabled, or no data-access policy is
--                           in force
create or replace function infrx.resolve_admission_pins(p_model text)
returns table (model_id uuid, requested_model text, deployment_revision_id uuid,
               serving_version_id uuid, rate_card_version text, policy_version text,
               accounting_regime text, model_revision text, max_input_tokens int,
               max_output_tokens int, input_rate_per_million text,
               output_rate_per_million text, provisional boolean)
language plpgsql stable security definer set search_path = infrx, public, pg_temp as $$
#variable_conflict use_column
declare
  v_alias text;
  v_label text;
  v_now timestamptz := infrx.now();
  l infrx.catalog_listings%rowtype;
  d infrx.deployment_revisions%rowtype;
  c infrx.rate_card_versions%rowtype;
  v_policy text;
  v_revision text;
begin
  perform infrx.require_feature('credit_admission');
  if p_model is null or p_model !~ '^[^@\s]{1,200}(@[^@\s]{1,64})?$' then
    raise exception 'not_found: model' using errcode = 'P0002';
  end if;
  v_alias := split_part(p_model, '@', 1);
  v_label := nullif(split_part(p_model, '@', 2), '');
  select cl.* into l from infrx.catalog_listings cl
    join infrx.serving_versions s on s.serving_version_id = cl.serving_version_id
   where cl.public_model_id = v_alias and cl.effective_at <= v_now
     and (v_label is null or s.revision_label = v_label)
   order by cl.version desc limit 1;
  if not found then
    raise exception 'not_found: model' using errcode = 'P0002';
  end if;
  select * into d from infrx.deployment_revisions dr
   where dr.deployment_revision_id = l.deployment_revision_id;
  if d.state <> 'active' or d.visibility <> 'public' then
    raise exception 'not_found: model' using errcode = 'P0002';
  end if;
  select * into c from infrx.rate_card_versions rc
   where rc.rate_card_version = l.rate_card_version;
  if c.effective_at > v_now then
    raise exception 'invalid_request: model % has no active rate card', v_alias
      using errcode = '22023';
  end if;
  select p.policy_version into v_policy from infrx.data_access_policies p
   where p.effective_at <= v_now order by p.effective_at desc, p.policy_version desc limit 1;
  if v_policy is null then
    raise exception 'maintenance: no data-access policy is in force' using errcode = '55000';
  end if;
  select s.revision_label into v_revision from infrx.serving_versions s
   where s.serving_version_id = l.serving_version_id;
  return query select l.model_id, p_model, l.deployment_revision_id, l.serving_version_id,
    l.rate_card_version, v_policy, 'credit'::text, v_alias || '@' || v_revision,
    d.max_input_tokens, d.max_output_tokens, c.input_rate_per_million::text,
    c.output_rate_per_million::text, c.provisional;
end $$;
revoke all on function infrx.resolve_admission_pins(text) from public, anon, authenticated;
grant execute on function infrx.resolve_admission_pins(text) to service_role;
comment on function infrx.resolve_admission_pins(text) is
  'D2 seam (D1R): the admission pins for a public model alias or R62 pin. service_role only.';

-- ===================================================== CREDIT wallet read surface ===
-- An individual's wallet is theirs: other members of their personal organization do not
-- see it (02: never silently give other members access to a personal wallet). A
-- provider_dev wallet is read by the platform only until Lab's server reads it.
create or replace view public.console_credit_wallets with (security_barrier = true) as
select w.wallet_id, w.kind, w.unit, w.owner_user_id, w.personal_org_id as org_id,
       w.owner_provider_org_id, w.ledger_total::text as ledger_total,
       w.reserved_total::text as reserved_total, w.available::text as available,
       w.revision, w.updated_at, e.granted_at as signup_granted_at
from infrx.credit_wallets w
left join infrx.signup_entitlements e on e.wallet_id = w.wallet_id
where (w.kind = 'consumer' and w.owner_user_id = auth.uid())
   or public.is_operator() or public.is_service_client();

-- The CREDIT ledger page. `actor` is masked by proven membership of the wallet's personal
-- organization (R59-1): `platform` for a signup grant and for any operator entry.
create or replace view public.console_credit_ledger with (security_barrier = true) as
select l.entry_id, l.wallet_id, w.personal_org_id as org_id, l.created_at, l.kind,
       l.amount::text as amount, l.unit, l.request_id, l.reason,
       public.visible_principal(w.personal_org_id, l.actor) as actor
from infrx.credit_ledger l
join infrx.credit_wallets w on w.wallet_id = l.wallet_id
where (w.kind = 'consumer' and w.owner_user_id = auth.uid())
   or public.is_operator() or public.is_service_client();

-- The legacy usage view gains the CREDIT columns, APPENDED (every 0005 column keeps its
-- name, type and position, so the deployed console's select lists still execute). A row
-- states its regime; `cost` stays the historical USD figure and `charged_credits` the
-- CREDIT one - side by side, never combined.
create or replace view public.console_usage with (security_barrier = true) as
select e.id as request_id, e.org_id, e.created_at, e.model_id as model,
       k.id as key_id, k.name as key_name, e.execution_mode, e.job_state,
       e.outcome as terminal_cause, e.status as http_status, e.prompt_tokens,
       e.completion_tokens, e.usage_certainty, e.settlement_state, e.settlement_regime,
       e.cost_usd::text as cost, h.amount::text as max_hold, e.trace_mode, e.price_version,
       e.accounting_regime, e.charged_credits::text as charged_credits,
       e.rate_card_version, e.serving_version_id, e.deployment_revision_id,
       ch.amount::text as credit_hold
from public.usage_events e
left join public.api_keys k on k.id = e.api_key_id and k.org_id = e.org_id
left join infrx.credit_holds h on h.request_id = e.id and h.state in ('held','unknown')
left join infrx.credit_wallet_holds ch
  on ch.request_id = e.id and ch.state in ('held','unknown')
where public.is_org_member(e.org_id) or public.is_operator() or public.is_service_client();

-- The individual's CREDIT balance: exactly one row. No wallet yet (not granted) is a row
-- with `wallet_id` NULL and zero amounts - the truth, stated explicitly, never a guess.
-- SECURITY INVOKER over the barrier view, with the explicit guard of `org_balance`, so
-- another user's id is a 42501 rather than an empty answer.
create or replace function public.console_wallet_summary(p_user uuid)
returns table (user_id uuid, wallet_id uuid, kind text, unit text, ledger_total text,
               reserved_total text, available text, revision bigint,
               signup_granted_at timestamptz)
language plpgsql stable security invoker set search_path = public, pg_temp as $$
#variable_conflict use_column
begin
  if not (p_user = auth.uid() or public.is_operator() or public.is_service_client()) then
    raise exception 'not this user''s wallet' using errcode = '42501';
  end if;
  return query
  select p_user, w.wallet_id, 'consumer'::text, 'CREDIT'::text,
         coalesce(w.ledger_total, '0.00000000'), coalesce(w.reserved_total, '0.00000000'),
         coalesce(w.available, '0.00000000'), coalesce(w.revision, 0::bigint),
         w.signup_granted_at
  from (select 1) one
  left join public.console_credit_wallets w
    on w.owner_user_id = p_user and w.kind = 'consumer';
end $$;

-- The legacy USD statement (06a LegacyUsdStatement): historical `credit_ledger` USD, its
-- own unit, and `rollout_hold` whenever the balance is nonzero - no product decision
-- exists to convert or discard it (R72). Never summed with CREDIT.
create or replace function public.console_legacy_usd_statement(p_org uuid)
returns table (org_id uuid, accounting_regime text, unit text, balance text,
               entry_count bigint, as_of timestamptz, rollout_hold boolean)
language plpgsql stable security invoker set search_path = public, pg_temp as $$
#variable_conflict use_column
begin
  if not (public.is_org_member(p_org) or public.is_operator()
          or public.is_service_client()) then
    raise exception 'not a member of organization %', p_org using errcode = '42501';
  end if;
  return query
  select p_org, 'legacy_usd'::text, 'USD'::text,
         coalesce(sum(l.delta_usd), 0)::numeric(20,8)::text, count(*)::bigint, now(),
         coalesce(sum(l.delta_usd), 0) <> 0
  from public.credit_ledger l where l.org_id = p_org;
end $$;

-- ================================================================ privileges ===
-- R59-4: Supabase's default ACL hands anon/authenticated ALL on a new public view and
-- EXECUTE on a new public function; revoke that first, then grant exactly the read.
revoke all on public.console_credit_wallets, public.console_credit_ledger
  from public, anon, authenticated;
grant select on public.console_credit_wallets, public.console_credit_ledger
  to authenticated, service_role;
-- `console_usage` keeps 0005's grants: `create or replace view` keeps the ACL.

revoke all on function public.console_wallet_summary(uuid) from public, anon, authenticated;
revoke all on function public.console_legacy_usd_statement(uuid)
  from public, anon, authenticated;
grant execute on function public.console_wallet_summary(uuid) to authenticated, service_role;
grant execute on function public.console_legacy_usd_statement(uuid)
  to authenticated, service_role;
