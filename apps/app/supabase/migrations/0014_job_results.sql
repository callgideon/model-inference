-- D2 item 4: the immutable result object (W2 integration request 4; 02 §7; R30).
--
-- `02` §7 stores the result BEFORE the settling transaction and R30 makes a success need
-- its reference, so the reference must name something durable. This is that object,
-- in PostgreSQL: one row per job, tenant-bound by the composite key, written once.
-- `put_result` answers `infrx-result:<request_id>` (the shape `tests/d/checks.py` already
-- uses for `jobs.result_ref`); the same text again is a replay, different text is a
-- `state_conflict` - first write wins, so a late second writer cannot replace what the
-- customer may already have fetched. `read_result` is the ownership-checked read.
--
-- Bounded at the per-job journal reservation (16 MiB): a result is at most what the job
-- was allowed to publish. Retention follows `jobs.result_expires_at` (D5 sets it; purging
-- expired results is an operations job, not built here). Additive and re-runnable.

create table if not exists infrx.job_results (
  request_id uuid primary key,
  org_id uuid not null,
  digest text not null check (digest ~ '^sha256:[0-9a-f]{64}$'),
  bytes int not null check (bytes between 0 and 16777216),
  body text not null check (octet_length(body) <= 16777216),
  created_at timestamptz not null default infrx.now(),
  constraint job_results_job_fk foreign key (request_id, org_id)
    references infrx.jobs (request_id, org_id) on delete restrict,
  constraint job_results_bytes_exact check (bytes = octet_length(body))
);
alter table infrx.job_results enable row level security;
revoke all on infrx.job_results from public, anon, authenticated, service_role;
do $$
begin
  if not exists (select 1 from pg_trigger where tgname = 'job_results_immutable') then
    create trigger job_results_immutable before update or delete on infrx.job_results
      for each row execute function infrx.forbid_update_delete();
    create trigger infrx_job_results_no_truncate before truncate on infrx.job_results
      for each statement execute function infrx.forbid_truncate();
  end if;
end $$;

-- Args `{job_id, text}`; answers the reference.
create or replace function infrx.put_result(p_args jsonb) returns text
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_job uuid := (p_args->>'job_id')::uuid;
  v_text text := p_args->>'text';
  v_digest text;
  v_org uuid;
  r infrx.job_results%rowtype;
begin
  if v_text is null then
    perform infrx.refuse('invalid_request', 'put_result takes {job_id, text}');
  end if;
  select org_id into v_org from infrx.jobs where request_id = v_job;
  if not found then
    perform infrx.refuse('not_found', 'no job ' || v_job);
  end if;
  v_digest := 'sha256:' || encode(sha256(convert_to(v_text, 'UTF8')), 'hex');
  insert into infrx.job_results (request_id, org_id, digest, bytes, body)
  values (v_job, v_org, v_digest, octet_length(v_text), v_text)
  on conflict (request_id) do nothing;
  select * into r from infrx.job_results where request_id = v_job;
  if r.digest <> v_digest then
    perform infrx.refuse('state_conflict', 'job ' || v_job
                         || ' already stored a different result');
  end if;
  return 'infrx-result:' || v_job;
end $$;

-- The owner's read of a result by its reference; anything else is not_found.
create or replace function infrx.read_result(p_org uuid, p_ref text) returns text
language plpgsql stable security definer set search_path = infrx, public, pg_temp as $$
declare
  v_body text;
begin
  select r.body into v_body from infrx.job_results r
   where p_ref ~ '^infrx-result:[0-9a-f-]{36}$'
     and r.request_id = substr(p_ref, 14)::uuid and r.org_id = p_org;
  if not found then
    perform infrx.refuse('not_found', 'no result ' || coalesce(p_ref, ''));
  end if;
  return v_body;
end $$;

revoke all on function infrx.put_result(jsonb) from public, anon, authenticated;
revoke all on function infrx.read_result(uuid, text) from public, anon, authenticated;
grant execute on function infrx.put_result(jsonb) to service_role;
grant execute on function infrx.read_result(uuid, text) to service_role;
