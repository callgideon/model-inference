-- Feedback scrub (wave-5 LW2, lane lab-sql; the trace-ship lane's T3 request): a trace
-- deletion reaches the durable feedback text, not only its projection. LOCAL-ONLY (R151):
-- never applied hosted; the number is the next free at merge.
--
-- 0028 made `infrx.feedback` immutable. The one change now allowed is the scrub: `comment`
-- to NULL and the free text of a `correction`/`comment` signal to '[scrubbed]' (its CHECK
-- needs a non-blank value; thumbs, ratings and calibration verdicts carry no free text and
-- are kept), made only inside `infrx.scrub_feedback`, which marks its own transaction. Every
-- other UPDATE, and every DELETE, is still refused (23514), as 0028's trigger did.
--
--   infrx.feedback_scrubs   one receipt per scrub that changed rows: org, request, the count,
--                           actor, reason, time (append-only).
--   infrx.scrub_feedback {org_id, request_id, actor, reason}: the org's own request (a job
--                           of that org, else `not_found`); scrubs every row of it, customer
--                           signals and operator labels' comments alike; answers
--                           {scrubbed: n}; a repeat scrubs nothing and writes no receipt.
--
-- ROLLBACK (this file alone): drop trigger feedback_immutable on infrx.feedback; re-create
--   it as 0028 does (forbid_update_delete); drop function infrx.scrub_feedback(jsonb),
--   infrx.feedback_guard(); drop table infrx.feedback_scrubs. Scrubbed text stays scrubbed.
--
-- Re-runnable: `if not exists`, `create or replace`, `drop ... if exists` before the re-add.

create table if not exists infrx.feedback_scrubs (
  scrub_id bigint generated always as identity primary key,
  org_id uuid not null references public.organizations(id) on delete restrict,
  request_id uuid not null,
  scrubbed int not null check (scrubbed >= 1),
  actor text not null check (length(btrim(actor)) between 1 and 200),
  reason text not null check (length(btrim(reason)) between 1 and 500),
  at timestamptz not null default infrx.now()
);
create or replace trigger feedback_scrubs_immutable before update or delete
  on infrx.feedback_scrubs for each row execute function infrx.forbid_update_delete();
create or replace trigger feedback_scrubs_no_truncate before truncate
  on infrx.feedback_scrubs for each statement execute function infrx.forbid_truncate();
alter table infrx.feedback_scrubs enable row level security;
revoke all on infrx.feedback_scrubs from public, anon, authenticated, service_role;
grant select on infrx.feedback_scrubs to service_role;

create or replace function infrx.feedback_guard() returns trigger
language plpgsql set search_path = infrx, public, pg_temp as $$
begin
  if tg_op = 'UPDATE' and current_setting('infrx.feedback_scrub', true) = 'on'
     and new.comment is null
     and new.value_text is not distinct from (case when old.name in ('correction', 'comment')
                                                   then '[scrubbed]' else old.value_text end)
     and to_jsonb(new) - 'comment' - 'value_text' = to_jsonb(old) - 'comment' - 'value_text'
  then
    return new;
  end if;
  raise exception 'feedback % is immutable: only a scrub removes its text', old.feedback_id
    using errcode = '23514';
end $$;

drop trigger if exists feedback_immutable on infrx.feedback;
create trigger feedback_immutable before update or delete on infrx.feedback
  for each row execute function infrx.feedback_guard();

create or replace function infrx.scrub_feedback(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_org uuid := (p_args->>'org_id')::uuid;
  v_job uuid := (p_args->>'request_id')::uuid;
  n int;
begin
  if not exists (select 1 from infrx.jobs j where j.request_id = v_job and j.org_id = v_org)
  then
    perform infrx.refuse('not_found', 'no request ' || v_job || ' owned by org ' || v_org);
  end if;
  perform set_config('infrx.feedback_scrub', 'on', true);
  update infrx.feedback set comment = null,
    value_text = case when name in ('correction', 'comment') then '[scrubbed]'
                      else value_text end
   where org_id = v_org and request_id = v_job
     and (comment is not null
          or (name in ('correction', 'comment') and value_text <> '[scrubbed]'));
  get diagnostics n = row_count;
  perform set_config('infrx.feedback_scrub', '', true);
  if n > 0 then
    begin
      insert into infrx.feedback_scrubs (org_id, request_id, scrubbed, actor, reason)
      values (v_org, v_job, n, p_args->>'actor', p_args->>'reason');
    exception when check_violation or not_null_violation then
      perform infrx.refuse('invalid_request', 'a scrub names its actor and reason');
    end;
  end if;
  return jsonb_build_object('scrubbed', n);
end $$;
