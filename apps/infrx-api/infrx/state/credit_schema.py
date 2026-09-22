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
