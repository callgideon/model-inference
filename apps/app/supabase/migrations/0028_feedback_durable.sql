-- D6F (wave-5 LW1, lane lab-sql; 09-amendment-workstreams §D6F, handoffs/D-durable-state D6):
-- the durable feedback transaction and outbox with server-derived, immutable provenance.
-- LOCAL-ONLY (R151): never applied hosted. The number is the next free one at merge.
--
--   infrx.accept_feedback   (0004's boundary, body filled here) {org_id, principal,
--                           by_operator, channel, request_id, feedback_id, body, idem}: one
--                           customer signal. `body` is exactly {name, value, comment?} - an
--                           author, role, channel, calibration or marker key in it is
--                           `invalid_request` (a spoofed author never reaches a row). The
--                           author role is always `customer` (R31); `by_operator` and the
--                           channel are the server's. Ownership is the DURABLE job
--                           (`infrx.jobs`), never a trace projection: another org's or an
--                           unknown request is `not_found`. A suspended org is
--                           `org_suspended` (R33). Replay: same (org, operation, key) and
--                           digest returns the stored row; another digest, or a key a label
--                           made, is `idempotency_conflict` (R54).
--   infrx.label_calibration {principal, is_operator, request_id, feedback_id, label,
--                           rubric_version, comment?, idem}: the only path to an operator
--                           calibration label (R31/R43); the tenant is the job's (R26); one
--                           `audit_entries` row (R34).
--   infrx.request_feedback  {request_id, org_id?, calibration}: a request's rows, customer
--                           signals or labels; with `org_id` the job must be that org's.
--   Both writes commit the row, its idempotency record and one `feedback_projection` outbox
--   event in ONE transaction, so an acknowledgment is never ahead of durability and a
--   crash after commit replays to the same row (FEEDBACK-ACK).
--
-- Enablement is its own flag, `feedback` (09 §D6F: separate from judge): OFF, and a missing
-- row is as closed as a disabled one (0006). This file widens the flag vocabulary and adds no
-- row, so nothing is enabled by applying it; the writes then raise `feature_not_supported`
-- exactly as 0004's stub did. An operator enables it by inserting the row.
--
-- `infrx.feedback` becomes immutable (no UPDATE or DELETE; 0003 already refuses TRUNCATE):
-- author, channel and role are server-derived once and never rewritten.
--
-- ROLLBACK (this file alone): re-run 0004's stub loop for `accept_feedback` (its grants are
--   kept); drop function infrx.label_calibration(jsonb), infrx.request_feedback(jsonb),
--   infrx.feedback_json(infrx.feedback), infrx.feedback_enabled(),
--   infrx.feedback_replay(uuid, jsonb, boolean), infrx.feedback_claim(uuid, jsonb); drop trigger
--   feedback_immutable on infrx.feedback; restore
--   feature_flags_name_check without 'feedback'; drop column entry_seq. Rows written stay
--   (they are history).
--
-- Re-runnable: `create or replace`, `drop ... if exists` before each re-add.

-- ================================================================ enablement ===
alter table infrx.feature_flags drop constraint if exists feature_flags_name_check;
alter table infrx.feature_flags add constraint feature_flags_name_check
  check (name in ('signup_grant', 'credit_admission', 'legacy_usd_admission', 'feedback'));

create or replace function infrx.feedback_enabled() returns void
language plpgsql stable security definer set search_path = infrx, public, pg_temp as $$
begin
  if not coalesce((select f.enabled from infrx.feature_flags f where f.name = 'feedback'),
                  false) then
    raise exception 'feedback is not enabled' using errcode = '0A000';
  end if;
end $$;

-- ================================================================ entry order ===
-- A request's entries list in the order they were accepted; `created_at` cannot say it (two
-- entries in one clock tick) and a feedback id is random.
alter table infrx.feedback add column if not exists entry_seq bigint generated always as identity;

-- =============================================================== immutability ===
drop trigger if exists feedback_immutable on infrx.feedback;
create trigger feedback_immutable before update or delete on infrx.feedback
  for each row execute function infrx.forbid_update_delete();

-- ==================================================================== shapes ===
create or replace function infrx.feedback_json(f infrx.feedback) returns jsonb
language sql stable set search_path = infrx, public, pg_temp as $$
  select jsonb_build_object('feedback_id', f.feedback_id, 'request_id', f.request_id,
    'org_id', f.org_id, 'author_principal', f.author_principal, 'author_role', f.author_role,
    'channel', f.channel, 'name', f.name,
    'value', coalesce(to_jsonb(f.value_bool), to_jsonb(f.value_int), to_jsonb(f.value_text)),
    'comment', f.comment, 'calibration_set', f.calibration_set,
    'rubric_version', f.rubric_version, 'by_operator', f.by_operator,
    'created_at', f.created_at)
