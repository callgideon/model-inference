-- D10-APP-SQL: the console read port the App lanes asked for (C0 WR-5, U1R WR-3(a)/(b);
-- R59-4, R64, R66, R122-R127). Reads only: no table, row, money or runtime grant changes.
--
--   credit_ledger_wallet_credits_in_idx   U1R WR-3(b): "Spent" = sum(grant + adjustments)
--                                         - ledger_total reads a wallet's NON-debit entries;
--                                         through the barrier view that read filtered every
--                                         entry of the wallet by kind. This partial index
--                                         serves it (the quals are leakproof, so they reach
--                                         the base relation; the App query is unchanged).
--   public.consumer_credit_ledger         C0 WR-5: the signed-in individual's own CREDIT
--     (p_after, p_limit)                  ledger, newest first. `console_credit_ledger` is a
--                                         security_barrier view joining wallets, so its page
--                                         bitmaps the whole wallet and top-N sorts it. Here
--                                         auth.uid() -> the caller's consumer wallet (the
--                                         identity `consumer_org()` resolves), and the page
--                                         is one row-comparison range on 0006's
--                                         `credit_ledger_wallet_created_idx` stopped by its
--                                         LIMIT: O(limit). Exposes no wallet, org or actor
--                                         (R59-2); money is exact text (R64). A limit outside
--                                         1..100 is REFUSED (`invalid_request`), not clamped.
--   public.consumer_jobs                  U1R WR-3(a): four optional filters - p_model (the
--     (+ p_model, p_key_id, p_from, p_to) requested string or the canonical revision),
--                                         p_key_id, and the half-open window [p_from, p_to).
--                                         Defaulted and appended, so every existing call
--                                         (positional or named, SQL or PostgREST) answers
--                                         exactly as 0021's did. PostgreSQL cannot add
--                                         parameters in place, and a second overload would
--                                         make a two-argument call ambiguous, so the 0021
--                                         signature is dropped and recreated in one
--                                         transaction with the same body, columns and grants.
--
-- ORDER. After 0021 (consumer_org, the consumer read surface) and 0006 (the ledger index).
-- Apply before the App build that calls `consumer_credit_ledger` or passes the new
-- consumer_jobs filters; the App running now calls neither, and its consumer_jobs calls
-- (p_after, p_limit, p_request_id) keep their answers. PostgREST reloads its schema cache
-- on the DDL notification Supabase sends.
--
-- ROLLBACK (0024 alone):
--   drop function if exists public.consumer_credit_ledger(text, integer);
--   drop function if exists public.consumer_jobs(text, integer, uuid, text, uuid,
--                                                timestamptz, timestamptz);
--   -- then re-run 0021's `create or replace function public.consumer_jobs(...)` and its
--   -- revoke/grant lines (0021:342-398, 421-427)
--   drop index if exists infrx.credit_ledger_wallet_credits_in_idx;
-- No data moves either way; an App build that calls the new reads must be rolled back first.
--
-- Re-runnable: `if not exists` / `if exists`, `create or replace`, and the grants restated.

-- =========================================================== U1R WR-3(b): credits-in ===
create index if not exists credit_ledger_wallet_credits_in_idx
  on infrx.credit_ledger (wallet_id) where kind <> 'inference_debit';

-- ======================================================== C0 WR-5: the ledger page ===
create or replace function public.consumer_credit_ledger(p_after text default null,
                                                         p_limit int default 50)
returns table (entry_id uuid, created_at timestamptz, kind text, amount text, unit text,
               request_id uuid, reason text, cursor text)
language plpgsql stable security definer set search_path = public, infrx, pg_temp as $$
#variable_conflict use_column
declare
  v_wallet uuid;
  -- No cursor: start above every entry, so both pages are the same one index range.
  v_at timestamptz := 'infinity';
  v_id uuid := 'ffffffff-ffff-ffff-ffff-ffffffffffff';
begin
  if auth.uid() is null then
    raise exception 'not signed in' using errcode = '42501';
  end if;
  if p_limit is null or p_limit < 1 or p_limit > 100 then
    perform infrx.refuse('invalid_request', 'p_limit must be between 1 and 100');
  end if;
  if p_after is not null then
    begin
      v_at := split_part(p_after, '|', 1)::timestamptz;
      v_id := split_part(p_after, '|', 2)::uuid;
    exception when others then
      raise exception 'invalid_cursor: not a cursor this read issued' using errcode = 'P0001';
    end;
  end if;
  select w.wallet_id into v_wallet from infrx.credit_wallets w
   where w.owner_user_id = auth.uid() and w.kind = 'consumer';
  return query
  select l.entry_id, l.created_at, l.kind, l.amount::text, l.unit, l.request_id, l.reason,
         l.created_at::text || '|' || l.entry_id::text
    from infrx.credit_ledger l
   where l.wallet_id = v_wallet
     and (l.created_at, l.entry_id) < (v_at, v_id)
   order by l.created_at desc, l.entry_id desc
   limit p_limit;
