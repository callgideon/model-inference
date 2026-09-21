-- TEST FIXTURE, NEVER A MIGRATION (see `supabase_shim.sql` for why this directory).
--
-- R48/S1 note 3: every conformance case drives `harness.clock`, and R7 says the
-- adapter reads the *database* clock inside its transaction. Both hold only if the
-- database's notion of now is movable by a test and immovable in production, because
-- a clock a deployed process can move is a way to release an unknown-usage hold
-- early, expire an idempotency tombstone, or claim a job past its queue deadline.
--
-- `infrx.now()` (migration 0004) therefore reads its offset from `infrx_test.clock`,
-- behind two independent barriers:
--
--   1. the relation is created by THIS file, which is not in
--      `apps/app/supabase/migrations/` and so is never applied by any deploy;
--   2. `infrx.now()` does not even look for it unless `current_database()` is a
--      task-local `infrx_<task>` database (08 §8). Production is Supabase's
--      `postgres`, and a session cannot rename the database it is connected to.
--
-- The offset is a table rather than a session GUC on purpose: the conformance
-- harness's hooks are synchronous on one connection while the port operations run on
-- a lazily opened async pool (S1 note 2), so a per-session setting would move the
-- clock for the hook and for nothing else.

create schema if not exists infrx_test;

create table if not exists infrx_test.clock (
  id boolean primary key default true check (id),
  offset_s interval not null default interval '0'
);
insert into infrx_test.clock (id) values (true) on conflict (id) do nothing;

-- `advance(seconds)` matches `FakeClock.advance`; `set_offset` is the absolute form.
create or replace function infrx_test.advance(p_seconds double precision)
returns timestamptz language sql as $$
  update infrx_test.clock set offset_s = offset_s + make_interval(secs => p_seconds)
  where id returning infrx.now();
$$;

create or replace function infrx_test.set_offset(p_seconds double precision)
returns timestamptz language sql as $$
  update infrx_test.clock set offset_s = make_interval(secs => p_seconds)
  where id returning infrx.now();
$$;

grant usage on schema infrx_test to service_role;
grant select, update on infrx_test.clock to service_role;
grant execute on function infrx_test.advance(double precision) to service_role;
grant execute on function infrx_test.set_offset(double precision) to service_role;
