-- D1R: additive CREDIT accounting (research/plan/11 §D1R item 2; 06a; platforms/02-credits).
--
-- Additive only. No USD value is copied, converted or relabelled: `public.credit_ledger`,
-- `usage_events.cost_usd`, `infrx.wallets` and `infrx.credit_holds` stay the legacy USD
-- regime exactly as 0001-0005 left them, and no function here maps one unit to another
-- (R64/R65). The only changes to a 0001-0005 object are the ones
-- `tests/d/checks_credit.ALLOWED_LEGACY_CHANGES` names.
--
-- Names. The dispatch named `infrx.credit_wallets`, `infrx.credit_ledger`,
-- `infrx.credit_holds` and `infrx.signup_entitlements`; `infrx.credit_holds` is D1's USD
-- hold table, so the CREDIT holds are `infrx.credit_wallet_holds`. `infrx.credit_ledger`
-- is not `public.credit_ledger`: every reference in this schema is schema-qualified.
--
-- Re-runnable: every statement is `if not exists` / `create or replace` / guarded, so a
-- second application is a no-op (the D1R upgrade test applies it twice).
--
-- Feature enablement is separate from application (item 5): this file creates the flags
-- with CREDIT admission and the signup grant OFF. Until an operator enables them the
-- CREDIT paths refuse with SQLSTATE 55000 ("maintenance") - never an unmetered success.

-- ============================================================== feature flags ===
create table if not exists infrx.feature_flags (
  name text primary key
    check (name in ('signup_grant', 'credit_admission', 'legacy_usd_admission')),
  enabled boolean not null,
  updated_by text not null check (length(btrim(updated_by)) between 1 and 200),
  reason text not null check (length(btrim(reason)) between 1 and 500),
  updated_at timestamptz not null default infrx.now()
);
insert into infrx.feature_flags (name, enabled, updated_by, reason) values
  ('signup_grant', false, 'migration 0006', 'applied; not enabled'),
  ('credit_admission', false, 'migration 0006', 'applied; not enabled'),
  -- The pilot's own regime keeps admitting until the cutover turns it off (02 §3:
  -- "freeze new old-regime admission at cutover").
  ('legacy_usd_admission', true, 'migration 0006', 'pre-cutover default')
on conflict (name) do nothing;

-- A missing row is as closed as a disabled one.
create or replace function infrx.require_feature(p_name text) returns void
language plpgsql stable security definer set search_path = infrx, public, pg_temp as $$
begin
  if not coalesce((select f.enabled from infrx.feature_flags f where f.name = p_name), false)
  then
    raise exception 'maintenance: % is not enabled', p_name using errcode = '55000';
  end if;
end $$;

-- ============================================================= CREDIT wallets ===
-- Ownership is an exclusive-or fixed by kind (06a `wallets`): a consumer wallet belongs
-- to one individual and is billed through that individual's personal consumer
-- organization; a provider_dev wallet belongs to a provider organization (FK in 0007).
-- `on delete restrict`: a CREDIT wallet is money history, so deleting its owner is
-- refused rather than cascading the ledger away (identity retention policy: 02, A1).
create table if not exists infrx.credit_wallets (
  wallet_id uuid primary key default gen_random_uuid(),
  kind text not null check (kind in ('consumer', 'provider_dev')),
  unit text not null default 'CREDIT' check (unit = 'CREDIT'),
  owner_user_id uuid references public.profiles(id) on delete restrict,
  personal_org_id uuid references public.organizations(id) on delete restrict,
  owner_provider_org_id uuid,
  ledger_total numeric(20,8) not null default 0,
  reserved_total numeric(20,8) not null default 0,
  available numeric(20,8) generated always as (ledger_total - reserved_total) stored,
  revision bigint not null default 0 check (revision >= 0),
  created_at timestamptz not null default infrx.now(),
  updated_at timestamptz not null default infrx.now(),
  constraint credit_wallets_owner_matches_kind check (case kind
    when 'consumer' then owner_user_id is not null and personal_org_id is not null
                         and owner_provider_org_id is null
    else owner_provider_org_id is not null and owner_user_id is null
         and personal_org_id is null end),
  constraint credit_wallets_totals_nonnegative check (ledger_total >= 0 and reserved_total >= 0),
  -- The one constraint admission maps to 402 (insufficient credit): a hold that would
  -- reserve more than the total fails here, by name, in the hold's own transaction.
  constraint credit_wallets_reserved_within_total check (reserved_total <= ledger_total),
  constraint credit_wallets_kind_key unique (wallet_id, kind),
  constraint credit_wallets_owner_key unique (wallet_id, owner_user_id)
);
-- One initial wallet per individual (02: per individual user, not per organization), one
-- personal org funds at most one wallet, one dev wallet per provider organization.
create unique index if not exists credit_wallets_one_per_user
  on infrx.credit_wallets (owner_user_id) where kind = 'consumer';
