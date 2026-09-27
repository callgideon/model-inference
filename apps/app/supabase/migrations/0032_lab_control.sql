-- L3-SQL (wave-5 LW2, lane lab-sql; 09-amendment-workstreams §L3, the D-owned schema slice):
-- the Lab's control plane over 0007's registry - no parallel catalog. Dev and prod revisions
-- ARE 0007's `deployment_revisions` (private dev, public prod) and discovery IS its
-- `catalog_listings`, read by `infrx/state/catalog.py`; rows are written by A3's
-- `PgRegistry` (`infrx/state/operations.py`). This file adds who may move a revision, the
-- publication approval and rollback as new listing versions, the provider_dev wallet
-- opening, and an append-only audit of every move (LAB-PUBLISH, the SQL half).
-- LOCAL-ONLY (R151): never applied hosted; the number is the next free at merge.
--
--   lab_control_events   one row per move: provider, revision, action, from -> to, the
--                        listing version it wrote, actor, operator marker, reason.
--
-- Named RPCs (SECURITY DEFINER; EXECUTE for service_role only through 0004's defaults; the
-- caller's provider and operator marker are the server's, never a request field):
--   lab_move_deployment {provider_org_id, deployment_revision_id, state, actor, operator,
--       reason}: the provider moves its own PRIVATE revisions (draft -> validating ->
--       ready_private, or retired) and withdraws a proposal (proposed_public -> retired);
--       draining/retiring a public revision is an operator's; activation is only
--       `lab_approve_publication`'s. 0007's guard decides which moves exist.
--   lab_approve_publication {provider_org_id, deployment_revision_id, public_model_id,
--       rate_card_version, actor, operator, reason}: an OPERATOR activates a provider's
--       proposed public revision whose serving version has a validated (`ready_private`) dev
--       revision, and lists it as the alias's next catalog version at the named card of
--       that revision (serialized per alias). A provider proposes; an operator approves.
--   lab_rollback_publication {public_model_id, deployment_revision_id, actor, operator,
--       reason}: an OPERATOR lists an earlier, still active revision of the alias again - a
--       new listing version at the card it was last listed with. History is never edited;
--       admitted jobs keep the pins they were admitted with.
--   lab_open_dev_wallet {provider_org_id}: the provider's provider_dev CREDIT wallet, created
--       at 0 (0006's guard) when it has none. Money enters only by D5's audited
--       `infrx.grant_credit` operator_allocation; its card is an internal rate card of the
--       private dev deployment (0008 pins the newest effective one).
--   lab_control_history {provider_org_id, deployment_revision_id?}: the provider's events.
--
-- ROLLBACK (this file alone; nothing earlier references it): drop function
--   infrx.lab_control_history(jsonb), infrx.lab_open_dev_wallet(jsonb),
--   infrx.lab_rollback_publication(jsonb), infrx.lab_approve_publication(jsonb),
--   infrx.lab_move_deployment(jsonb), infrx.lab_list_alias(uuid, text, text, text, text),
--   infrx.lab_control_event_json(infrx.lab_control_events); drop table
--   infrx.lab_control_events. Listings, states and wallets written stay (they are history).
--
-- Re-runnable: `if not exists`, `create or replace`.

create table if not exists infrx.lab_control_events (
  event_id bigint generated always as identity primary key,
  provider_org_id uuid not null references infrx.provider_orgs on delete restrict,
  deployment_revision_id uuid not null references infrx.deployment_revisions on delete restrict,
  action text not null check (action in ('move', 'approve', 'rollback')),
  from_state text not null,
  to_state text not null,
  public_model_id text,
  listing_version int,
  actor text not null check (length(btrim(actor)) between 1 and 200),
  by_operator boolean not null,
  reason text not null check (length(btrim(reason)) between 1 and 500),
  at timestamptz not null default infrx.now(),
  check ((action = 'move') = (listing_version is null))
);
create index if not exists lab_control_events_provider
  on infrx.lab_control_events (provider_org_id, event_id);

create or replace trigger lab_control_events_immutable before update or delete
  on infrx.lab_control_events for each row execute function infrx.forbid_update_delete();
create or replace trigger lab_control_events_no_truncate before truncate
  on infrx.lab_control_events for each statement execute function infrx.forbid_truncate();
alter table infrx.lab_control_events enable row level security;
revoke all on infrx.lab_control_events from public, anon, authenticated, service_role;
grant select on infrx.lab_control_events to service_role;

create or replace function infrx.lab_control_event_json(e infrx.lab_control_events)
returns jsonb language sql stable set search_path = infrx, public, pg_temp as $$
  select jsonb_build_object('event_id', e.event_id, 'deployment_revision_id',
    e.deployment_revision_id, 'action', e.action, 'from_state', e.from_state,
    'to_state', e.to_state, 'public_model_id', e.public_model_id,
    'listing_version', e.listing_version, 'actor', e.actor, 'by_operator', e.by_operator,
    'reason', e.reason, 'at', e.at)
$$;

-- The alias's next listing version for one revision at one card, serialized per alias so
-- concurrent approvals and rollbacks take consecutive versions; answers the version.
create or replace function infrx.lab_list_alias(p_deployment uuid, p_alias text,
                                                p_card text, p_actor text, p_reason text)
returns int language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_version int;
begin
  perform pg_advisory_xact_lock(hashtextextended('catalog_listings/' || p_alias, 0));
  insert into infrx.catalog_listings (public_model_id, version, model_id,
    deployment_revision_id, serving_version_id, rate_card_version, effective_at, approved_by)
  select p_alias, coalesce((select max(l.version) from infrx.catalog_listings l
                             where l.public_model_id = p_alias), 0) + 1,
         s.model_id, d.deployment_revision_id, d.serving_version_id, p_card, infrx.now(),
         p_actor
    from infrx.deployment_revisions d
    join infrx.serving_versions s using (serving_version_id)
   where d.deployment_revision_id = p_deployment
  returning version into v_version;
  return v_version;
exception when foreign_key_violation or not_null_violation then
  perform infrx.refuse('invalid_request', 'the alias is the model''s public id and the card '
                       'is an effective card of this revision');
  return null;
end $$;

create or replace function infrx.lab_move_deployment(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_to text := p_args->>'state';
  v_operator boolean := coalesce((p_args->>'operator')::boolean, false);
  d infrx.deployment_revisions%rowtype;
  e infrx.lab_control_events%rowtype;
begin
  select * into d from infrx.deployment_revisions
   where deployment_revision_id = (p_args->>'deployment_revision_id')::uuid
     and provider_org_id = (p_args->>'provider_org_id')::uuid for update;
  if not found then
    perform infrx.refuse('not_found', 'no such deployment revision for this provider');
  end if;
  if v_to = 'active' then
    perform infrx.refuse('forbidden', 'a public revision is activated only by an approved '
                         'publication');
  end if;
  if not v_operator and d.visibility = 'public'
     and not (d.state = 'proposed_public' and v_to = 'retired') then
    perform infrx.refuse('forbidden', 'draining or retiring a public revision is an '
                         'operator''s');
  end if;
  begin
    update infrx.deployment_revisions set state = v_to
     where deployment_revision_id = d.deployment_revision_id;
  exception when check_violation or not_null_violation then
    perform infrx.refuse('state_conflict', 'deployment revision: ' || d.state || ' -> '
                         || coalesce(v_to, '?') || ' is not an allowed transition');
  end;
  insert into infrx.lab_control_events (provider_org_id, deployment_revision_id, action,
    from_state, to_state, actor, by_operator, reason)
  values (d.provider_org_id, d.deployment_revision_id, 'move', d.state, v_to,
    p_args->>'actor', v_operator, p_args->>'reason')
  returning * into e;
  return infrx.lab_control_event_json(e);
exception when check_violation or not_null_violation then
  perform infrx.refuse('invalid_request', 'a move names its actor and reason');
  return null;
end $$;

create or replace function infrx.lab_approve_publication(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  d infrx.deployment_revisions%rowtype;
  e infrx.lab_control_events%rowtype;
  v_version int;
begin
  if not coalesce((p_args->>'operator')::boolean, false) then
    perform infrx.refuse('forbidden', 'a provider proposes; an operator approves');
  end if;
  select * into d from infrx.deployment_revisions
   where deployment_revision_id = (p_args->>'deployment_revision_id')::uuid
     and provider_org_id = (p_args->>'provider_org_id')::uuid for update;
  if not found then
    perform infrx.refuse('not_found', 'no such deployment revision for this provider');
  end if;
  if d.state <> 'proposed_public' then
    perform infrx.refuse('state_conflict', 'only a proposed public revision is approved; this '
                         'one is ' || d.state);
  end if;
  if not exists (select 1 from infrx.deployment_revisions x
                  where x.serving_version_id = d.serving_version_id
                    and x.environment = 'dev' and x.state = 'ready_private') then
    perform infrx.refuse('state_conflict', 'the serving version has no validated dev revision');
  end if;
  if not exists (select 1 from infrx.rate_card_versions c
                  where c.rate_card_version = p_args->>'rate_card_version'
                    and c.deployment_revision_id = d.deployment_revision_id
                    and c.effective_at <= infrx.now()) then
    perform infrx.refuse('invalid_request', 'the card is an effective card of this revision');
  end if;
  update infrx.deployment_revisions set state = 'active'
   where deployment_revision_id = d.deployment_revision_id;
  v_version := infrx.lab_list_alias(d.deployment_revision_id, p_args->>'public_model_id',
                                    p_args->>'rate_card_version', p_args->>'actor',
                                    p_args->>'reason');
  insert into infrx.lab_control_events (provider_org_id, deployment_revision_id, action,
    from_state, to_state, public_model_id, listing_version, actor, by_operator, reason)
  values (d.provider_org_id, d.deployment_revision_id, 'approve', d.state, 'active',
    p_args->>'public_model_id', v_version, p_args->>'actor', true, p_args->>'reason')
  returning * into e;
  return infrx.lab_control_event_json(e);
exception when check_violation or not_null_violation then
  perform infrx.refuse('invalid_request', 'an approval names its actor and reason');
  return null;
end $$;

create or replace function infrx.lab_rollback_publication(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_alias text := p_args->>'public_model_id';
  d infrx.deployment_revisions%rowtype;
  l infrx.catalog_listings%rowtype;
  e infrx.lab_control_events%rowtype;
  v_version int;
begin
  if not coalesce((p_args->>'operator')::boolean, false) then
    perform infrx.refuse('forbidden', 'a rollback is an operator''s');
  end if;
  perform pg_advisory_xact_lock(hashtextextended('catalog_listings/' || v_alias, 0));
  select * into d from infrx.deployment_revisions
   where deployment_revision_id = (p_args->>'deployment_revision_id')::uuid for share;
  -- the revision's last listing under this alias: it must have been listed there before
  select * into l from infrx.catalog_listings where public_model_id = v_alias
     and deployment_revision_id = d.deployment_revision_id order by version desc limit 1;
  if l.version is null then
    perform infrx.refuse('not_found', 'the revision was never listed under ' || v_alias);
  end if;
  if d.state <> 'active' then
    perform infrx.refuse('state_conflict', 'a rollback target is still active; this one is '
                         || d.state);
  end if;
  if (select c.deployment_revision_id from infrx.catalog_listings c
       where c.public_model_id = v_alias order by c.version desc limit 1)
     = d.deployment_revision_id then
    perform infrx.refuse('state_conflict', 'the revision is already the listed one');
  end if;
  v_version := infrx.lab_list_alias(d.deployment_revision_id, v_alias, l.rate_card_version,
                                    p_args->>'actor', p_args->>'reason');
  insert into infrx.lab_control_events (provider_org_id, deployment_revision_id, action,
    from_state, to_state, public_model_id, listing_version, actor, by_operator, reason)
  values (d.provider_org_id, d.deployment_revision_id, 'rollback', d.state, d.state, v_alias,
    v_version, p_args->>'actor', true, p_args->>'reason')
  returning * into e;
  return infrx.lab_control_event_json(e);
exception when check_violation or not_null_violation then
  perform infrx.refuse('invalid_request', 'a rollback names its actor and reason');
  return null;
end $$;

create or replace function infrx.lab_open_dev_wallet(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_provider uuid := (p_args->>'provider_org_id')::uuid;
  w infrx.credit_wallets%rowtype;
begin
  if not exists (select 1 from infrx.provider_orgs where provider_org_id = v_provider) then
    perform infrx.refuse('not_found', 'no such provider');
  end if;
  insert into infrx.credit_wallets (kind, owner_provider_org_id)
  values ('provider_dev', v_provider)
  on conflict (owner_provider_org_id) where kind = 'provider_dev' do nothing;
  select * into w from infrx.credit_wallets
   where owner_provider_org_id = v_provider and kind = 'provider_dev';
  return jsonb_build_object('wallet_id', w.wallet_id, 'kind', w.kind, 'unit', w.unit,
    'owner_provider_org_id', w.owner_provider_org_id, 'ledger_total', w.ledger_total::text,
    'reserved_total', w.reserved_total::text);
end $$;

create or replace function infrx.lab_control_history(p_args jsonb) returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select coalesce(jsonb_agg(infrx.lab_control_event_json(e) order by e.event_id), '[]')
    from infrx.lab_control_events e
   where e.provider_org_id = (p_args->>'provider_org_id')::uuid
     and (p_args->>'deployment_revision_id' is null
          or e.deployment_revision_id = (p_args->>'deployment_revision_id')::uuid)
$$;
