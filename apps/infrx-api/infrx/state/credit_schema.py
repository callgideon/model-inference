"""The D1R seams, as data: what A1, D2 and C0 call and exactly what comes back.

Checked against the live catalog by `tests/d/checks_credit.check_seams`, so this map and
the migrations cannot drift apart. Nothing here opens a connection.

Errors every seam uses (SQLSTATE -> meaning):
  55000  maintenance: the feature flag is off or missing, or a rollout hold
  P0002  not_found (never confirms that a private artifact exists)
  22023  invalid_request (blank evidence; an unpriced model)
  42501  not this caller's wallet / organization
  23514  a named CHECK; `credit_wallets_reserved_within_total` is insufficient credit (402)
"""
from __future__ import annotations

#: signature -> (who may call it, ordered result columns as (name, SQL type)).
SEAMS: dict[str, tuple[frozenset[str], tuple[tuple[str, str], ...]]] = {
    # A1: the one-transaction initial grant; a retry returns the same row, replayed=true.
    "infrx.grant_signup_credit(uuid,text,text,uuid)": (frozenset({"service_role"}), (
        ("user_id", "uuid"), ("wallet_id", "uuid"), ("ledger_operation_id", "uuid"),
        ("amount", "text"), ("granted_at", "timestamp with time zone"),
        ("replayed", "boolean"))),
    # D2: the pins frozen on a CREDIT job at acceptance, plus what the hold needs.
    "infrx.resolve_admission_pins(text)": (frozenset({"service_role"}), (
        ("model_id", "uuid"), ("requested_model", "text"), ("deployment_revision_id", "uuid"),
        ("serving_version_id", "uuid"), ("rate_card_version", "text"),
        ("policy_version", "text"), ("accounting_regime", "text"), ("model_revision", "text"),
        ("max_input_tokens", "integer"), ("max_output_tokens", "integer"),
        ("input_rate_per_million", "text"), ("output_rate_per_million", "text"),
        ("provisional", "boolean"))),
    # C0: exactly one row per user; wallet_id NULL until granted.
    "public.console_wallet_summary(uuid)": (frozenset({"authenticated", "service_role"}), (
        ("user_id", "uuid"), ("wallet_id", "uuid"), ("kind", "text"), ("unit", "text"),
        ("ledger_total", "text"), ("reserved_total", "text"), ("available", "text"),
        ("revision", "bigint"), ("signup_granted_at", "timestamp with time zone"))),
    # C0: historical USD, its own unit, never summed with CREDIT.
    "public.console_legacy_usd_statement(uuid)": (frozenset({"authenticated", "service_role"}), (
        ("org_id", "uuid"), ("accounting_regime", "text"), ("unit", "text"),
        ("balance", "text"), ("entry_count", "bigint"),
        ("as_of", "timestamp with time zone"), ("rollout_hold", "boolean"))),
}

# --- D2 (0010-0014): the platform's admission / preparation / outbox operations. Each
# takes and returns one jsonb (or a scalar); `infrx/state/jobstore.py` is the adapter and
# its docstrings are the argument shapes. Refusals are SQLSTATE P0001 `<error_code>: …`
# (hint `retry_after=<s>` on 429s); a refusal that follows a committed terminalization
# (R39) is returned as `{"refusal": {code, detail}}` instead of raised.
_SERVICE = frozenset({"service_role"})
SEAMS.update({
    "infrx.admit(jsonb)": (_SERVICE, ()),                 # 06 boundary, body D2 (0011)
    "infrx.prepare(jsonb)": (_SERVICE, ()),               # 06 boundary, body D2 (0012)
    "infrx.claim_preparation(jsonb)": (_SERVICE, ()),
    "infrx.dispatch_pending(jsonb)": (_SERVICE, ()),
    "infrx.acknowledge_dispatch(jsonb)": (_SERVICE, ()),
    "infrx.dispatch_snapshot()": (_SERVICE, ()),
    "infrx.gc_outbox(jsonb)": (_SERVICE, ()),
    "infrx.job_admission(uuid)": (_SERVICE, ()),
    "infrx.put_result(jsonb)": (_SERVICE, ()),            # W2: the result object writer
    "infrx.read_result(uuid,text)": (_SERVICE, ()),
    "infrx.touch_media_object(text,uuid)": (_SERVICE, ()),  # M3 (0010)
    # also M3: infrx.delete_media_object_if_idle(text, timestamptz) -> boolean, service_role
})

