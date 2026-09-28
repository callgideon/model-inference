-- L3-SQL (wave-5 LW2, lane lab-sql; 09-amendment-workstreams §L3, the D-owned schema slice):
-- the Lab's control plane over 0007's registry - no parallel catalog. Dev and prod revisions
-- ARE 0007's `deployment_revisions` (private dev, public prod) and discovery IS its
-- `catalog_listings`, read by `infrx/state/catalog.py`; serving revisions, dev revisions and
-- internal cards are written by A3's `PgRegistry`. This file is the L3 service's
-- `ControlStore` seam (WR-L3-1, `infrx/state/lab_control.py: PgControlStore`): every state
-- move, proposal, dev credential, listing version and dev-wallet allocation is ONE call that
-- does its compare-and-set and writes its audit event in the same transaction.
-- LOCAL-ONLY (R151): never applied hosted; the number is the next free at merge.
--
--   lab_control_events   append-only: {action, actor, provider_org_id, subject, before, after,
--                        at} per lab_transition | lab_propose | lab_dev_key | lab_publish |
--                        lab_rollback | lab_fund (never a key hash).
--
-- Named RPCs (SECURITY DEFINER; EXECUTE for service_role only through 0004's defaults; the
-- provider and the operator are the server's - L2's membership, `Operations.operator` - never
-- a request field; refusals `<code>: detail`, jobstore.domain_error):
--   lab_control_endpoint   {provider_org_id, name, environment, actor}: the provider's endpoint,
--                          created if absent -> {endpoint_id}.
--   lab_control_transition {deployment_revision_id, provider_org_id, expected, to, actor,
--                          reason}: CAS on a PRIVATE revision's state (0007's guard keeps the
--                          graph); absent, foreign or public -> not_found, another state or an
--                          undeclared move -> state_conflict.
--   lab_control_propose    {proposal, source_revision_id, actor}: insert the public
--                          `proposed_public` prod revision of the provider's `ready_private`
--                          dev source of the same serving version.
--   lab_control_dev_key    {provider_org_id, endpoint_id, user_id, key_hash, prefix, name}: a
--                          provider_dev `api_keys` row scoped to the provider's dev endpoint,
--                          filed in the provider's own organization (see below).
--   lab_control_publish    {public_model_id, card, expected_version, actor, reason}: CAS on the
--                          alias's current version; the card is inserted, its
--                          `proposed_public` revision becomes `active`, listing version + 1.
--                          The previous listing's revision is left active (admitted pins).
--   lab_control_rollback   {public_model_id, to_version, expected_version, actor, reason}: CAS;
--                          version + 1 repeating an earlier version whose revision is active.
--   lab_control_fund       {provider_org_id, amount, operation_id, actor, reason}: the
--                          provider_dev wallet at 0 if absent, then D5's audited
--                          `grant_credit` operator_allocation (replay by operation id).
--   lab_control_events     {provider_org_id}: the provider's events, oldest first.
--
-- A provider_dev key's `org_id` is its provider (auth.context requires org_id =
-- provider_org_id) and `api_keys.org_id` references `public.organizations`, so the first dev
-- key opens a workspace organization whose id IS the provider's (slug `lab-<provider id>`, no
-- members; 0003's trigger gives it the legacy zero wallet and nothing else - no grant).
--
-- ROLLBACK (this file alone; nothing earlier references it): drop function
--   infrx.lab_control_events(jsonb), infrx.lab_control_fund(jsonb),
--   infrx.lab_control_rollback(jsonb), infrx.lab_control_publish(jsonb),
--   infrx.lab_control_dev_key(jsonb), infrx.lab_control_propose(jsonb),
--   infrx.lab_control_transition(jsonb), infrx.lab_control_endpoint(jsonb),
--   infrx.lab_control_listing(infrx.catalog_listings),
--   infrx.lab_control_audit(uuid, text, text, text, jsonb, jsonb); drop table
--   infrx.lab_control_events. Listings, states, keys, provider organizations and wallets
--   written stay (they are history).
--
-- Re-runnable: `if not exists`, `create or replace`.