create unique index if not exists credit_wallets_one_per_personal_org
  on infrx.credit_wallets (personal_org_id) where kind = 'consumer';
create unique index if not exists credit_wallets_one_per_provider
  on infrx.credit_wallets (owner_provider_org_id) where kind = 'provider_dev';

-- Kind, unit and owner binding never change; the totals move only through the ledger and
-- hold triggers below (R59-7) - `pg_trigger_depth() >= 2` is "called from one of them".
-- ponytail: depth, not an identity token; a future trigger that updates wallets directly
-- would pass - add a transaction-local token if one ever exists.
create or replace function infrx.credit_wallets_guard() returns trigger
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'CREDIT wallet % is never deleted', old.wallet_id using errcode = '23514';
  end if;
  if tg_op = 'INSERT' then
    if new.ledger_total <> 0 or new.reserved_total <> 0 or new.revision <> 0 then
      raise exception 'a CREDIT wallet starts at zero; money enters through the ledger'
        using errcode = '23514';
    end if;
    return new;
  end if;
  if new.wallet_id is distinct from old.wallet_id
     or new.kind is distinct from old.kind
     or new.unit is distinct from old.unit
     or new.owner_user_id is distinct from old.owner_user_id
     or new.personal_org_id is distinct from old.personal_org_id
     or new.owner_provider_org_id is distinct from old.owner_provider_org_id
     or new.created_at is distinct from old.created_at then
    raise exception 'CREDIT wallet %: kind, unit and owner binding are immutable',
      old.wallet_id using errcode = '23514';
  end if;
  if pg_trigger_depth() < 2 and (new.ledger_total is distinct from old.ledger_total
                                 or new.reserved_total is distinct from old.reserved_total
                                 or new.revision is distinct from old.revision) then
    raise exception 'CREDIT wallet %: totals move only by ledger and hold rows',
      old.wallet_id using errcode = '23514';
  end if;
  new.updated_at := infrx.now();
  return new;
end $$;
create or replace trigger credit_wallets_guard before insert or update or delete
  on infrx.credit_wallets for each row execute function infrx.credit_wallets_guard();

-- ============================================================= CREDIT ledger ===
-- LedgerEntryKind is closed and has no transfer (R67). `wallet_kind` rides along so the
-- kind rules are a CHECK, and the composite key makes it the wallet's real kind.
create table if not exists infrx.credit_ledger (
  entry_id uuid primary key default gen_random_uuid(),
  wallet_id uuid not null,
  wallet_kind text not null,
  kind text not null check (kind in ('signup_grant', 'operator_allocation',
                                     'operator_adjustment', 'inference_debit')),
  amount numeric(20,8) not null check (amount <> 0),
  unit text not null default 'CREDIT' check (unit = 'CREDIT'),
  operation_id uuid not null,
  request_id uuid,
  -- Who produced it (an operator principal, `platform`, a settling service). Stored in
  -- `infrx`, which no customer can read (R59-2); the console view masks it.
  actor text not null check (length(btrim(actor)) between 1 and 200),
  reason text not null default '' check (length(reason) <= 500),
  created_at timestamptz not null default infrx.now(),
  constraint credit_ledger_operation_key unique (operation_id),
  constraint credit_ledger_signup_target_key unique (operation_id, wallet_id, kind),
  constraint credit_ledger_wallet_kind_fk foreign key (wallet_id, wallet_kind)
    references infrx.credit_wallets (wallet_id, kind) on delete restrict,
  constraint credit_ledger_kind_rules check (case kind
    when 'signup_grant' then wallet_kind = 'consumer' and amount = 10000.00000000
    when 'operator_allocation' then wallet_kind = 'provider_dev' and amount > 0
    when 'inference_debit' then amount < 0 and request_id is not null
    else true end),
  constraint credit_ledger_request_only_on_debits
    check (kind = 'inference_debit' or request_id is null)
);
-- A second signup grant into the same wallet is refused even without its entitlement row.
create unique index if not exists credit_ledger_one_signup_grant_per_wallet
  on infrx.credit_ledger (wallet_id) where kind = 'signup_grant';