$$;

-- The shared replay rule of both writes, called when the scope's idempotency row already
-- exists: its stored row, or `idempotency_conflict` for a changed payload or a key that the
-- OTHER operation (or anything that is not feedback) made (R54).
create or replace function infrx.feedback_replay(p_org uuid, p_idem jsonb, p_label boolean)
returns jsonb language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  i infrx.idempotency%rowtype;
  f infrx.feedback%rowtype;
begin
  select * into i from infrx.idempotency where org_id = p_org
     and operation = p_idem->>'operation' and key = p_idem->>'key';
  select * into f from infrx.feedback where feedback_id = i.feedback_id;
  if not found or f.calibration_set <> p_label then
    perform infrx.refuse('idempotency_conflict', 'the key already belongs to another operation');
  end if;
  if i.payload_digest <> p_idem->>'payload_hash' then
    perform infrx.refuse('idempotency_conflict', 'same key, different payload');
  end if;
  return infrx.feedback_json(f);
end $$;

-- Serializes one scope and answers whether it is still free. A concurrent duplicate waits
-- here for the first transaction and then finds its key, so it replays that row instead of
-- writing a second one. (The key row itself is written after the feedback row: 0003's
-- `idempotency_feedback_belongs_to_org` references it.)
create or replace function infrx.feedback_claim(p_org uuid, p_idem jsonb) returns boolean
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
begin
  perform pg_advisory_xact_lock(hashtextextended(
    p_org::text || '/' || (p_idem->>'operation') || '/' || (p_idem->>'key'), 0));
  return not exists (select 1 from infrx.idempotency where org_id = p_org
                     and operation = p_idem->>'operation' and key = p_idem->>'key');
end $$;