create table if not exists infrx.lab_control_events (
  event_id bigint generated always as identity primary key,
  provider_org_id uuid not null references infrx.provider_orgs on delete restrict,
  action text not null check (action in ('lab_transition', 'lab_propose', 'lab_dev_key',
                                         'lab_publish', 'lab_rollback', 'lab_fund')),
  actor text not null check (length(btrim(actor)) between 1 and 200),
  subject text not null check (length(subject) between 1 and 200),
  before jsonb,
  after jsonb not null,
  at timestamptz not null default infrx.now()
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

create or replace function infrx.lab_control_audit(p_provider uuid, p_action text,
  p_actor text, p_subject text, p_after jsonb, p_before jsonb default null)
returns void language plpgsql security definer set search_path = infrx, public, pg_temp as $$
begin
  insert into infrx.lab_control_events (provider_org_id, action, actor, subject, before, after)
  values (p_provider, p_action, p_actor, p_subject, p_before, p_after);
exception when check_violation or not_null_violation then
  perform infrx.refuse('invalid_request', 'a control move names its actor');
end $$;

create or replace function infrx.lab_control_listing(l infrx.catalog_listings) returns jsonb
language sql stable set search_path = infrx, public, pg_temp as $$
  select case when l.version is null then null else jsonb_build_object(
    'public_model_id', l.public_model_id, 'version', l.version,
    'deployment_revision_id', l.deployment_revision_id,
    'rate_card_version', l.rate_card_version) end
$$;

create or replace function infrx.lab_control_endpoint(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_provider uuid := (p_args->>'provider_org_id')::uuid;
  v_id uuid;
begin
  begin
    insert into infrx.endpoints (provider_org_id, name, environment, created_by)
    values (v_provider, p_args->>'name', p_args->>'environment', p_args->>'actor')
    on conflict (provider_org_id, name, environment) do nothing;
  exception when foreign_key_violation then
    perform infrx.refuse('not_found', 'no such provider');
  when check_violation or not_null_violation then
    perform infrx.refuse('invalid_request', 'an endpoint is a lowercase name, dev or prod, '
                         'created by a named actor');
  end;
  select endpoint_id into v_id from infrx.endpoints
   where (provider_org_id, name, environment) = (v_provider, p_args->>'name',
                                                  p_args->>'environment');
  return jsonb_build_object('endpoint_id', v_id);
end $$;

create or replace function infrx.lab_control_transition(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_id uuid := (p_args->>'deployment_revision_id')::uuid;
  v_provider uuid := (p_args->>'provider_org_id')::uuid;
  d infrx.deployment_revisions%rowtype;
begin
  select * into d from infrx.deployment_revisions
   where deployment_revision_id = v_id and provider_org_id = v_provider
     and visibility = 'private' for update;
  if not found then
    perform infrx.refuse('not_found', 'no such dev revision for this provider');
  end if;
  if d.state is distinct from p_args->>'expected' then
    perform infrx.refuse('state_conflict', 'the revision is ' || d.state || ', not '
                         || coalesce(p_args->>'expected', '?'));
  end if;
  begin
    update infrx.deployment_revisions set state = p_args->>'to'
     where deployment_revision_id = v_id returning * into d;
  exception when check_violation or not_null_violation then
    perform infrx.refuse('state_conflict', 'deployment revision: ' || d.state || ' -> '
                         || coalesce(p_args->>'to', '?') || ' is not an allowed transition');
  end;
  perform infrx.lab_control_audit(v_provider, 'lab_transition', p_args->>'actor', v_id::text,
    jsonb_build_object('state', d.state, 'reason', p_args->>'reason'),
    jsonb_build_object('state', p_args->>'expected'));
  return to_jsonb(d);
end $$;

create or replace function infrx.lab_control_propose(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  p jsonb := p_args->'proposal';
  s infrx.deployment_revisions%rowtype;
  d infrx.deployment_revisions%rowtype;
begin
  select * into s from infrx.deployment_revisions
   where deployment_revision_id = (p_args->>'source_revision_id')::uuid for share;
  if not found or s.provider_org_id is distinct from (p->>'provider_org_id')::uuid then
    perform infrx.refuse('not_found', 'no such dev revision for this provider');
  end if;
  if (s.environment, s.visibility, s.state) <> ('dev', 'private', 'ready_private')
     or s.serving_version_id is distinct from (p->>'serving_version_id')::uuid then
    perform infrx.refuse('state_conflict', 'only a validated dev revision''s serving version '
                         'is proposed');
  end if;
  if p->>'state' is distinct from 'proposed_public' then
    perform infrx.refuse('invalid_request', 'a proposal is a proposed_public revision');
  end if;
  begin
    insert into infrx.deployment_revisions (deployment_revision_id, endpoint_id,
      provider_org_id, environment, serving_version_id, visibility, state, max_input_tokens,
      max_output_tokens, created_by, created_at)
    values ((p->>'deployment_revision_id')::uuid, (p->>'endpoint_id')::uuid, s.provider_org_id,
      p->>'environment', s.serving_version_id, p->>'visibility', 'proposed_public',
      (p->>'max_input_tokens')::int, (p->>'max_output_tokens')::int, p_args->>'actor',
      coalesce((p->>'created_at')::timestamptz, infrx.now()))
    returning * into d;
  exception when unique_violation then
    perform infrx.refuse('state_conflict', 'a revision already holds this id');
  when check_violation or not_null_violation or foreign_key_violation then
    perform infrx.refuse('invalid_request', 'a proposal is a public prod revision on the '
                         'provider''s prod endpoint, proposed by a named actor');
  end;
  perform infrx.lab_control_audit(s.provider_org_id, 'lab_propose', p_args->>'actor',
    d.deployment_revision_id::text, jsonb_build_object('source', s.deployment_revision_id));
  return to_jsonb(d);
end $$;

create or replace function infrx.lab_control_dev_key(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_provider uuid := (p_args->>'provider_org_id')::uuid;
  e infrx.endpoints%rowtype;
  v_key uuid;
begin
  select * into e from infrx.endpoints where endpoint_id = (p_args->>'endpoint_id')::uuid
     and provider_org_id = v_provider and environment = 'dev';
  if not found then
    perform infrx.refuse('not_found', 'no such dev endpoint for this provider');
  end if;
  insert into public.organizations (id, name, slug)
  select o.provider_org_id, o.display_name, 'lab-' || o.provider_org_id
    from infrx.provider_orgs o where o.provider_org_id = v_provider
  on conflict (id) do nothing;
  begin
    insert into public.api_keys (org_id, created_by, name, prefix, key_hash, audience,
      provider_org_id, endpoint_id)
    values (v_provider, (p_args->>'user_id')::uuid, p_args->>'name', p_args->>'prefix',
      p_args->>'key_hash', 'provider_dev', v_provider, e.endpoint_id)
    returning id into v_key;
  exception when unique_violation or not_null_violation or check_violation
       or foreign_key_violation then
    perform infrx.refuse('invalid_request', 'a dev credential is a new hash with a name and '
                         'prefix, issued to a member');
  end;
  perform infrx.lab_control_audit(v_provider, 'lab_dev_key', p_args->>'user_id', v_key::text,
    jsonb_build_object('endpoint_id', e.endpoint_id, 'prefix', p_args->>'prefix'));
  return jsonb_build_object('key_id', v_key);
end $$;

create or replace function infrx.lab_control_publish(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_alias text := p_args->>'public_model_id';
  c jsonb := p_args->'card';
  cur infrx.catalog_listings%rowtype;
  l infrx.catalog_listings%rowtype;
  d infrx.deployment_revisions%rowtype;
begin
  perform pg_advisory_xact_lock(hashtextextended('catalog_listings/' || v_alias, 0));
  select * into cur from infrx.catalog_listings where public_model_id = v_alias
   order by version desc limit 1;
  if cur.version is distinct from (p_args->>'expected_version')::int then
    perform infrx.refuse('state_conflict', v_alias || ' is no longer at version '
                         || coalesce(p_args->>'expected_version', 'none'));
  end if;
  select * into d from infrx.deployment_revisions
   where deployment_revision_id = (c->>'deployment_revision_id')::uuid for update;
  if not found then
    perform infrx.refuse('not_found', 'no such deployment revision');
  end if;
  if d.state <> 'proposed_public' then
    perform infrx.refuse('state_conflict', 'only a proposed public revision is published; '
                         'this one is ' || d.state);
  end if;
  begin
    insert into infrx.rate_card_versions (rate_card_version, model_id, deployment_revision_id,
      serving_version_id, input_rate_per_million, output_rate_per_million, effective_at,
      approved_by, provisional)
    values (c->>'rate_card_version', (c->>'model_id')::uuid, d.deployment_revision_id,
      (c->>'serving_version_id')::uuid, (c->>'input_rate_per_million')::numeric,
      (c->>'output_rate_per_million')::numeric, (c->>'effective_at')::timestamptz,
      c->>'approved_by', position('P-01' in c->>'approved_by') > 0);
    update infrx.deployment_revisions set state = 'active'
     where deployment_revision_id = d.deployment_revision_id;
    insert into infrx.catalog_listings (public_model_id, version, model_id,
      deployment_revision_id, serving_version_id, rate_card_version, effective_at, approved_by)
    values (v_alias, coalesce(cur.version, 0) + 1, (c->>'model_id')::uuid,
      d.deployment_revision_id, d.serving_version_id, c->>'rate_card_version', infrx.now(),
      p_args->>'actor')
    returning * into l;
  exception when unique_violation then
    perform infrx.refuse('state_conflict', 'a card of this version already exists');
  when foreign_key_violation or check_violation or not_null_violation
       or invalid_text_representation then
    perform infrx.refuse('invalid_request', 'the card prices another model or revision, or '
                         'is not an approved card');
  end;
  perform infrx.lab_control_audit(d.provider_org_id, 'lab_publish', p_args->>'actor', v_alias,
    infrx.lab_control_listing(l) || jsonb_build_object('reason', p_args->>'reason'),
    infrx.lab_control_listing(cur));
  return infrx.lab_control_listing(l);
end $$;

create or replace function infrx.lab_control_rollback(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_alias text := p_args->>'public_model_id';
  v_to int := (p_args->>'to_version')::int;
  cur infrx.catalog_listings%rowtype;
  t infrx.catalog_listings%rowtype;
  l infrx.catalog_listings%rowtype;
  d infrx.deployment_revisions%rowtype;
begin
  perform pg_advisory_xact_lock(hashtextextended('catalog_listings/' || v_alias, 0));
  select * into cur from infrx.catalog_listings where public_model_id = v_alias
   order by version desc limit 1;
  if cur.version is distinct from (p_args->>'expected_version')::int then
    perform infrx.refuse('state_conflict', v_alias || ' moved on from version '
                         || coalesce(p_args->>'expected_version', 'none'));
  end if;
  select * into t from infrx.catalog_listings
   where public_model_id = v_alias and version = v_to and v_to < cur.version;
  if not found then
    perform infrx.refuse('not_found', v_alias || ' has no earlier version '
                         || coalesce(v_to::text, '?'));
  end if;
  select * into d from infrx.deployment_revisions
   where deployment_revision_id = t.deployment_revision_id for share;
  if d.state <> 'active' then
    perform infrx.refuse('state_conflict', 'the target revision is ' || d.state);
  end if;
  begin
    insert into infrx.catalog_listings (public_model_id, version, model_id,
      deployment_revision_id, serving_version_id, rate_card_version, effective_at, approved_by)
    values (v_alias, cur.version + 1, t.model_id, t.deployment_revision_id,
      t.serving_version_id, t.rate_card_version, infrx.now(), p_args->>'actor')
    returning * into l;
  exception when check_violation or not_null_violation then
    perform infrx.refuse('invalid_request', 'a rollback names its actor');
  end;
  perform infrx.lab_control_audit(d.provider_org_id, 'lab_rollback', p_args->>'actor', v_alias,
    infrx.lab_control_listing(l) || jsonb_build_object('reason', p_args->>'reason'),
    infrx.lab_control_listing(cur));
  return infrx.lab_control_listing(l);
end $$;

create or replace function infrx.lab_control_fund(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_provider uuid := (p_args->>'provider_org_id')::uuid;
  v_wallet uuid;
  v_answer jsonb;
begin
  if not exists (select 1 from infrx.provider_orgs where provider_org_id = v_provider) then
    perform infrx.refuse('not_found', 'no such provider');
  end if;
  insert into infrx.credit_wallets (kind, owner_provider_org_id)
  values ('provider_dev', v_provider)
  on conflict (owner_provider_org_id) where kind = 'provider_dev' do nothing;
  select wallet_id into v_wallet from infrx.credit_wallets
   where owner_provider_org_id = v_provider and kind = 'provider_dev';
  v_answer := infrx.grant_credit(jsonb_build_object('wallet_id', v_wallet,
    'kind', 'operator_allocation', 'amount', p_args->'amount',
    'operation_id', p_args->>'operation_id', 'actor', p_args->>'actor',
    'reason', p_args->>'reason', 'at', infrx.now()));
  if not (v_answer->>'replayed')::boolean then
    perform infrx.lab_control_audit(v_provider, 'lab_fund', p_args->>'actor', v_wallet::text,
      jsonb_build_object('entry_id', v_answer->'entry'->'entry_id',
                         'amount', v_answer->'entry'->'amount'));
  end if;
  return v_answer;
end $$;

create or replace function infrx.lab_control_events(p_args jsonb) returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select coalesce(jsonb_agg(jsonb_build_object('action', e.action, 'actor', e.actor,
      'provider_org_id', e.provider_org_id, 'subject', e.subject, 'before', e.before,
      'after', e.after, 'at', e.at) order by e.event_id), '[]')
    from infrx.lab_control_events e
   where e.provider_org_id = (p_args->>'provider_org_id')::uuid
$$;