-- At most one settlement debit per request.
create unique index if not exists credit_ledger_one_debit_per_request
  on infrx.credit_ledger (request_id) where kind = 'inference_debit';
create index if not exists credit_ledger_wallet_created_idx
  on infrx.credit_ledger (wallet_id, created_at desc, entry_id desc);

create or replace trigger credit_ledger_append_only before update or delete
  on infrx.credit_ledger for each row execute function infrx.forbid_update_delete();

-- R59-7: the wallet total moves only here, in the ledger row's transaction, for every
-- writer. A debit past zero fails `credit_wallets_totals_nonnegative`.
create or replace function infrx.credit_ledger_moves_wallet() returns trigger
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
begin
  update infrx.credit_wallets
     set ledger_total = ledger_total + new.amount, revision = revision + 1
   where wallet_id = new.wallet_id;
  return null;
end $$;
create or replace trigger credit_ledger_moves_wallet after insert on infrx.credit_ledger
  for each row execute function infrx.credit_ledger_moves_wallet();

-- ======================================================= signup entitlements ===
-- 02 / R71: UNIQUE (user_id, initial_signup_grant) and nothing else in the key; the
-- campaign is an ordinary column. The grant lands in the user's OWN wallet (composite key
-- on the wallet's owner) and its money is the signup ledger row of that wallet.
create table if not exists infrx.signup_entitlements (
  user_id uuid not null references public.profiles(id) on delete restrict,
  entitlement text not null check (entitlement = 'initial_signup_grant'),
  wallet_id uuid not null,
  amount numeric(20,8) not null check (amount = 10000.00000000),
  verification_evidence_ref text not null
    check (length(btrim(verification_evidence_ref)) between 1 and 500),
  ledger_operation_id uuid not null,
  ledger_kind text not null default 'signup_grant' check (ledger_kind = 'signup_grant'),
  campaign_version text not null default '' check (length(campaign_version) <= 100),
  granted_at timestamptz not null default infrx.now(),
  constraint signup_entitlements_pkey primary key (user_id, entitlement),
  constraint signup_entitlements_operation_key unique (ledger_operation_id),
  constraint signup_entitlements_own_wallet foreign key (wallet_id, user_id)
    references infrx.credit_wallets (wallet_id, owner_user_id) on delete restrict,
  constraint signup_entitlements_is_the_grant_row
    foreign key (ledger_operation_id, wallet_id, ledger_kind)
    references infrx.credit_ledger (operation_id, wallet_id, kind) on delete restrict
);
create or replace trigger signup_entitlements_immutable before update or delete
  on infrx.signup_entitlements for each row execute function infrx.forbid_update_delete();

-- ==================================================== the accounting regime on jobs ===
-- 06a AdmissionPins: every admission states its regime explicitly. Existing jobs are the
-- pilot's USD regime, which is what the one-time default records; the default is then
-- dropped, so a NEW job that does not name its regime is refused (never unmetered).
alter table infrx.jobs
  add column if not exists accounting_regime text not null default 'legacy_usd',
  add column if not exists wallet_id uuid,
  add column if not exists model_id uuid,
  add column if not exists requested_model text,
  add column if not exists deployment_revision_id uuid,
  add column if not exists serving_version_id uuid,
  add column if not exists rate_card_version text,
  add column if not exists policy_version text;
alter table infrx.jobs
  alter column accounting_regime drop default,
  -- Allowed legacy change: a CREDIT job has a rate card, not a USD price version; the
  -- regime CHECK below requires both for every legacy_usd row, so no USD job loses one.
  alter column price_version drop not null,
  alter column price_snapshot drop not null;

