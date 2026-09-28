-- SR-C3L-1 (wave-5 LW2 request from the judge lane's C3L; lane lab-sql, D6J): the four
-- authenticated doors the Lab's judge actions call with the user's own session
-- (`apps/lab/lib/services/judge/core.ts`). The Lab holds no service-role key (R171); PostgREST
-- exposes `public` only, never `infrx`. LOCAL-ONLY (R151): never applied hosted; the number is
-- the next free at merge.
--
-- Every door: SECURITY DEFINER; the identity is auth.uid(), never an argument; currency is
-- the database clock (infrx.now()); D6J's flag `lab_submission` gates all four (off or absent:
-- 55000, which the Lab shows as "unavailable" - so the launched App's database answers
-- nothing here until the flag is set); a caller the rules refuse gets 42501, an unknown or
-- another provider's row P0002 (the Lab maps both to "denied" and shows no data).
--
--   lab_judge_configs    a provider's judge configuration: grantor, model, judge model,
--                        rubric version, sample size 1..200 (J1's scan bound), created by.
--   lab_judge_requests   a run the Lab queued for the J2 worker: run id (minted when the form
--                        renders, so a double click repeats it), config, payer, requester.
--
--   public.lab_judge_configure(p_provider_org_id, p_grantor_org_id, p_model_id, p_judge_model,
--       p_rubric_version, p_sample_size): a CURRENT developer+ membership AND the grantor's
--       current grant to this provider naming the model, over request_content and
--       response_content, for external_judging -> the config row.
--   public.lab_judge_set_budget(p_provider_org_id, p_payer_ref, p_limit {unit, value}):
--       administrator; the payer ref is this provider's; PROVIDER_USD only; D6J's
--       `lab_put_budget` (the one budget row, versioned limits) -> the budget.
--   public.lab_judge_request_run(p_provider_org_id, p_run_id, p_config_id, p_payer_ref):
--       developer+ and the config's grant still current; idempotent on p_run_id (the same
--       provider gets the stored request; another provider's run id is P0002).
--   public.lab_judge_calibration(p_provider_org_id, p_after, p_limit): developer+; the
--       provider's own runs' judge labels (0036 `lab_judge_results`), keyset on label_id,
--       at most min(p_limit, 50) rows, and only while the run's grant is still current
--       for external_judging. Only the label (accepted, overall_pass, limited, each
--       criterion's name and score) - never a rationale, notes or Rejected.detail, which are
--       judge text about the grantor's content.
--
-- ROLLBACK (this file alone; nothing earlier references it): drop function
--   public.lab_judge_calibration(uuid, uuid, int),
--   public.lab_judge_request_run(uuid, uuid, uuid, text),
--   public.lab_judge_set_budget(uuid, text, jsonb),
--   public.lab_judge_configure(uuid, uuid, uuid, text, int, int),
--   infrx.lab_judge_door(uuid, text), infrx.lab_judge_granted(uuid, uuid, uuid); drop table
--   infrx.lab_judge_requests, infrx.lab_judge_configs.
--
-- Re-runnable: `if not exists`, `create or replace`.

create table if not exists infrx.lab_judge_configs (
  config_id uuid primary key default gen_random_uuid(),
  provider_org_id uuid not null references infrx.provider_orgs on delete restrict,
  grantor_org_id uuid not null references public.organizations(id) on delete restrict,
  model_id uuid not null,
  judge_model text not null check (judge_model ~ '^[a-z0-9][a-z0-9.-]{0,63}$'),
  rubric_version int not null check (rubric_version between 1 and 1000),
  sample_size int not null check (sample_size between 1 and 200),
  created_by uuid not null,
  created_at timestamptz not null default infrx.now()
);

create table if not exists infrx.lab_judge_requests (
  run_id uuid primary key,
  provider_org_id uuid not null references infrx.provider_orgs on delete restrict,
  config_id uuid not null references infrx.lab_judge_configs on delete restrict,
  payer_ref text not null,
  requested_by uuid not null,
  requested_at timestamptz not null default infrx.now()
);

do $$
declare
  t text;
begin
  foreach t in array array['lab_judge_configs', 'lab_judge_requests'] loop
    execute format('create or replace trigger %I before update or delete on infrx.%I '
                   'for each row execute function infrx.forbid_update_delete()',
                   t || '_immutable', t);
    execute format('create or replace trigger %I before truncate on infrx.%I '
                   'for each statement execute function infrx.forbid_truncate()',
                   t || '_no_truncate', t);
    execute format('alter table infrx.%I enable row level security', t);
    execute format('revoke all on infrx.%I from public, anon, authenticated, service_role', t);
    execute format('grant select on infrx.%I to service_role', t);
  end loop;
end $$;

-- The flag, then auth.uid()'s CURRENT membership of the provider at `p_min` or above; else
-- 42501 (no workspace, a revoked membership and a lower role are one answer).
create or replace function infrx.lab_judge_door(p_provider uuid, p_min text)
returns void language plpgsql security definer set search_path = infrx, public, pg_temp as $$
begin
  perform infrx.require_feature('lab_submission');
  if not exists (select 1 from infrx.provider_memberships m
                  where m.provider_org_id = p_provider and m.user_id = auth.uid()
                    and m.granted_at <= infrx.now()
                    and (m.revoked_at is null or infrx.now() < m.revoked_at)
                    and array_position(array['viewer', 'developer', 'administrator'], m.role)
                        >= array_position(array['viewer', 'developer', 'administrator'], p_min))
  then
    raise exception 'this provider role does not hold the judge action'
      using errcode = '42501';
  end if;
end $$;

-- The grantor's current grant to the provider names the model, over the question and the
-- answer, for external judging.
create or replace function infrx.lab_judge_granted(p_provider uuid, p_grantor uuid,
                                                   p_model uuid)
returns boolean language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select coalesce((select g.effective_at <= infrx.now()
                          and g.revoked_at is null
                          and (g.expires_at is null or infrx.now() < g.expires_at)
                          and p_model::text = any(g.model_ids)
                          and g.categories @> array['request_content', 'response_content']
                          and 'external_judging' = any(g.purposes)
                     from infrx.lab_access_grants g
                    where g.grantor_org_id = p_grantor
                      and g.recipient_provider_org_id = p_provider
                    order by g.version desc limit 1), false)
$$;

create or replace function public.lab_judge_configure(p_provider_org_id uuid,
  p_grantor_org_id uuid, p_model_id uuid, p_judge_model text, p_rubric_version int,
  p_sample_size int)
returns jsonb language plpgsql security definer set search_path = public, infrx, pg_temp as $$
declare
  c infrx.lab_judge_configs%rowtype;
begin
  perform infrx.lab_judge_door(p_provider_org_id, 'developer');
  if not infrx.lab_judge_granted(p_provider_org_id, p_grantor_org_id, p_model_id) then
    raise exception 'no current external_judging grant covers this model'
      using errcode = '42501';
  end if;
  begin
    insert into infrx.lab_judge_configs (provider_org_id, grantor_org_id, model_id,
      judge_model, rubric_version, sample_size, created_by)
    values (p_provider_org_id, p_grantor_org_id, p_model_id, p_judge_model,
      p_rubric_version, p_sample_size, auth.uid())
    returning * into c;
  exception when check_violation or not_null_violation then
    raise exception 'a judge model name, a rubric version 1..1000 and 1..200 samples'
      using errcode = '22023';
  end;
  return jsonb_build_object('config_id', c.config_id, 'grantor_org_id', c.grantor_org_id,
    'model_id', c.model_id, 'judge_model', c.judge_model, 'rubric_version', c.rubric_version,
    'sample_size', c.sample_size, 'created_at', c.created_at);
end $$;

create or replace function public.lab_judge_set_budget(p_provider_org_id uuid,
  p_payer_ref text, p_limit jsonb)
returns jsonb language plpgsql security definer set search_path = public, infrx, pg_temp as $$
begin
  perform infrx.lab_judge_door(p_provider_org_id, 'administrator');
  if (infrx.lab_ref_parts(p_payer_ref))[1:2] is distinct from
     array['payer', p_provider_org_id::text] then
    raise exception 'a budget is this provider''s own lab:payer ref' using errcode = '42501';
  end if;
  if p_limit->>'unit' is distinct from 'PROVIDER_USD' then
    raise exception 'a judge budget is PROVIDER_USD' using errcode = '22023';
  end if;
  return infrx.lab_put_budget(jsonb_build_object('provider_org_id', p_provider_org_id,
    'payer_ref', p_payer_ref, 'limit', p_limit->>'value', 'actor', auth.uid(),
    'reason', 'set in the Lab'));
end $$;

create or replace function public.lab_judge_request_run(p_provider_org_id uuid,
  p_run_id uuid, p_config_id uuid, p_payer_ref text)
returns jsonb language plpgsql security definer set search_path = public, infrx, pg_temp as $$
declare
  c infrx.lab_judge_configs%rowtype;
  r infrx.lab_judge_requests%rowtype;
begin
  perform infrx.lab_judge_door(p_provider_org_id, 'developer');
  select * into r from infrx.lab_judge_requests where run_id = p_run_id;
  if found then
    if r.provider_org_id <> p_provider_org_id then
      raise exception 'no such judge run' using errcode = 'P0002';
    end if;
  else
    select * into c from infrx.lab_judge_configs
     where config_id = p_config_id and provider_org_id = p_provider_org_id;
    if not found then
      raise exception 'no such judge configuration' using errcode = 'P0002';
    end if;
    if (infrx.lab_ref_parts(p_payer_ref))[1:2] is distinct from
       array['payer', p_provider_org_id::text] then
      raise exception 'a run is paid by this provider''s own payer' using errcode = '42501';
    end if;
    if not infrx.lab_judge_granted(p_provider_org_id, c.grantor_org_id, c.model_id) then
      raise exception 'no current external_judging grant covers this model'
        using errcode = '42501';
    end if;
    insert into infrx.lab_judge_requests (run_id, provider_org_id, config_id, payer_ref,
      requested_by)
    values (p_run_id, p_provider_org_id, p_config_id, p_payer_ref, auth.uid())
    on conflict (run_id) do nothing
    returning * into r;
    if r.run_id is null then                   -- a concurrent double click won
      select * into r from infrx.lab_judge_requests where run_id = p_run_id;
      if r.provider_org_id <> p_provider_org_id then
        raise exception 'no such judge run' using errcode = 'P0002';
      end if;
    end if;
  end if;
  return jsonb_build_object('run_id', r.run_id, 'config_id', r.config_id,
    'payer_ref', r.payer_ref, 'requested_at', r.requested_at);
end $$;

create or replace function public.lab_judge_calibration(p_provider_org_id uuid,
  p_after uuid, p_limit int)
returns jsonb language plpgsql stable security definer set search_path = public, infrx, pg_temp as $$
begin
  perform infrx.lab_judge_door(p_provider_org_id, 'developer');
  return (select coalesce(jsonb_agg(jsonb_build_object('label_id', x.label_id,
            'run_id', x.run_id, 'sample_id', x.sample_id, 'rubric_version', x.rubric_version,
            'accepted', x.accepted, 'overall_pass', x.result->'overall_pass',
            'limited', x.result->'limited',
            'scores', (select coalesce(jsonb_agg(jsonb_build_object('name', s->'name',
                                                                    'score', s->'score')), '[]')
                         from jsonb_array_elements(coalesce(x.result->'scores', '[]')) s))
            order by x.label_id), '[]')
    from (select l.* from infrx.lab_judge_results l
            join infrx.lab_judge_runs r on r.run_id = l.run_id
           where r.provider_org_id = p_provider_org_id
             and infrx.lab_grant_current(r.grant_id, 'external_judging')
             and (p_after is null or l.label_id > p_after)
           order by l.label_id
           limit least(greatest(coalesce(p_limit, 50), 0), 50)) x);
end $$;

revoke all on function public.lab_judge_configure(uuid, uuid, uuid, text, int, int),
  public.lab_judge_set_budget(uuid, text, jsonb),
  public.lab_judge_request_run(uuid, uuid, uuid, text),
  public.lab_judge_calibration(uuid, uuid, int) from public, anon;
grant execute on function public.lab_judge_configure(uuid, uuid, uuid, text, int, int),
  public.lab_judge_set_budget(uuid, text, jsonb),
  public.lab_judge_request_run(uuid, uuid, uuid, text),
  public.lab_judge_calibration(uuid, uuid, int) to authenticated;
