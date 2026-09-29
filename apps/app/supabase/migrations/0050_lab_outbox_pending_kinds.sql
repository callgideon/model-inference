-- WR-LSQ-C2B (lane lab-sql-lw6; COMPOSITION-2-b790f17.md "requests filed"): each Lab worker
-- role's outbox relay claims only its own event kinds.
-- LOCAL-ONLY (R151/R201): never applied hosted; the number is the next free at merge.
--
-- `0029_lab_data.sql`'s `lab_outbox_pending` claims the oldest available rows of EVERY kind.
-- Composition-2's evidence: "today an eval role claims a `checkpoint_received` event,
-- records an error and it is redelivered after 30 s - correct (nothing is lost, D2's
-- OutboxRelay retries), but noisy". With two roles pumping the same relay-shaped outbox
-- (`eval_run` for the eval worker, `checkpoint_received` for the checkpoints worker), every
-- role should claim only the kinds its own `OutboxRelay` is built to enqueue.
--
-- Additive: `kinds` is OPTIONAL and defaults to every kind (the current, one-outbox-for-all
-- behaviour), so `PgLabDataStore.dispatch_pending`'s existing callers are unchanged unless
-- they opt in.
--
--   lab_outbox_pending {..., kinds?: [...]}: as before, plus - when `kinds` is given and
--       non-empty - `kind = any(kinds)` in the claim's own `where`, so a role's relay never
--       claims, times out on, and redelivers another role's event kind.
--
-- ROLLBACK (this file alone): `create or replace function infrx.lab_outbox_pending` back to
-- 0029's body verbatim (that file is immutable history; this migration's only edit is the
-- `kinds` clause added below, so reverting means dropping exactly that clause).
--
-- Re-runnable: `create or replace`.

create or replace function infrx.lab_outbox_pending(p_args jsonb) returns jsonb
language sql security definer set search_path = infrx, public, pg_temp as $$
  with picked as (
    select o.event_id from infrx.lab_outbox o
     where o.acknowledged_at is null and o.available_at <= infrx.now()
       and (o.claimed_at is null or o.claimed_at
            <= infrx.now() - make_interval(secs => (p_args->>'redelivery_s')::float8))
       and (p_args->'kinds' is null or jsonb_array_length(p_args->'kinds') = 0
            or o.kind = any(array(select jsonb_array_elements_text(p_args->'kinds'))))
     order by o.available_at, o.event_id limit (p_args->>'limit')::int
     for update skip locked),
  claimed as (
    update infrx.lab_outbox o set claimed_at = infrx.now(), claimed_by = p_args->>'worker_id',
                                  attempts = o.attempts + 1
      from picked where o.event_id = picked.event_id
    returning o.*)
  select coalesce(jsonb_agg(jsonb_build_object('event_id', event_id, 'kind', kind,
    'provider_org_id', provider_org_id, 'payload', payload) order by available_at, event_id),
    '[]') from claimed
$$;