do $$
begin
  if not exists (select 1 from pg_constraint where conrelid = 'infrx.jobs'::regclass
                 and conname = 'jobs_accounting_regime_check') then
    alter table infrx.jobs
      add constraint jobs_accounting_regime_check
        check (accounting_regime in ('legacy_usd', 'credit')),
      -- The regime fixes the provenance: USD jobs keep their price version and carry no
      -- pin; CREDIT jobs carry every pin and the wallet, and no USD price.
      add constraint jobs_regime_fixes_provenance check (case accounting_regime
        when 'legacy_usd' then price_version is not null and price_snapshot is not null
          and num_nulls(wallet_id, model_id, requested_model, deployment_revision_id,
                        serving_version_id, rate_card_version, policy_version) = 7
        else price_version is null and price_snapshot is null
          and num_nonnulls(wallet_id, model_id, requested_model, deployment_revision_id,
                           serving_version_id, rate_card_version, policy_version) = 7 end),
      add constraint jobs_wallet_fk foreign key (wallet_id)
        references infrx.credit_wallets (wallet_id) on delete restrict,
      -- Composite targets: a hold, a debit and a CREDIT usage row reference the job
      -- together with the pins they must agree with.
      add constraint jobs_request_wallet_key unique (request_id, wallet_id),
      add constraint jobs_hold_target_key
        unique (request_id, org_id, wallet_id, rate_card_version),
      add constraint jobs_settlement_target_key
        unique (request_id, rate_card_version, serving_version_id, deployment_revision_id);
  end if;
end $$;

-- R78: the pins never change after acceptance, whatever else the job does.
create or replace function infrx.jobs_pins_guard() returns trigger
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
begin
  if new.accounting_regime is distinct from old.accounting_regime
     or new.wallet_id is distinct from old.wallet_id
     or new.model_id is distinct from old.model_id
     or new.requested_model is distinct from old.requested_model
     or new.deployment_revision_id is distinct from old.deployment_revision_id
     or new.serving_version_id is distinct from old.serving_version_id
     or new.rate_card_version is distinct from old.rate_card_version
     or new.policy_version is distinct from old.policy_version then
    raise exception 'job %: the accounting regime and admission pins are immutable',
      old.request_id using errcode = '23514';
  end if;
  return new;
end $$;
create or replace trigger jobs_pins_guard before update on infrx.jobs
  for each row execute function infrx.jobs_pins_guard();

-- Admission is honoured only in an enabled regime (maintenance refusal otherwise), and a
-- CREDIT job spends only a wallet its admission may reach (R66): an individual's wallet
-- through their personal organization, or a provider's dev wallet on that provider's own
-- dev deployment. The registry relations are 0007's; plpgsql resolves them at call time.
create or replace function infrx.jobs_admission_guard() returns trigger
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  w infrx.credit_wallets%rowtype;
begin
  if new.accounting_regime = 'legacy_usd' then
    perform infrx.require_feature('legacy_usd_admission');
    return new;
  end if;
  perform infrx.require_feature('credit_admission');
  select * into w from infrx.credit_wallets where wallet_id = new.wallet_id;
  if w.kind = 'consumer' and w.personal_org_id is distinct from new.org_id then
    raise exception 'job %: wallet % is not funded through organization %',
      new.request_id, new.wallet_id, new.org_id using errcode = '23514';
  end if;
  if w.kind = 'provider_dev' and not exists (
       select 1 from infrx.deployment_revisions d
       where d.deployment_revision_id = new.deployment_revision_id
         and d.provider_org_id = w.owner_provider_org_id and d.environment = 'dev') then
    raise exception 'job %: a provider_dev wallet funds only its own dev deployments',
      new.request_id using errcode = '23514';
  end if;
  return new;
end $$;
create or replace trigger jobs_admission_guard before insert on infrx.jobs
  for each row execute function infrx.jobs_admission_guard();