end $$;

-- ============================================ U1R WR-3(a): the consumer_jobs filters ===
drop function if exists public.consumer_jobs(text, integer, uuid);
-- 0021's read (C0/U4), unchanged but for the four filters in the WHERE clause.
create or replace function public.consumer_jobs(p_after text default null,
                                                p_limit int default 50,
                                                p_request_id uuid default null,
                                                p_model text default null,
                                                p_key_id uuid default null,
                                                p_from timestamptz default null,
                                                p_to timestamptz default null)
returns table (request_id uuid, job_handle text, created_at timestamptz, requested_model text,
               model_revision text, execution_mode text, state text, outcome_cause text,
               accounting_regime text, settlement_state text, usage_certainty text,
               prompt_tokens int, completion_tokens int, unit text, hold text,
               hold_state text, charged text, result_available boolean,
               result_expires_at timestamptz, settled_at timestamptz, cursor text)
language plpgsql stable security definer set search_path = public, infrx, pg_temp as $$
#variable_conflict use_column
declare
  v_org uuid;
  v_at timestamptz;
  v_id uuid;
  v_now timestamptz := infrx.now();
begin
  if auth.uid() is null then
    raise exception 'not signed in' using errcode = '42501';
  end if;
  if p_after is not null then
    begin
      v_at := split_part(p_after, '|', 1)::timestamptz;
      v_id := split_part(p_after, '|', 2)::uuid;
    exception when others then
      raise exception 'invalid_cursor: not a cursor this read issued' using errcode = 'P0001';
    end;
  end if;
  v_org := public.consumer_org();
  return query
  select j.request_id, j.job_handle, j.created_at, coalesce(j.requested_model, j.model_revision),
         j.model_revision, j.execution_mode, j.state, j.outcome_cause, j.accounting_regime,
         j.settlement_state, j.usage_certainty, j.usage_prompt_tokens,
         j.usage_completion_tokens,
         case j.accounting_regime when 'credit' then 'CREDIT' else 'USD' end,
         coalesce(ch.amount, uh.amount)::text, coalesce(ch.state, uh.state),
         case when j.settlement_state = 'settled' then case j.accounting_regime
           when 'credit' then (select (-l.amount)::text from infrx.credit_ledger l
                                where l.request_id = j.request_id
                                  and l.kind = 'inference_debit')
           else j.debit::text end end,
         j.state = 'succeeded' and j.result_ref is not null
           and j.usage_prompt_tokens is not null and j.result_expires_at is not null
           and v_now < j.result_expires_at
           and not exists (select 1 from infrx.job_results x where x.request_id = j.request_id
                            and x.scrubbed_at is not null),
         j.result_expires_at, j.settled_at, j.created_at::text || '|' || j.request_id::text
    from infrx.jobs j
    left join infrx.credit_wallet_holds ch on ch.request_id = j.request_id
    left join infrx.credit_holds uh on uh.request_id = j.request_id
   where v_org is not null and j.org_id = v_org
     and (p_request_id is null or j.request_id = p_request_id)
     -- ponytail: model/key filter rows inside the org's keyset scan; a rare value walks the
     -- org's history. Add (org_id, key_id, created_at) indexes if per-key pages get slow.
     and (p_model is null or p_model in (j.requested_model, j.model_revision))
     and (p_key_id is null or j.key_id = p_key_id)
     and (p_from is null or j.created_at >= p_from)
     and (p_to is null or j.created_at < p_to)
     and (v_at is null or j.created_at < v_at or (j.created_at = v_at and j.request_id > v_id))
   order by j.created_at desc, j.request_id
   limit greatest(1, least(coalesce(p_limit, 50), 100));
end $$;

-- ================================================================ privileges ===
-- R59-4: Supabase's default ACL hands anon/authenticated EXECUTE on a new public function;
-- revoke that first, then grant exactly the signed-in read (and the platform's, as every
-- console read; with no JWT subject it is refused 42501). Never infrx_runtime/monitor.
revoke all on function public.consumer_credit_ledger(text, integer)
  from public, anon, authenticated, service_role;
revoke all on function public.consumer_jobs(text, integer, uuid, text, uuid, timestamptz,
                                            timestamptz)
  from public, anon, authenticated, service_role;
grant execute on function public.consumer_credit_ledger(text, integer)
  to authenticated, service_role;
grant execute on function public.consumer_jobs(text, integer, uuid, text, uuid, timestamptz,
                                               timestamptz)
  to authenticated, service_role;
