-- LOCAL-ONLY (R151/R201/R271): never applied hosted; 0067 is allocated by the coordinator
-- (merge #95) to api-judge-2's schema request SR-AP08-1, moved here unchanged from
-- apps/infrx-api/tests/ap08/sr_ap08_1.sql (evidence/w7/api-judge-2-e354988.md). One coordinator
-- line follows it (WR-AP09L-3): EXECUTE on 0043's `public.lab_judge_runs(uuid, uuid)` for the
-- Lab control login, so `GET /lab/v1/traces/{id}/judge-runs` runs the door as the session user
-- on that login (0043 granted it to `authenticated` only; the door's own role check is unchanged).
--
-- ROLLBACK (this file alone; nothing earlier references it; tests/d/test_control_ops_upgrade.py runs these lines):
-- rollback: drop function if exists public.lab_judge_rubric_create(jsonb), public.lab_judge_rubric_list(uuid), infrx.lab_judge_rubric_of(jsonb), infrx.lab_judge_results_of(jsonb), infrx.lab_judge_rubric_json(infrx.lab_judge_rubrics);
-- rollback: drop table if exists infrx.lab_judge_rubrics;
-- rollback: revoke execute on function public.lab_judge_runs(uuid, uuid) from infrx_lab_control;
--
-- SR-AP08-1 (api-judge-2, AP-08): the SCHEMA REQUEST's exact DDL, applied by `tests/ap08`'s
-- PostgreSQL world after 0001..0064 so the request is proven before it is allocated (R271:
-- no lane but the schema owner writes under migrations/). The coordinator moves this text,
-- unchanged, into the allocated LOCAL-ONLY migration (0066 or an amended 0064) and drops the
-- `sr_sql()` hook in `tests/ap08/conftest.py`.
--
-- What it adds:
--   infrx.lab_judge_rubrics: one immutable row per rubric version (1..1000, the integer a
--       configuration pins), its definition (`infrx.judge.rubric.definition`), digest, the
--       review it passed and the operator who stored it. Append-only (0064's triggers).
--   public.lab_judge_rubric_create(p_args {rubric_version, rubric_id, definition, digest,
--       review_ref}): a platform operator only (`profiles.is_operator`, 42501 otherwise); the
--       definition names the same version and id; once per version - the same digest again
--       is the stored row (`replayed`), another `idempotency_conflict`.
--   public.lab_judge_rubric_list(p_provider_org_id): the stored versions, to a CURRENT viewer+
--       of that provider (0037's `lab_judge_door`).
--   infrx.lab_judge_rubric_of(p_args {run_id}): the judge worker's read - the run's pinned
--       version and its stored definition (null for a code version); null for no such run.
--   infrx.lab_judge_results_of(p_args {provider_org_id, grantor_org_id, judge_model,
--       rubric_version, limit}): the calibration pass's read - the stored results of that
--       configuration's COMPLETED runs whose judging grant is still current, oldest first,
--       at most `limit` (1..1000). Sample ids and judge output only; no trace content.
-- EXECUTE: the two public doors to the Lab control login only; the infrx reads keep 0004's
-- platform default (the worker's login); PUBLIC, anon and authenticated get nothing (R271).
--
-- ROLLBACK: drop the four functions; drop table infrx.lab_judge_rubrics.

create table if not exists infrx.lab_judge_rubrics (
  rubric_version int primary key check (rubric_version between 1 and 1000),
  rubric_id text not null check (rubric_id ~ '^[a-z0-9][a-z0-9.-]{0,63}$'),
  definition jsonb not null check (jsonb_typeof(definition) = 'object'),
  digest text not null check (digest ~ '^sha256:[0-9a-f]{64}$'),
  review_ref text not null check (length(btrim(review_ref)) between 1 and 400),
  created_by uuid not null,
  created_at timestamptz not null default infrx.now(),
  constraint lab_judge_rubrics_names_itself
    check ((definition->>'version')::int = rubric_version and definition->>'rubric_id' = rubric_id)
);

create or replace trigger lab_judge_rubrics_immutable before update or delete
  on infrx.lab_judge_rubrics for each row execute function infrx.forbid_update_delete();
create or replace trigger lab_judge_rubrics_no_truncate before truncate
  on infrx.lab_judge_rubrics for each statement execute function infrx.forbid_truncate();
alter table infrx.lab_judge_rubrics enable row level security;
revoke all on infrx.lab_judge_rubrics from public, anon, authenticated, service_role;
grant select on infrx.lab_judge_rubrics to service_role;

create or replace function infrx.lab_judge_rubric_json(r infrx.lab_judge_rubrics) returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select jsonb_build_object('rubric_version', r.rubric_version, 'rubric_id', r.rubric_id,
    'definition', r.definition, 'digest', r.digest, 'review_ref', r.review_ref,
    'created_by', r.created_by, 'created_at', r.created_at)
$$;

create or replace function public.lab_judge_rubric_create(p_args jsonb)
returns jsonb language plpgsql security definer set search_path = public, infrx, pg_temp as $$
declare
  r infrx.lab_judge_rubrics%rowtype;
  v_version int;
begin
  if not coalesce((select p.is_operator from public.profiles p where p.id = auth.uid()), false)
  then
    raise exception 'only a platform operator stores a rubric version' using errcode = '42501';
  end if;
  begin
    v_version := (p_args->>'rubric_version')::int;
    insert into infrx.lab_judge_rubrics (rubric_version, rubric_id, definition, digest,
      review_ref, created_by)
    values (v_version, p_args->>'rubric_id', p_args->'definition', p_args->>'digest',
      p_args->>'review_ref', auth.uid())
    on conflict (rubric_version) do nothing
    returning * into r;
  exception when check_violation or not_null_violation or invalid_text_representation
              or numeric_value_out_of_range then
    raise exception 'a rubric version 1..1000, its id, a definition naming both, a sha256 '
                    'digest and a review reference' using errcode = '22023';
  end;
  if r.rubric_version is not null then
    return infrx.lab_judge_rubric_json(r) || jsonb_build_object('replayed', false);
  end if;
  select * into r from infrx.lab_judge_rubrics where rubric_version = v_version;
  if r.digest is distinct from p_args->>'digest' then
    perform infrx.refuse('idempotency_conflict',
                         'this rubric version is stored with another definition');
  end if;
  return infrx.lab_judge_rubric_json(r) || jsonb_build_object('replayed', true);
end $$;

create or replace function public.lab_judge_rubric_list(p_provider_org_id uuid)
returns jsonb language plpgsql stable security definer set search_path = public, infrx, pg_temp as $$
begin
  perform infrx.lab_judge_door(p_provider_org_id, 'viewer');
  return (select coalesce(jsonb_agg(infrx.lab_judge_rubric_json(r) order by r.rubric_version),
                          '[]') from infrx.lab_judge_rubrics r);
end $$;

create or replace function infrx.lab_judge_rubric_of(p_args jsonb) returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select jsonb_build_object('rubric_version', c.rubric_version, 'definition', r.definition)
    from infrx.lab_judge_requests q
    join infrx.lab_judge_configs c on c.config_id = q.config_id
    left join infrx.lab_judge_rubrics r on r.rubric_version = c.rubric_version
   where q.run_id = (p_args->>'run_id')::uuid
$$;

create or replace function infrx.lab_judge_results_of(p_args jsonb) returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select coalesce(jsonb_agg(jsonb_build_object('run_id', x.run_id, 'sample_id', x.sample_id,
           'rubric_version', x.rubric_version, 'accepted', x.accepted, 'result', x.result)
           order by x.recorded_at, x.label_id), '[]')
    from (select l.* from infrx.lab_judge_results l
            join infrx.lab_judge_runs j on j.run_id = l.run_id and j.state = 'completed'
            join infrx.lab_judge_requests q on q.run_id = l.run_id
            join infrx.lab_judge_configs c on c.config_id = q.config_id
           where q.provider_org_id = (p_args->>'provider_org_id')::uuid
             and c.grantor_org_id = (p_args->>'grantor_org_id')::uuid
             and c.judge_model = p_args->>'judge_model'
             and c.rubric_version = (p_args->>'rubric_version')::int
             and l.rubric_version = c.rubric_version
             and infrx.lab_grant_current(j.grant_id, 'external_judging')
           order by l.recorded_at, l.label_id
           limit least(greatest(coalesce((p_args->>'limit')::int, 1000), 1), 1000)) x
$$;

revoke all on function infrx.lab_judge_rubric_json(infrx.lab_judge_rubrics),
  public.lab_judge_rubric_create(jsonb), public.lab_judge_rubric_list(uuid),
  infrx.lab_judge_rubric_of(jsonb), infrx.lab_judge_results_of(jsonb)
  from public, anon, authenticated;
grant execute on function public.lab_judge_rubric_create(jsonb),
  public.lab_judge_rubric_list(uuid) to infrx_lab_control;

-- WR-AP09L-3 (coordinator, merge #95): the per-request judge read on the Lab control login.
grant execute on function public.lab_judge_runs(uuid, uuid) to infrx_lab_control;