-- ============================================================== CREDIT holds ===
-- One hold per request, ever (the key); it belongs to the job AND carries the job's
-- wallet and rate card (the composite key), so a hold cannot fund another wallet or
-- price at another card.
create table if not exists infrx.credit_wallet_holds (
  request_id uuid primary key,
  org_id uuid not null,
  wallet_id uuid not null,
  rate_card_version text not null,
  amount numeric(20,8) not null check (amount >= 0),
  unit text not null default 'CREDIT' check (unit = 'CREDIT'),
  state text not null check (state in ('held', 'settled', 'released', 'unknown')),
  reconcile_after timestamptz,
  created_at timestamptz not null default infrx.now(),
  updated_at timestamptz not null default infrx.now(),
  constraint credit_wallet_holds_unknown_window
    check ((state = 'unknown') = (reconcile_after is not null)),
  constraint credit_wallet_holds_job_fk
    foreign key (request_id, org_id, wallet_id, rate_card_version)
    references infrx.jobs (request_id, org_id, wallet_id, rate_card_version)
    on delete restrict
);
create index if not exists credit_wallet_holds_wallet_active_idx
  on infrx.credit_wallet_holds (wallet_id) where state in ('held', 'unknown');
create index if not exists credit_wallet_holds_reconcile_idx
  on infrx.credit_wallet_holds (reconcile_after) where state = 'unknown';

-- The reservation moves only here: a new hold reserves (and fails
-- `credit_wallets_reserved_within_total` when available is short), leaving
-- held/unknown releases it. Unknown usage keeps its hold and is never debited later
-- (02): unknown -> released is its only exit.
create or replace function infrx.credit_wallet_holds_moves_wallet() returns trigger
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'hold % is never deleted', old.request_id using errcode = '23514';
  end if;
  if tg_op = 'INSERT' then
    if new.state <> 'held' then
      raise exception 'hold %: a hold starts held', new.request_id using errcode = '23514';
    end if;
    update infrx.credit_wallets set reserved_total = reserved_total + new.amount,
                                    revision = revision + 1
     where wallet_id = new.wallet_id;
    return new;
  end if;
  if new.request_id is distinct from old.request_id or new.org_id is distinct from old.org_id
     or new.wallet_id is distinct from old.wallet_id or new.amount is distinct from old.amount
     or new.unit is distinct from old.unit or new.created_at is distinct from old.created_at
     or new.rate_card_version is distinct from old.rate_card_version then
    raise exception 'hold %: identity, wallet, card and amount are immutable',
      old.request_id using errcode = '23514';
  end if;
  if new.state is distinct from old.state and not (
       (old.state = 'held' and new.state in ('settled', 'released', 'unknown'))
    or (old.state = 'unknown' and new.state = 'released')) then
    raise exception 'hold %: % -> % is not an allowed transition', old.request_id,
      old.state, new.state using errcode = '23514';
  end if;
  if old.state in ('held', 'unknown') and new.state in ('settled', 'released') then
    update infrx.credit_wallets set reserved_total = reserved_total - old.amount,
                                    revision = revision + 1
     where wallet_id = old.wallet_id;
  end if;
  new.updated_at := infrx.now();
  return new;
end $$;
create or replace trigger credit_wallet_holds_moves_wallet
  before insert or update or delete on infrx.credit_wallet_holds
  for each row execute function infrx.credit_wallet_holds_moves_wallet();

-- ============================================= the accounting regime on usage ===
-- The deployed gateway still posts USD rows that name no regime, so the default stays:
-- a writer that does not say otherwise IS the legacy USD writer. A CREDIT row must say so
-- and carry the card and serving revision it was admitted at (06a UsageRecordV2); the
-- composite key below makes those exactly the job's pins (CREDIT-RATE). `cost_usd` is
-- untouched history; `charged_credits` is a separate column, never a relabelled one.
alter table public.usage_events
  add column if not exists accounting_regime text not null default 'legacy_usd',
  add column if not exists charged_credits numeric(20,8),
  add column if not exists rate_card_version text,
  add column if not exists serving_version_id uuid,
  add column if not exists deployment_revision_id uuid;