# --- D3 (0016): fenced leases, recovery and cancellation. Same conventions as D2's. Each
# takes one job row FOR UPDATE, then (when it terminalizes) that job's hold, then its
# wallet - never a wallet before a job row; `recover` takes job rows SKIP LOCKED, one at a
# time, so it never waits on one while holding a wallet. `terminalize` is D3's fenced
# prefix only: past the fence it is D5's stub (0A000).
SEAMS.update({
    "infrx.claim(jsonb)": (_SERVICE, ()),                 # 06 boundary, body D3
    "infrx.heartbeat(jsonb)": (_SERVICE, ()),             # 06 boundary, body D3
    "infrx.cancel(jsonb)": (_SERVICE, ()),                # 06 boundary, body D3
    "infrx.terminalize(jsonb)": (_SERVICE, ()),           # 06 boundary, fence D3, rest D5
    "infrx.load_work(jsonb)": (_SERVICE, ()),
    "infrx.recover(jsonb)": (_SERVICE, ()),
})

# --- D4 (0017): the stream journal (`infrx/state/journal.py`). `append` takes ONE job row
# (through D3's fence) and never the admission scope lock; `expire_journal` takes job rows
# SKIP LOCKED; the terminal event is a trigger inside the terminalizing transaction.
SEAMS.update({
    "infrx.append(jsonb)": (_SERVICE, ()),                # 06 boundary, body D4
    "infrx.read_journal(jsonb)": (_SERVICE, ()),
    "infrx.expire_journal(jsonb)": (_SERVICE, ()),
    "infrx.journal_usage()": (_SERVICE, ()),
})

#: The admission lock order (0011). Every D writer takes these in this order; a grant
#: takes only the last; settlement (D5) takes the wallet without the scope lock.
LOCK_ORDER = ("pg_advisory_xact_lock(infrx.admission_lock_key())  -- capacity scope",
              "infrx.idempotency (org_id, operation, key)  FOR UPDATE",
              "public.organizations (id)  FOR SHARE",
              "public.api_keys (id)  FOR SHARE",
              "infrx.wallets (org_id) | infrx.credit_wallets (wallet_id)  FOR UPDATE")

#: D5 hard rule (D1R review (d)): settle the CREDIT hold (held -> settled, which releases
#: the reservation) BEFORE inserting the `inference_debit` ledger row. A debit first trips
#: `credit_wallets_reserved_within_total` whenever the hold equals the available balance.
#: D2 never writes a zero-amount CREDIT hold (0011 refuses it as invalid_request).
D5_SETTLE_HOLD_BEFORE_DEBIT = True

#: C0/U1R pages: view -> ordered columns. Keyset order for the ledger page is
#: (created_at desc, entry_id desc) within one wallet_id.
VIEWS: dict[str, tuple[str, ...]] = {
    "public.console_credit_wallets": (
        "wallet_id", "kind", "unit", "owner_user_id", "org_id", "owner_provider_org_id",
        "ledger_total", "reserved_total", "available", "revision", "updated_at",
        "signup_granted_at"),
    "public.console_credit_ledger": (
        "entry_id", "wallet_id", "org_id", "created_at", "kind", "amount", "unit",
        "request_id", "reason", "actor"),
}

#: D2/D5: the columns a CREDIT admission writes on `infrx.jobs` (all NOT NULL for the
#: credit regime, never updated), and the hold row that reserves it.
CREDIT_JOB_PINS = ("accounting_regime", "wallet_id", "model_id", "requested_model",
                   "deployment_revision_id", "serving_version_id", "rate_card_version",
                   "policy_version")
CREDIT_HOLD_COLUMNS = ("request_id", "org_id", "wallet_id", "rate_card_version", "amount",
                       "state")
