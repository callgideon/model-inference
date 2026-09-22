-- D2: expiry and garbage collection of outbox rows (D-durable-state §D2; 02 §2).
--
-- The outbox is at-least-once and consumers never delete (0003: "an acknowledgment cannot
-- erase source truth"). So rows are removed only here, by the platform, and only when no
-- consumer can still need them:
--
--   expire   a DISPATCH row of a terminal job that nobody acknowledged is acknowledged as
--            `expired: job terminal` - there is nothing left to dispatch (the relay would
--            also ack it as superseded when it next read it; this does not wait for that).
--   delete   an ACKNOWLEDGED row older than `retention_s` whose aggregate is not a live job.
--            Never an unacknowledged row (a projection consumer may still need it), never a
--            live job's row (its latest dispatch event is the rebuild's stable id), never a
--            `callback_delivery` row (its deliveries reference it).
--
-- Bounded: at most `limit` rows per call, oldest first, `skip locked` so a relay in flight
-- is never blocked. Additive and re-runnable.

create index if not exists outbox_acknowledged_idx on infrx.outbox (acknowledged_at)
  where acknowledged_at is not null;

-- Args `{retention_s, limit}`; answers `{"expired": n, "deleted": n}`.
create or replace function infrx.gc_outbox(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_now timestamptz := infrx.now();
  v_retention float8 := (p_args->>'retention_s')::float8;
  v_limit int := greatest(1, least(coalesce((p_args->>'limit')::int, 1000), 10000));
  v_expired int;
  v_deleted int;
begin
  if v_retention is null or v_retention < 0 then
    perform infrx.refuse('invalid_request', 'gc_outbox takes a nonnegative retention_s');
  end if;
  with stale as (
    select o.event_id from infrx.outbox o
      join infrx.jobs j on j.request_id = o.aggregate_id
     where o.acknowledged_at is null
       and o.kind in ('prepare_dispatch', 'inference_dispatch')
       and j.settled_at is not null
     order by o.available_at, o.event_id
     limit v_limit
     for update of o skip locked),
  acked as (
    update infrx.outbox o set acknowledged_at = v_now, last_error = 'expired: job terminal'
      from stale where o.event_id = stale.event_id
    returning 1)
  select count(*) into v_expired from acked;
  with old as (
    select o.event_id from infrx.outbox o
     where o.acknowledged_at is not null
       and o.acknowledged_at < v_now - make_interval(secs => v_retention)
       and o.kind <> 'callback_delivery'
       and not exists (select 1 from infrx.jobs j where j.request_id = o.aggregate_id
                       and j.settled_at is null)
     order by o.acknowledged_at, o.event_id
     limit v_limit
     for update of o skip locked),
  gone as (
    delete from infrx.outbox o using old where o.event_id = old.event_id returning 1)
  select count(*) into v_deleted from gone;
  return jsonb_build_object('expired', v_expired, 'deleted', v_deleted);
end $$;

revoke all on function infrx.gc_outbox(jsonb) from public, anon, authenticated;
grant execute on function infrx.gc_outbox(jsonb) to service_role;