do $$
begin
  if not exists (select 1 from pg_constraint where conrelid = 'public.usage_events'::regclass
                 and conname = 'usage_events_accounting_regime_check') then
    alter table public.usage_events
      add constraint usage_events_accounting_regime_check
        check (accounting_regime in ('legacy_usd', 'credit')),
      add constraint usage_events_regime_fixes_unit check (case accounting_regime
        when 'legacy_usd' then num_nulls(charged_credits, rate_card_version,
                                         serving_version_id, deployment_revision_id) = 4
        else charged_credits is not null and charged_credits >= 0
          and num_nonnulls(rate_card_version, serving_version_id,
                           deployment_revision_id) = 3
          and price_version is null and cost_usd = 0 and settlement_regime = 'pilot' end),
      add constraint usage_events_credit_row_is_the_job
        foreign key (id, rate_card_version, serving_version_id, deployment_revision_id)
        references infrx.jobs (request_id, rate_card_version, serving_version_id,
                               deployment_revision_id);
  end if;
end $$;

-- The detector (R59-7): a CREDIT summary equals its immutable ledger and its active
-- holds, for ever. A nonzero drift is a reconciliation failure.
create or replace view infrx.credit_wallet_reconciliation as
select w.wallet_id, w.kind,
       w.ledger_total - coalesce((select sum(l.amount) from infrx.credit_ledger l
                                  where l.wallet_id = w.wallet_id), 0) as ledger_drift,
       w.reserved_total - coalesce((select sum(h.amount) from infrx.credit_wallet_holds h
                                    where h.wallet_id = w.wallet_id
                                      and h.state in ('held', 'unknown')), 0) as reserved_drift
from infrx.credit_wallets w;

