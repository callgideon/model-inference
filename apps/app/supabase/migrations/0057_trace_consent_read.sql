-- WR-LC-HOSTED (lane lab-capture-2, R250): the trace consent read for 0021's dedicated runtime
-- login. `gateway.capture.ConsentSource` reads a key's own opt-in (`api_keys.trace_mode`, null =
-- off) and its organization's consent head (the highest `consent_history` version, revoked or
-- not: a revoked head is off, never a fall-back to an older consent) through this function;
-- `infrx_runtime` reads neither table, so without it capture fails closed to off on that login.
-- Additive: one function; no table, column, constraint or existing grant is touched.
-- LOCAL-ONLY (R150/R151/R201): never applied hosted; the number is the next free at merge.
--
--   trace_consent(p_org, p_key): at most one row, for a key of `p_org` only (a key named under
--       another organization reads nothing): the key's mode, then the head's version, mode,
--       retention days, evaluation consent, effective and revoked times (all null without a
--       consent row).
--
-- RPC: SECURITY DEFINER; EXECUTE for infrx_runtime, and service_role (the pilot pool) through
-- 0004's defaults; never PUBLIC or a browser role.
--
-- ROLLBACK (this file alone; nothing in SQL references it): drop function
-- infrx.trace_consent(uuid, uuid). The gateway then reads off (fails closed).
--
-- Re-runnable: `create or replace`, grants are idempotent.

create or replace function infrx.trace_consent(p_org uuid, p_key uuid)
returns table (key_trace_mode text, consent_version int, trace_mode text,
               content_retention_days int, evaluation_consent boolean,
               effective_at timestamptz, revoked_at timestamptz)
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select k.trace_mode, c.consent_version, c.trace_mode, c.content_retention_days,
         c.evaluation_consent, c.effective_at, c.revoked_at
    from public.api_keys k
    left join lateral (select h.consent_version, h.trace_mode, h.content_retention_days,
                              h.evaluation_consent, h.effective_at, h.revoked_at
                         from infrx.consent_history h where h.org_id = k.org_id
                        order by h.consent_version desc limit 1) c on true
   where k.id = p_key and k.org_id = p_org
$$;

revoke all on function infrx.trace_consent(uuid, uuid) from public;
grant execute on function infrx.trace_consent(uuid, uuid) to infrx_runtime;