-- ======================================================================= RPCs ===
create or replace function infrx.accept_feedback(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_org uuid;
  v_job uuid;
  v_body jsonb := p_args->'body';
  v_value jsonb := p_args->'body'->'value';
  f infrx.feedback%rowtype;
begin
  perform infrx.feedback_enabled();
  if jsonb_typeof(v_body) is distinct from 'object'
     or exists (select 1 from jsonb_object_keys(v_body) k
                where k not in ('name', 'value', 'comment'))
     or p_args->'idem'->>'key' is null then
    perform infrx.refuse('invalid_request', 'feedback takes one {name, value, comment} signal '
                         'with an idempotency key; provenance is the server''s');
  end if;
  v_org := (p_args->>'org_id')::uuid;
  v_job := (p_args->>'request_id')::uuid;
  if not exists (select 1 from infrx.jobs j where j.request_id = v_job and j.org_id = v_org)
  then
    perform infrx.refuse('not_found', 'no request ' || v_job || ' owned by org ' || v_org);
  end if;
  if (select o.suspended from public.organizations o where o.id = v_org) then
    perform infrx.refuse('org_suspended', 'org ' || v_org || ' is suspended');
  end if;
  if not infrx.feedback_claim(v_org, p_args->'idem') then
    return infrx.feedback_replay(v_org, p_args->'idem', false);
  end if;
  -- The relation's own checks refuse a missing or stored-only name (a `calibration_label`
  -- row must be an operator's: `feedback_calibration_is_one_fact`) and a mistyped value.
  begin
    insert into infrx.feedback (feedback_id, org_id, request_id, author_principal,
      author_role, channel, name, value_bool, value_int, value_text, comment, by_operator,
      idempotency_key)
    values (p_args->>'feedback_id', v_org, v_job, p_args->>'principal', 'customer',
      p_args->>'channel', v_body->>'name',
      case when jsonb_typeof(v_value) = 'boolean' then (v_value)::boolean end,
      case when jsonb_typeof(v_value) = 'number' and v_value::text ~ '^-?[0-9]+$'
           then (v_value)::int end,
      case when jsonb_typeof(v_value) = 'string' then v_value #>> '{}' end,
      v_body->>'comment', coalesce((p_args->>'by_operator')::boolean, false),
      p_args->'idem'->>'key')
    returning * into f;
  exception when check_violation or not_null_violation or numeric_value_out_of_range then
    perform infrx.refuse('invalid_request', 'the value does not match the signal''s type');
  end;
  insert into infrx.idempotency (org_id, operation, key, payload_digest, feedback_id)
  values (v_org, p_args->'idem'->>'operation', p_args->'idem'->>'key',
          p_args->'idem'->>'payload_hash', f.feedback_id);
  insert into infrx.outbox (event_id, aggregate_id, org_id, kind, payload, available_at)
  values (gen_random_uuid(), v_job, v_org, 'feedback_projection',
          jsonb_build_object('feedback_id', f.feedback_id), infrx.now());
  return infrx.feedback_json(f);
end $$;

create or replace function infrx.label_calibration(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_job uuid := (p_args->>'request_id')::uuid;
  v_org uuid;
  f infrx.feedback%rowtype;
begin
  perform infrx.feedback_enabled();
  if not coalesce((p_args->>'is_operator')::boolean, false) then
    perform infrx.refuse('forbidden', 'labelling a calibration set requires a platform operator');
  end if;
  if p_args->'idem'->>'key' is null then
    perform infrx.refuse('invalid_request', 'an idempotency key is required for a label');
  end if;
  select j.org_id into v_org from infrx.jobs j where j.request_id = v_job;
  if not found then
    perform infrx.refuse('not_found', 'no request ' || v_job);
  end if;
  if (p_args->'idem'->>'org_id')::uuid is distinct from v_org then
    perform infrx.refuse('forbidden', 'the idempotency scope must name the labelled row''s org');
  end if;
  if not infrx.feedback_claim(v_org, p_args->'idem') then
    return infrx.feedback_replay(v_org, p_args->'idem', true);
  end if;
  begin
    insert into infrx.feedback (feedback_id, org_id, request_id, author_principal,
      author_role, channel, name, value_text, comment, calibration_set, rubric_version,
      by_operator, idempotency_key)
    values (p_args->>'feedback_id', v_org, v_job, p_args->>'principal', 'operator',
      'console', 'calibration_label', p_args->>'label', p_args->>'comment', true,
      (p_args->>'rubric_version')::int, true, p_args->'idem'->>'key')
    returning * into f;
  exception when check_violation or not_null_violation or numeric_value_out_of_range
            or invalid_text_representation then
    perform infrx.refuse('invalid_request', 'a label is one of the vocabulary against a '
                         'rubric version in 1..1000');
  end;
  insert into infrx.idempotency (org_id, operation, key, payload_digest, feedback_id)
  values (v_org, p_args->'idem'->>'operation', p_args->'idem'->>'key',
          p_args->'idem'->>'payload_hash', f.feedback_id);
  insert into infrx.audit_entries (id, actor_principal, action, target_org_id, reason, after,
    idempotency_key)
  values (gen_random_uuid(), f.author_principal, 'calibration_label', v_org,
    'calibration label', jsonb_build_object('feedback_id', f.feedback_id, 'request_id', v_job,
      'label', f.value_text, 'rubric_version', f.rubric_version), f.idempotency_key);
  insert into infrx.outbox (event_id, aggregate_id, org_id, kind, payload, available_at)
  values (gen_random_uuid(), v_job, v_org, 'feedback_projection',
          jsonb_build_object('feedback_id', f.feedback_id, 'calibration_set', true,
                             'rubric_version', f.rubric_version), infrx.now());
  return infrx.feedback_json(f);
end $$;

create or replace function infrx.request_feedback(p_args jsonb) returns jsonb
language plpgsql stable security definer set search_path = infrx, public, pg_temp as $$
declare
  v_job uuid := (p_args->>'request_id')::uuid;
  v_org uuid := (p_args->>'org_id')::uuid;
begin
  if not exists (select 1 from infrx.jobs j where j.request_id = v_job
                 and (v_org is null or j.org_id = v_org)) then
    perform infrx.refuse('not_found', 'no request ' || v_job);
  end if;
  return coalesce((select jsonb_agg(infrx.feedback_json(f) order by f.entry_seq)
                     from infrx.feedback f
                    where f.request_id = v_job and (v_org is null or f.org_id = v_org)
                      and f.calibration_set = coalesce((p_args->>'calibration')::boolean,
                                                       false)), '[]');
end $$;

-- Grants: none here. 0004's default privileges give EXECUTE on each new `infrx` function to
-- service_role alone (the helpers read and write nothing the platform role cannot already);
-- `accept_feedback` keeps 0004's grants through `create or replace`.