-- ================================================================ privileges ===
-- `infrx` stays unreachable for browser roles (0004's schema revoke still holds). The
-- platform role READS the money relations and writes them only through D's SECURITY
-- DEFINER operations (the grant below; D2-D5's bodies): a direct INSERT would be a mint,
-- a direct hold write an unreserved spend. 0004's default privileges granted it all four
-- verbs on anything new in `infrx`, so they are taken back here explicitly.
alter table infrx.feature_flags enable row level security;
alter table infrx.credit_wallets enable row level security;
alter table infrx.credit_ledger enable row level security;
alter table infrx.signup_entitlements enable row level security;
alter table infrx.credit_wallet_holds enable row level security;

revoke all on infrx.feature_flags, infrx.credit_wallets, infrx.credit_ledger,
              infrx.signup_entitlements, infrx.credit_wallet_holds
  from public, anon, authenticated, service_role;
revoke all on infrx.credit_wallet_reconciliation from public, anon, authenticated;
grant select on infrx.feature_flags, infrx.credit_wallets, infrx.credit_ledger,
                infrx.signup_entitlements, infrx.credit_wallet_holds,
                infrx.credit_wallet_reconciliation to service_role;
-- The rollout switch: an operator action through the platform role, attributed.
grant update (enabled, updated_by, reason, updated_at) on infrx.feature_flags
  to service_role;

revoke all on function infrx.require_feature(text) from public, anon, authenticated;
revoke all on function infrx.credit_wallets_guard() from public, anon, authenticated;
revoke all on function infrx.credit_ledger_moves_wallet() from public, anon, authenticated;
revoke all on function infrx.jobs_pins_guard() from public, anon, authenticated;
revoke all on function infrx.jobs_admission_guard() from public, anon, authenticated;
revoke all on function infrx.credit_wallet_holds_moves_wallet()
  from public, anon, authenticated;

-- Append-only relations refuse TRUNCATE for every role (R59-4), as 0003's do.
do $$
declare
  r text;
begin
  foreach r in array array['infrx.credit_wallets', 'infrx.credit_ledger',
                           'infrx.signup_entitlements', 'infrx.credit_wallet_holds']
  loop
    execute format('create or replace trigger %I before truncate on %s for each statement '
                   'execute function infrx.forbid_truncate()',
                   replace(r, '.', '_') || '_no_truncate', r);
  end loop;
end $$;

-- ======================================================== the initial grant (A1) ===
-- 02 "Wallet and idempotency", as ONE transaction: create or lock the individual's
-- consumer wallet, insert the unique entitlement, append +10000.00000000 CREDIT, and let
-- the ledger trigger move the total. A retry - same or different operation id, another
-- campaign, another organization - returns the existing grant with `replayed`. A race
-- loses on the wallet row lock and then finds the winner's entitlement.
--
-- The personal organization is the one the signup trigger made: created by the user,
-- owned by them. An organization with other members is a billing-owner transition that
-- 02 §2 says must be resolved before cutover, so it is refused as a rollout hold rather
-- than attaching a shared org to one individual's grant. Verification is A1's policy;
-- the evidence reference is mandatory here, so an unverified call cannot mint.
create or replace function infrx.grant_signup_credit(
  p_user_id uuid, p_verification_evidence_ref text, p_campaign_version text default '',
  p_operation_id uuid default null)
returns table (user_id uuid, wallet_id uuid, ledger_operation_id uuid, amount text,
               granted_at timestamptz, replayed boolean)
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
#variable_conflict use_column
declare
  v_org uuid;
  v_wallet uuid;
  v_op uuid := coalesce(p_operation_id, gen_random_uuid());
begin
  perform infrx.require_feature('signup_grant');
  if p_user_id is null or length(btrim(coalesce(p_verification_evidence_ref, ''))) = 0 then
    raise exception 'invalid_request: a user and its verification evidence are required'
      using errcode = '22023';
  end if;
  select w.wallet_id into v_wallet from infrx.credit_wallets w
   where w.owner_user_id = p_user_id and w.kind = 'consumer';
  if v_wallet is null then
    select o.id into v_org from public.organizations o
      join public.org_members m on m.org_id = o.id and m.user_id = p_user_id
                                and m.role = 'owner'
     where o.created_by = p_user_id
     order by o.created_at, o.id limit 1;
    if v_org is null then
      raise exception 'not_found: user % has no personal organization', p_user_id
        using errcode = 'P0002';
    end if;
    if (select count(*) from public.org_members m where m.org_id = v_org) > 1 then
      raise exception 'rollout_hold: organization % has other members; its billing owner '
        'must be resolved before an individual wallet is bound to it', v_org
        using errcode = '55000';
    end if;
    insert into infrx.credit_wallets (kind, owner_user_id, personal_org_id)
    values ('consumer', p_user_id, v_org)
    on conflict (owner_user_id) where kind = 'consumer' do nothing;
  end if;
  select w.wallet_id into v_wallet from infrx.credit_wallets w
   where w.owner_user_id = p_user_id and w.kind = 'consumer' for update;

  if not exists (select 1 from infrx.signup_entitlements e
                 where e.user_id = p_user_id and e.entitlement = 'initial_signup_grant') then
    insert into infrx.credit_ledger (wallet_id, wallet_kind, kind, amount, operation_id,
                                     actor, reason)
    values (v_wallet, 'consumer', 'signup_grant', 10000.00000000, v_op, 'platform',
            'initial_signup_grant');
    insert into infrx.signup_entitlements (user_id, entitlement, wallet_id, amount,
      verification_evidence_ref, ledger_operation_id, campaign_version)
    values (p_user_id, 'initial_signup_grant', v_wallet, 10000.00000000,
            p_verification_evidence_ref, v_op, coalesce(p_campaign_version, ''));
    return query select e.user_id, e.wallet_id, e.ledger_operation_id, e.amount::text,
                        e.granted_at, false
                 from infrx.signup_entitlements e
                 where e.user_id = p_user_id and e.entitlement = 'initial_signup_grant';
    return;
  end if;
  return query select e.user_id, e.wallet_id, e.ledger_operation_id, e.amount::text,
                      e.granted_at, true
               from infrx.signup_entitlements e
               where e.user_id = p_user_id and e.entitlement = 'initial_signup_grant';
end $$;
revoke all on function infrx.grant_signup_credit(uuid, text, text, uuid)
  from public, anon, authenticated;
grant execute on function infrx.grant_signup_credit(uuid, text, text, uuid) to service_role;
comment on function infrx.grant_signup_credit(uuid, text, text, uuid) is
  'A1 seam (D1R): the one-time individual +10000.00000000 CREDIT grant, one transaction, '
  'idempotent on (user_id, initial_signup_grant). service_role only.';
