"""D2: the invariants of the atomic admission (0011) as SQL-level checks.

Same contract as `checks.py`/`checks_credit.py`: each check raises `AssertionError` on a
broken invariant and returns a short summary, so `migration_mutants.py` can run it against
a single-edit mutant (R32/R40). The database is the "admission" scenario: every migration,
the test clock FROZEN at the conformance start, `pgtesting.seed` (the builders' two
organizations, KEY_A/KEY_B, `DEFAULT_PRICE`) and `checks_credit.seed_credit` (the CREDIT
individuals, flags on, grants, the Marlin registry) plus one consumer key per individual.

Every admission here is ONE call of the boundary `infrx.admit(jsonb)` with the arguments
the adapter builds, so what is checked is exactly what `PgJobStore.admit` runs. Each check
works inside a transaction it rolls back, so checks do not see each other's rows.
"""
from __future__ import annotations

import threading
import uuid
from datetime import timedelta
from decimal import Decimal

import psycopg
from psycopg.types.json import Jsonb

from infrx.contracts import errors
from infrx.contracts.conformance import builders as b
from infrx.contracts.fakes.support import DEFAULT_START, SequentialIds
from infrx.contracts.limits import DEFAULTS
from infrx.state import pgtesting
from infrx.state.jobstore import PgJobStore, domain_error

from . import checks_credit as cc

# Consumer keys, one per individual of the CREDIT fixture (0009: a consumer key names
# its individual). OPERATOR_KEY is the single operator-audience key; STRAY_KEY is
# CONSUMER_1's key filed under another organization (R66: its wallet is not reachable).
C1_KEY = "c7000000-0000-4000-8000-000000000001"
C2_KEY = "c7000000-0000-4000-8000-000000000002"
UNGRANTED_KEY = "c7000000-0000-4000-8000-000000000003"
OPERATOR_KEY = "c7000000-0000-4000-8000-000000000004"
STRAY_KEY = "c7000000-0000-4000-8000-000000000005"
NAMED_KEY = "c7000000-0000-4000-8000-000000000006"     # created by C2, names C1 (M6)
PROVIDER_KEY = "c7000000-0000-4000-8000-000000000007"  # a provider_dev key in ORG_A (M4)
# MC-1: a provider_dev key CREATED BY CONSUMER_1 and filed in C1's personal org - the shape
# whose `coalesce(user_id, created_by)` resolves to C1's consumer wallet if the CREDIT
# body's audience rule is lost.
PROVIDER_C1_KEY = "c7000000-0000-4000-8000-000000000008"
ALIAS = "nemostation/marlin-2b"
PIN = "nemostation/marlin-2b@2026-09-01"


class _Rollback(Exception):
    pass


def seed_admission(conn) -> None:
    """The scenario's rows (see the module docstring)."""
    conn.execute("select infrx_test.freeze(%s)", (DEFAULT_START,))
    pgtesting.seed(conn)
    cc.seed_credit(conn)
    for key, user, audience in ((C1_KEY, cc.CONSUMER_1, "consumer"),
                                (C2_KEY, cc.CONSUMER_2, "consumer"),
                                (UNGRANTED_KEY, cc.UNGRANTED, "consumer"),
                                (OPERATOR_KEY, cc.CONSUMER_2, "operator")):
        conn.execute("insert into public.api_keys (id, org_id, created_by, name, prefix, "
                     "key_hash, audience) values (%s, %s, %s, 'k', 'sk-infrx-credit0', %s, %s)",
                     (key, cc.personal_org(conn, user), user, f"hash-{key}", audience))
    conn.execute("insert into public.api_keys (id, org_id, created_by, name, prefix, key_hash) "
                 "values (%s, %s, %s, 'k', 'sk-infrx-stray000', %s)",
                 (STRAY_KEY, b.ORG_A, cc.CONSUMER_1, f"hash-{STRAY_KEY}"))
    conn.execute("insert into public.credit_ledger (org_id, delta_usd, kind, reason) values "
                 "(%s, 25, 'grant', 'fixture'), (%s, 25, 'grant', 'fixture')",
                 (b.ORG_A, b.ORG_B))


# --------------------------------------------------------------------- driving
class _Clock:
    def __init__(self, conn) -> None:
        self.conn = conn

    def now(self):
        return self.conn.execute("select infrx.now()").fetchone()[0]


class World:
    """What `builders.request` needs from a harness, on this connection's clock."""

    def __init__(self, conn) -> None:
        self.clock = _Clock(conn)
        self.ids = _Ids()


class _Ids(SequentialIds):
    def uuid(self) -> str:                      # unique across checks sharing a database
        return str(uuid.uuid4())


def args(request, idem, *, regime: str = "legacy_usd", limits=DEFAULTS) -> dict:
    return PgJobStore(None, limits=limits)._admit_args(regime, request, idem)


def admit(conn, request, idem, **kw) -> dict:
    return conn.execute("select infrx.admit(%s)", (Jsonb(args(request, idem, **kw)),)
                        ).fetchone()[0]


def refusal(conn, request, idem, **kw) -> str | None:
    """The error code an admission refuses with (inside a savepoint, so the rest of the
    caller's transaction survives), or None when it was admitted."""
    try:
        with conn.transaction():
            admit(conn, request, idem, **kw)
    except psycopg.Error as failed:
        mapped = domain_error(failed)
        return mapped.code if isinstance(mapped, errors.DomainError) else \
            f"untyped {failed.sqlstate}: {str(failed).splitlines()[0][:100]}"
    return None


def footprint(conn) -> tuple:
    """Everything an admission may own: jobs, both hold tables, reservations, outbox,
    idempotency mappings, and the two wallets' reserved totals."""
    return conn.execute("""select (select count(*) from infrx.jobs),
        (select count(*) from infrx.credit_holds), (select count(*) from infrx.credit_wallet_holds),
        (select count(*) from infrx.capacity_reservations), (select count(*) from infrx.outbox),
        (select count(*) from infrx.idempotency),
        (select coalesce(sum(reserved_total), 0) from infrx.wallets),
        (select coalesce(sum(reserved_total), 0) from infrx.credit_wallets)""").fetchone()


def _in_rollback(conn, body):
    try:
        with conn.transaction():
            result = body()
            raise _Rollback()
    except _Rollback:
        return result


def gateway_skewed(request, db_now, *, created_s: float, deadline_s: float):
    """The same request as a gateway whose clock is `created_s` off the store's would send
    it: `created_at = db_now + created_s`, `deadline_at = db_now + deadline_s`."""
    return request.model_copy(update={
        "created_at": db_now + timedelta(seconds=created_s),
        "deadline_at": db_now + timedelta(seconds=deadline_s)})


def credit_request(world, key: str, org: str, model: str = PIN, **kw):
    return b.request(world, org_id=org, key_id=key, model_revision=model, **kw)


# --------------------------------------------------------------------- checks
def check_admission_accepts(conn) -> str:
    """DUR-ADMIT acceptance: one legacy USD admission owns exactly one job (preparing,
    the store's price snapshot, the derived ceiling hold, R29/R20 instants on the store
    clock), one held USD hold reserving that amount, three active reservations, one
    prepare_dispatch naming the job and one idempotency mapping. One CREDIT admission owns
    the same plus every pin `resolve_admission_pins` answers and a held CREDIT hold on the
    individual's own wallet (R66/R78)."""
    world = World(conn)

    def body():
        now = world.clock.now()
        before = footprint(conn)
        request = b.request(world)
        doc = admit(conn, request, b.idem(request, "accept"))
        hold = b.hold_for(request, b.DEFAULT_PRICE)
        after = footprint(conn)
        assert after[:6] == (before[0] + 1, before[1] + 1, before[2], before[3] + 3,
                             before[4] + 1, before[5] + 1), (before, after)
        assert after[6] - before[6] == hold and after[7] == before[7], (before, after)
        job = conn.execute("""select state, accounting_regime, price_version, maximum_hold,
            deadline_at, preparation_deadline_at, admitted_at, job_handle, request_record,
            idem_payload_hash from infrx.jobs where request_id = %s""",
                           (request.request_id,)).fetchone()
        horizon = b.default_deadline_s()
        assert job[:4] == ("preparing", "legacy_usd", b.DEFAULT_PRICE.price_version, hold), job
        assert job[4] == min(request.deadline_at, now + timedelta(seconds=horizon)), job[4]
        assert job[5] == min(now + timedelta(seconds=DEFAULTS.preparation_timeout_s), job[4]), 'failed: job[5] == min(now + timedelta(seconds=DEFAULTS.preparation_timeout_s), job[4])'
        assert job[6] == now and job[7].startswith("job_") and job[7] == doc["job_handle"], 'failed: job[6] == now and job[7].startswith("job_") and job[7] == doc["job_handle"]'
        assert job[8] == request.model_dump(mode="json"), "the admitted request was altered"
        assert job[9] == b.idem(request, "accept").payload_hash, 'failed: job[9] == b.idem(request, "accept").payload_hash'
        held = conn.execute("select amount, state from infrx.credit_holds where request_id = %s",
                            (request.request_id,)).fetchone()
        assert held == (hold, "held"), held
        reservations = conn.execute(
            "select kind, amount, active from infrx.capacity_reservations where request_id = %s "
            "order by kind", (request.request_id,)).fetchall()
        assert reservations == [("inference", 1, True),
                                ("journal_bytes", DEFAULTS.journal_job_reserve_bytes, True),
                                ("preparation", 1, True)], reservations
        events = conn.execute("select kind, payload->>'job_handle', acknowledged_at from "
                              "infrx.outbox where aggregate_id = %s", (request.request_id,)
                              ).fetchall()
        assert events == [("prepare_dispatch", job[7], None)], events
        assert doc["maximum_hold"] == f"{hold:f}" and doc["replayed"] is False, 'failed: doc["maximum_hold"] == f"{hold:f}" and doc["replayed"] is False'
        assert Decimal(doc["price_snapshot"]["input_rate_per_million"]) == \
            b.DEFAULT_PRICE.input_rate_per_million, 'failed: Decimal(doc["price_snapshot"]["input_rate_per_million"]) == \\ b.DEFAULT_PRICE.input_rate_per_million'
        # R53 is immutable on the row (the admitted request too)
        why = cc.attempt(conn, "update infrx.jobs set request_record = '{}'::jsonb "
                               "where request_id = %s", (request.request_id,))
        assert why is not None and why.startswith("23514"), f"request_record rewritten: {why}"
        # R29/R79: a horizon the store cannot keep is clamped to its own clock + budgets
        far = b.request(world, deadline_s=horizon * 100)
        admit(conn, far, b.idem(far, "accept-far"))
        kept, = conn.execute("select deadline_at from infrx.jobs where request_id = %s",
                             (far.request_id,)).fetchone()
        assert kept == now + timedelta(seconds=horizon), f"not clamped: {kept}"
        # ...and measured on the STORE clock, never the gateway's: a request whose own
        # clock runs an hour ahead (created_at = db_now + 1 h, deadline 2 h after that)
        # keeps db_now + horizon, not created_at + horizon (R29/R79, audit R-3).
        skewed = gateway_skewed(b.request(world), now, created_s=3600, deadline_s=3600 + 7200)
        admit(conn, skewed, b.idem(skewed, "accept-skewed"))
        kept, = conn.execute("select deadline_at from infrx.jobs where request_id = %s",
                             (skewed.request_id,)).fetchone()
        assert kept == now + timedelta(seconds=horizon), \
            f"the clamp used the gateway clock: {kept} != db_now + {horizon}s"
        # the hold rounds UP (ceiling_8) on a rate whose exact cost has more digits
        conn.execute("insert into infrx.price_versions (price_version, model_revision, "
                     "input_rate_per_million, output_rate_per_million, token_rules_version, "
                     "effective_from) values ('pv_odd', 'odd/model@1', 0.11111111, "
                     "0.11111111, 'tr_v1', '2026-01-01T00:00:00Z')")
        odd = b.request(world, model_revision="odd/model@1", max_input_tokens=1,
                        max_output_tokens=1)
        odd_doc = admit(conn, odd, b.idem(odd, "accept-odd"))
        # (2 x 0.11111111) / 10^6 = 0.00000022222222: up, never to the nearest
        assert odd_doc["maximum_hold"] == "0.00000023", odd_doc["maximum_hold"]

        # CREDIT
        org = cc.personal_org(conn, cc.CONSUMER_1)
        credit = credit_request(world, C1_KEY, org)
        before = footprint(conn)
        cdoc = admit(conn, credit, b.idem(credit, "accept-credit"), regime="credit")
        pins = cc.resolve(conn, PIN)
        row = conn.execute("""select accounting_regime, wallet_id::text, model_id::text,
            requested_model, deployment_revision_id::text, serving_version_id::text,
            rate_card_version, policy_version, model_revision, price_version, maximum_hold
            from infrx.jobs where request_id = %s""", (credit.request_id,)).fetchone()
        want_hold = pgtesting_hold(credit, "400", "1200")
        assert row == ("credit", cc.wallet_of(conn, cc.CONSUMER_1), pins["model_id"], PIN,
                       pins["deployment_revision_id"], pins["serving_version_id"],
                       pins["rate_card_version"], pins["policy_version"], PIN, None,
                       want_hold), row
        chold = conn.execute("select amount, state, wallet_id::text from "
                             "infrx.credit_wallet_holds where request_id = %s",
                             (credit.request_id,)).fetchone()
        assert chold == (want_hold, "held", row[1]), chold
        after = footprint(conn)
        assert after[7] - before[7] == want_hold and after[6] == before[6], (before, after)
        assert cdoc["pins"]["rate_card_version"] == pins["rate_card_version"], 'failed: cdoc["pins"]["rate_card_version"] == pins["rate_card_version"]'
        assert cdoc["rate_card"]["input_rate_per_million"] == "400.00000000", 'failed: cdoc["rate_card"]["input_rate_per_million"] == "400.00000000"'
        # Review M6: the wallet is the key's INDIVIDUAL (`api_keys.user_id`), not whoever
        # created the key row: CONSUMER_2 files a key that names CONSUMER_1.
        conn.execute("insert into public.api_keys (id, org_id, created_by, user_id, name, "
                     "prefix, key_hash) values (%s, %s, %s, %s, 'k', 'sk-infrx-named000', "
                     "'hash-named')", (NAMED_KEY, org, cc.CONSUMER_2, cc.CONSUMER_1))
        named = credit_request(world, NAMED_KEY, org)
        got = refusal(conn, named, b.idem(named, "accept-named"), regime="credit")
        assert got is None, f"a key naming CONSUMER_1 could not spend CONSUMER_1's wallet: {got}"
        spent, = conn.execute("select wallet_id::text from infrx.jobs where request_id = %s",
                              (named.request_id,)).fetchone()
        assert spent == cc.wallet_of(conn, cc.CONSUMER_1), \
            f"the key's creator's wallet was spent, not its individual's: {spent}"
        # MC-3 (DUR-CAP boundary): a hold EXACTLY equal to what is available is admitted,
        # in both bodies
        with conn.transaction():
            usd = b.request(world, org_id=b.ORG_B, key_id=b.KEY_B)
            available, = conn.execute("select available from infrx.wallets where org_id = %s",
                                      (b.ORG_B,)).fetchone()
            conn.execute("insert into public.credit_ledger (org_id, delta_usd, kind, reason) "
                         "values (%s, %s, 'adjustment', 'mc3')",
                         (b.ORG_B, b.hold_for(usd) - available))
            got = refusal(conn, usd, b.idem(usd, "mc3-usd"))
            assert got is None, f"a USD hold equal to the available balance was refused: {got}"
            raise_rollback()
        with conn.transaction():
            c2_org = cc.personal_org(conn, cc.CONSUMER_2)
            exact = credit_request(world, C2_KEY, c2_org)
            w2 = cc.wallet_of(conn, cc.CONSUMER_2)
            available, = conn.execute("select available from infrx.credit_wallets where "
                                      "wallet_id = %s", (w2,)).fetchone()
            conn.execute("insert into infrx.credit_ledger (wallet_id, wallet_kind, kind, amount, "
                         "operation_id, actor, reason) values (%s, 'consumer', "
                         "'operator_adjustment', %s, gen_random_uuid(), 'ops', 'mc3')",
                         (w2, pgtesting_hold(exact, "400", "1200") - available))
            got = refusal(conn, exact, b.idem(exact, "mc3-credit"), regime="credit")
            assert got is None, f"a CREDIT hold equal to the available balance was refused: {got}"
            raise_rollback()
        return f"one USD admission owns job/hold/3 reservations/dispatch/mapping; one CREDIT " \
               f"admission owns the pins and a {want_hold} CREDIT hold"
    return _in_rollback(conn, body)


def pgtesting_hold(request, input_rate: str, output_rate: str) -> Decimal:
    from infrx.contracts import money
    return money.maximum_hold(request.max_input_tokens, request.max_output_tokens,
                              Decimal(input_rate), Decimal(output_rate))


def check_admission_refusals(conn) -> str:
    """DUR-ADMIT "rejects own none": every refusal answers its typed code and leaves the
    whole footprint - jobs, both hold tables, reservations, outbox, mappings, reserved
    totals - exactly as it was (R10, R29, R45, R55, R66, R69, R70, DUR-CAP)."""
    world = World(conn)
    c1_org = cc.personal_org(conn, cc.CONSUMER_1)
    c2_org = cc.personal_org(conn, cc.CONSUMER_2)
    ungranted_org = cc.personal_org(conn, cc.UNGRANTED)

    def req(**kw):
        return b.request(world, **kw)

    staged = {
        "revoked": f"update public.api_keys set revoked_at = infrx.now() where id = '{b.KEY_A}'",
        "suspended": f"update public.organizations set suspended = true, "
                     f"suspended_at = infrx.now(), suspension_reason = 'other' "
                     f"where id = '{b.ORG_A}'",
        "unentitled": f"insert into infrx.org_entitlements (org_id, model_ids) values "
                      f"('{b.ORG_A}', '{{other/model}}')",
        "credit off": "update infrx.feature_flags set enabled = false "
                      "where name = 'credit_admission'",
        "legacy off": "update infrx.feature_flags set enabled = false "
                      "where name = 'legacy_usd_admission'",
        "expensive listing": _listing(2, "rc_expensive", "100000000", "100000000"),
        "zero listing": _listing(3, "rc_free", "0", "0"),
        "zero price": "insert into infrx.price_versions (price_version, model_revision, "
                      "input_rate_per_million, output_rate_per_million, token_rules_version, "
                      "effective_from) values ('pv_free', 'free/model@1', 0, 0, 'tr_v1', "
                      "'2026-01-01T00:00:00Z')",
        "provider key": f"insert into public.api_keys (id, org_id, name, prefix, key_hash, "
                        f"audience, provider_org_id, endpoint_id) values ('{PROVIDER_KEY}', "
                        f"'{b.ORG_A}', 'p', 'sk-infrx-prov0000', 'hash-prov', 'provider_dev', "
                        f"'{cc.NEMO}', '{cc.DEV_ENDPOINT}')",
        "provider key in c1's org": f"insert into public.api_keys (id, org_id, created_by, "
                                    f"name, prefix, key_hash, audience, provider_org_id, "
                                    f"endpoint_id) values ('{PROVIDER_C1_KEY}', '{c1_org}', "
                                    f"'{cc.CONSUMER_1}', 'p', 'sk-infrx-provc100', "
                                    f"'hash-prov-c1', 'provider_dev', '{cc.NEMO}', "
                                    f"'{cc.DEV_ENDPOINT}')",
        "withdrawn price": "alter table infrx.price_versions disable trigger "
                           "price_versions_immutable; update infrx.price_versions set "
                           "effective_to = infrx.now() where price_version = 'pv_test'; "
                           "alter table infrx.price_versions enable trigger "
                           "price_versions_immutable",
    }
    cases = (
        # (label, request factory, idem key/override, regime, staged setup, expected code)
        ("an elapsed deadline", lambda: req(deadline_s=-1), None, "legacy_usd", None,
         "invalid_request"),
        ("a deadline equal to the store clock", lambda: req(deadline_s=0), None,
         "legacy_usd", None, "invalid_request"),
        # R29 on the STORE clock: a gateway an hour behind sends a deadline that is still
        # in ITS future but already past on the store's - refused.
        ("a deadline elapsed on the store clock, not the gateway's",
         lambda: gateway_skewed(req(), world.clock.now(), created_s=-3600, deadline_s=-1),
         None, "legacy_usd", None, "invalid_request"),
        ("a revoked key", req, None, "legacy_usd", "revoked", "invalid_api_key"),
        ("another organization's key", lambda: req(key_id=b.KEY_B), None, "legacy_usd",
         None, "invalid_api_key"),
        ("a suspended organization", req, None, "legacy_usd", "suspended", "org_suspended"),
        ("an unentitled model", req, None, "legacy_usd", "unentitled", "model_not_entitled"),
        ("a zero output ceiling", lambda: req(max_output_tokens=0), None, "legacy_usd", None,
         "invalid_request"),
        ("an output ceiling past MAX_OUTPUT_TOKENS", lambda: req(
            max_input_tokens=100, max_output_tokens=DEFAULTS.max_output_tokens + 1), None,
         "legacy_usd", None, "invalid_request"),
        ("a zero input ceiling", lambda: req(max_input_tokens=0), None, "legacy_usd", None,
         "invalid_request"),
        ("a context past MAX_CONTEXT_TOKENS", lambda: req(max_input_tokens=31_000), None,
         "legacy_usd", None, "context_length_exceeded"),
        ("an unpriced model", lambda: req(model_revision=f"{b.MODEL}-unpriced"), None,
         "legacy_usd", None, "invalid_request"),
        ("a withdrawn price", req, None, "legacy_usd", "withdrawn price", "invalid_request"),
        ("a hold past the available USD", lambda: req(org_id=b.ORG_B, key_id=b.KEY_B,
                                                      max_input_tokens=30_000), None,
         "legacy_usd", "drain org b", "insufficient_credit"),
        # Review M2: the gate is AVAILABLE, not the ledger: org B holds 0.01 USD and one
        # 0.0072288 hold; the next hold fits the ledger but not what is available.
        ("a hold past the available USD but within the ledger",
         lambda: req(org_id=b.ORG_B, key_id=b.KEY_B), None, "legacy_usd", "one hold in org b",
         "insufficient_credit"),
        # Review MC-3b: the other side of the boundary - one unit (0.00000001 USD) short
        # of the hold is refused (the equal side is admitted in check_admission_accepts).
        ("a USD hold one unit past the available balance",
         lambda: req(org_id=b.ORG_B, key_id=b.KEY_B), None, "legacy_usd",
         "org b one unit short", "insufficient_credit"),
        # Review M3: never a zero hold in the USD regime either.
        ("a zero USD hold", lambda: req(model_revision="free/model@1"), None, "legacy_usd",
         "zero price", "invalid_request"),
        # Review M4: the CREDIT body's audience rule, in the USD body.
        ("an operator key in the USD regime", lambda: req(org_id=c2_org, key_id=OPERATOR_KEY),
         None, "legacy_usd", None, "forbidden"),
        ("a provider_dev key in the USD regime", lambda: req(key_id=PROVIDER_KEY), None,
         "legacy_usd", "provider key", "not_found"),
        ("another organization's idempotency scope", req, "foreign idem", "legacy_usd", None,
         "forbidden"),
        ("another organization's media", lambda: req(refs=(b.media(b.ORG_B),)), None,
         "legacy_usd", None, "not_found"),
        ("the legacy regime switched off", req, None, "legacy_usd", "legacy off",
         "dependency_unavailable"),
        ("an unknown accounting regime", req, None, "usd", None, "invalid_request"),
        # CREDIT
        ("an unknown model", lambda: credit_request(world, C1_KEY, c1_org, "nobody/nothing"),
         None, "credit", None, "not_found"),
        ("the private dev deployment", lambda: credit_request(world, C1_KEY, c1_org,
                                                              cc.DEV_DEPLOYMENT),
         None, "credit", None, "not_found"),
        ("CREDIT admission switched off", lambda: credit_request(world, C1_KEY, c1_org),
         None, "credit", "credit off", "dependency_unavailable"),
        ("an operator key", lambda: credit_request(world, OPERATOR_KEY, c2_org), None,
         "credit", None, "forbidden"),
        ("an individual with no wallet", lambda: credit_request(world, UNGRANTED_KEY,
                                                                ungranted_org),
         None, "credit", None, "not_found"),
        ("a wallet reached through another organization", lambda: credit_request(
            world, STRAY_KEY, b.ORG_A), None, "credit", None, "forbidden"),
        ("a hold past the available CREDIT", lambda: credit_request(world, C1_KEY, c1_org,
                                                                    ALIAS),
         None, "credit", "expensive listing", "insufficient_credit"),
        ("a zero CREDIT hold", lambda: credit_request(world, C1_KEY, c1_org, ALIAS), None,
         "credit", "zero listing", "invalid_request"),
        ("ceilings past the deployment's limits", lambda: credit_request(
            world, C1_KEY, c1_org, max_input_tokens=30_721, max_output_tokens=1), None,
         "credit", None, "context_length_exceeded"),
        # MC-1: a provider_dev credential reaches no public listing through the CREDIT
        # body either - even one whose creator owns a funded consumer wallet in that org.
        ("a provider_dev key in the CREDIT regime", lambda: credit_request(
            world, PROVIDER_C1_KEY, c1_org, ALIAS), None, "credit", "provider key in c1's org",
         "not_found"),
    )

    def body():
        before = footprint(conn)
        seen = []
        for label, make, idem_key, regime, setup, expected in cases:
            with conn.transaction():
                if setup == "drain org b":
                    conn.execute("insert into public.credit_ledger (org_id, delta_usd, kind, "
                                 "reason) values (%s, -24.999, 'adjustment', 'drain')",
                                 (b.ORG_B,))
                elif setup == "one hold in org b":
                    conn.execute("insert into public.credit_ledger (org_id, delta_usd, kind, "
                                 "reason) values (%s, -24.99, 'adjustment', 'drain')",
                                 (b.ORG_B,))
                    first = req(org_id=b.ORG_B, key_id=b.KEY_B)
                    admit(conn, first, b.idem(first, str(uuid.uuid4())))
                    left = conn.execute("select ledger_total, available from infrx.wallets "
                                        "where org_id = %s", (b.ORG_B,)).fetchone()
                    assert left[1] < b.hold_for(first) <= left[0], \
                        f"the case needs a hold between available and the ledger: {left}"
                elif setup == "org b one unit short":
                    short = req(org_id=b.ORG_B, key_id=b.KEY_B)
                    available, = conn.execute("select available from infrx.wallets where "
                                              "org_id = %s", (b.ORG_B,)).fetchone()
                    conn.execute("insert into public.credit_ledger (org_id, delta_usd, kind, "
                                 "reason) values (%s, %s, 'adjustment', 'mc3b')",
                                 (b.ORG_B, b.hold_for(short) - available
                                  - Decimal("0.00000001")))
                elif setup:
                    conn.execute(staged[setup])
                request = make()
                idem = b.idem(request, str(uuid.uuid4()))
                if idem_key == "foreign idem":
                    idem = idem.model_copy(update={"org_id": b.ORG_B})
                mark = footprint(conn)
                got = refusal(conn, request, idem, regime=regime)
                assert got == expected, f"{label}: expected {expected}, got {got}"
                assert footprint(conn) == mark, f"{label}: the refusal left rows behind"
                raise_rollback()
            seen.append(label)
        assert footprint(conn) == before, 'failed: footprint(conn) == before'
        # the control: a gateway an hour AHEAD sends a deadline 300 s out on the store's
        # clock (already "past" on its own) - admitted, because only the store clock counts
        with conn.transaction():
            ahead = gateway_skewed(req(), world.clock.now(), created_s=3600, deadline_s=300)
            got = refusal(conn, ahead, b.idem(ahead, "ahead"))
            assert got is None, f"a deadline 300 s out on the store clock was refused: {got}"
            raise_rollback()
        return f"{len(seen)} refusals typed, each leaving no job/hold/reservation/dispatch"
    return _in_rollback(conn, body)


def raise_rollback():
    raise psycopg.Rollback()


def _listing(version: int, card: str, input_rate: str, output_rate: str) -> str:
    return (f"insert into infrx.rate_card_versions (rate_card_version, model_id, "
            f"deployment_revision_id, serving_version_id, input_rate_per_million, "
            f"output_rate_per_million, effective_at, approved_by, provisional) values "
            f"('{card}', '{cc.MODEL}', '{cc.PUBLIC_DEPLOYMENT}', '{cc.SERVING}', {input_rate}, "
            f"{output_rate}, '2026-09-01T00:00:00Z', 'ops', true); "
            f"insert into infrx.catalog_listings (public_model_id, version, model_id, "
            f"deployment_revision_id, serving_version_id, rate_card_version, effective_at, "
            f"approved_by) values ('{ALIAS}', {version}, '{cc.MODEL}', "
            f"'{cc.PUBLIC_DEPLOYMENT}', '{cc.SERVING}', '{card}', '2026-09-01T00:00:00Z', "
            f"'ops')")


def check_admission_capacity(conn) -> str:
    """DUR-CAP: each scope refuses on its own with the other scopes slack - total, org,
    key, preparation (R1) and the journal budget - with a Retry-After, and the refused
    admission owns nothing."""
    world = World(conn)
    wide = dict(max_active_jobs=64, max_active_jobs_per_org=16, max_active_jobs_per_key=8,
                max_preparing_jobs=8)
    scopes = (("total", dict(wide, max_active_jobs=2), "capacity_exhausted"),
              ("org", dict(wide, max_active_jobs_per_org=2), "capacity_exhausted"),
              ("key", dict(wide, max_active_jobs_per_key=2), "capacity_exhausted"),
              ("preparation", dict(wide, max_preparing_jobs=2), "capacity_exhausted"),
              ("journal", dict(wide, journal_total_bytes=2 * DEFAULTS.journal_job_reserve_bytes),
               "journal_capacity_exhausted"))

    def body():
        for scope, changes, code in scopes:
            with conn.transaction():
                limits = DEFAULTS.replace(**changes)
                for n in range(2):
                    request = b.request(world)
                    admit(conn, request, b.idem(request, f"{scope}-{n}"), limits=limits)
                request = b.request(world)
                mark = footprint(conn)
                try:
                    with conn.transaction():
                        admit(conn, request, b.idem(request, f"{scope}-over"), limits=limits)
                except psycopg.Error as failed:
                    mapped = domain_error(failed)
                    assert getattr(mapped, "code", None) == code, (scope, mapped)
                    assert mapped.retry_after_s and mapped.retry_after_s >= 1, (scope, mapped)
                else:
                    raise AssertionError(f"the {scope} scope was oversubscribed")
                assert footprint(conn) == mark, f"{scope}: the refusal left rows behind"
                # every other scope still fits: a job in ORG_B on KEY_B is admitted
                # (except under the global total and journal, which bind everyone)
                if scope in ("org", "key"):
                    other = b.request(world, org_id=b.ORG_B, key_id=b.KEY_B)
                    admit(conn, other, b.idem(other, f"{scope}-other"), limits=limits)
                raise_rollback()
        return f"{len(scopes)} scopes refuse on their own with retry guidance"
    return _in_rollback(conn, body)


def check_admission_idempotency(conn) -> str:
    """DUR-ADMIT: the same key and payload replay the original identity and reserve
    nothing (R53: the original hold and price); a changed payload is 409; the R6 retry of
    a request UUID without its key is 409; the tombstone runs 24 h from the TERMINAL
    state, replaying up to the instant before it expires and answering 410 at it."""
    world = World(conn)

    def body():
        request = b.request(world)
        idem = b.idem(request, "idem-1")
        first = admit(conn, request, idem)
        mark = footprint(conn)
        again = admit(conn, request, idem)
        assert again["replayed"] is True and footprint(conn) == mark, "the replay reserved"
        assert {k: v for k, v in again.items() if k != "replayed"} == \
            {k: v for k, v in first.items() if k != "replayed"}, "the replay changed identity"
        assert refusal(conn, request, b.idem(request, "idem-1", payload="other")) == \
            "idempotency_conflict", 'failed: refusal(conn, request, b.idem(request, "idem-1", payload="other")) == \\ "idempotency_conflict"'
        assert refusal(conn, request, b.idem(request, None)) == "state_conflict", 'failed: refusal(conn, request, b.idem(request, None)) == "state_conflict"'
        assert refusal(conn, request, b.idem(request, "a-late-key")) == "state_conflict", 'failed: refusal(conn, request, b.idem(request, "a-late-key")) == "state_conflict"'
        assert footprint(conn) == mark, 'failed: footprint(conn) == mark'
        # Review M7: one key never answers across regimes - a CREDIT caller replaying a
        # USD admission's key (same org, same payload) is a state_conflict, not its job.
        c1 = cc.personal_org(conn, cc.CONSUMER_1)
        conn.execute("insert into public.credit_ledger (org_id, delta_usd, kind, reason) "
                     "values (%s, 25, 'grant', 'm7')", (c1,))
        usd = credit_request(world, C1_KEY, c1)
        crossed = b.idem(usd, "crossed")
        admit(conn, usd, crossed)                             # a USD job in C1's org
        got = refusal(conn, usd, crossed, regime="credit")
        assert got == "state_conflict", f"a CREDIT replay answered a USD admission: {got}"
        # the tombstone runs from the TERMINAL state (D5's settlement stands in as a row),
        # which here is an hour after admission
        ttl = DEFAULTS.idempotency_ttl_s
        conn.execute("select infrx_test.advance(3600)")
        conn.execute("""update infrx.jobs set state = 'failed', outcome_cause = 'platform_error',
            settlement_state = 'released_free', settled_at = infrx.now()
            where request_id = %s""", (request.request_id,))
        conn.execute("select infrx_test.advance(%s)", (ttl - 1e-6,))
        assert refusal(conn, request, idem) is None, \
            "the tombstone expired before terminal + 24 h"
        conn.execute("select infrx_test.advance(%s)", (1e-6,))
        assert refusal(conn, request, idem) == "idempotency_expired", \
            "the tombstone outlived terminal + 24 h"
        # an active job's mapping never expires, however long it runs
        live = b.request(world)
        admit(conn, live, b.idem(live, "live"))
        conn.execute("select infrx_test.advance(%s)", (10 * ttl,))
        assert refusal(conn, live, b.idem(live, "live")) is None, \
            "an active job's mapping expired"
        return "replay same identity, 409 changed payload / R6, 410 exactly at terminal+24h"
    return _in_rollback(conn, body)


def check_admission_concurrency(connect, database: str, rounds: int = 5,
                                threads: int = 8) -> str:
    """DUR-CAP (04): concurrent admissions across keys and organizations, interleaved with
    grants, under the documented lock order: no deadlock, exactly the capacity admitted,
    reserved == the sum of the holds, available never negative."""
    limits = DEFAULTS.replace(max_active_jobs=3, max_active_jobs_per_org=3,
                              max_active_jobs_per_key=3)
    report = []
    for n in range(rounds):
        with connect(database) as setup:
            _end_everything(setup)
            world = World(setup)
            requests = [b.request(world, org_id=org, key_id=key)
                        for org, key in ((b.ORG_A, b.KEY_A), (b.ORG_B, b.KEY_B)) * (threads // 2)]
        outcomes: list = []
        errors_seen: list = []
        barrier = threading.Barrier(threads + 2)

        def admit_one(request):
            try:
                with connect(database) as conn:
                    conn.execute("set role service_role")
                    barrier.wait()
                    outcomes.append(admit(conn, request, b.idem(request, request.request_id),
                                         limits=limits))
            except psycopg.Error as failed:
                mapped = domain_error(failed)
                (outcomes if isinstance(mapped, errors.CapacityExhausted)
                 else errors_seen).append(mapped)
            except Exception as other:           # pragma: no cover - reported below
                errors_seen.append(other)

        def grant_one(org):
            try:
                with connect(database) as conn:
                    barrier.wait()
                    conn.execute("insert into public.credit_ledger (org_id, delta_usd, kind, "
                                  "reason) values (%s, 0.5, 'grant', 'race')", (org,))
            except Exception as other:           # pragma: no cover - reported below
                errors_seen.append(other)

        workers = [threading.Thread(target=admit_one, args=(r,)) for r in requests]
        workers += [threading.Thread(target=grant_one, args=(org,)) for org in (b.ORG_A, b.ORG_B)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        assert not errors_seen, f"round {n}: {errors_seen}"
        admitted = [o for o in outcomes if isinstance(o, dict)]
        assert len(admitted) == 3, f"round {n}: {len(admitted)} admitted against room for 3"
        with connect(database) as check:
            rows = check.execute("select w.org_id, w.reserved_total, w.available, "
                                 "coalesce((select sum(h.amount) from infrx.credit_holds h "
                                 "where h.org_id = w.org_id and h.state = 'held'), 0) "
                                 "from infrx.wallets w where w.org_id in (%s, %s)",
                                 (b.ORG_A, b.ORG_B)).fetchall()
        for org, reserved, available, holds in rows:
            assert reserved == holds, f"round {n}: {org} reserved {reserved} != holds {holds}"
            assert available >= 0, f"round {n}: {org} available {available}"
        report.append(len(admitted))
    with connect(database) as cleanup:
        _end_everything(cleanup)
    return f"{rounds} rounds x {threads} admissions + 2 grants: {report} admitted, no deadlock"


def _end_everything(conn) -> None:
    """Terminalize every live legacy job and release what it held (D5's settlement,
    stood in for by rows), so a check that commits leaves the scenario as it found it."""
    conn.execute("update infrx.jobs set state = 'failed', outcome_cause = 'platform_error', "
                 "settlement_state = 'released_free', settled_at = infrx.now() "
                 "where state in ('preparing','queued','running')")
    conn.execute("update infrx.capacity_reservations set active = false, "
                 "released_at = infrx.now() where active")
    conn.execute("update infrx.credit_holds set state = 'released' where state = 'held'")
    conn.execute("update infrx.wallets set reserved_total = 0")


#: Review SEC-3: D2's internal helpers - only a SECURITY DEFINER body calls them, so NOBODY
#: holds EXECUTE, service_role included - and the platform operations service_role calls.
D2_HELPERS = (
    "infrx.refuse(text,text,integer)", "infrx.refuse_json(text,text)",
    "infrx.admission_lock_key()", "infrx.hold_for(integer,integer,numeric,numeric)",
    "infrx.journal_bytes_charged()", "infrx.is_entitled(uuid,text)",
    "infrx.admission_replay(jsonb,double precision,timestamp with time zone,text)",
    "infrx.admission_checks(jsonb,jsonb,jsonb,text,timestamp with time zone)",
    "infrx.admission_rows(jsonb,jsonb,jsonb,text,timestamp with time zone)",
    "infrx.admission_insert_job(jsonb,jsonb,jsonb,jsonb,timestamp with time zone,"
    "timestamp with time zone,text,text,text,jsonb,jsonb,numeric)",
    "infrx.admit_legacy_usd(jsonb)", "infrx.admit_credit(jsonb)",
    "infrx.jobs_admission_record_guard()", "infrx.release_hold_legacy_usd(uuid)",
    "infrx.release_hold_credit(uuid)", "infrx.terminalize_unstarted(uuid,text)",
    "infrx.lease_doc(infrx.attempts)", "infrx.index_event(infrx.outbox,infrx.jobs)",
    "infrx.dispatch_wanted(text,text)", "infrx.jobs_credit_admission_guard(infrx.jobs)",
    "infrx.media_uploads_guard()")
D2_OPERATIONS = (
    "infrx.admit(jsonb)", "infrx.prepare(jsonb)", "infrx.claim_preparation(jsonb)",
    "infrx.dispatch_pending(jsonb)", "infrx.acknowledge_dispatch(jsonb)",
    "infrx.dispatch_snapshot()", "infrx.reopen_dispatch(jsonb)",
    "infrx.release_dispatch(jsonb)", "infrx.fail_dispatch(jsonb)", "infrx.gc_outbox(jsonb)",
    "infrx.job_admission(uuid)", "infrx.put_result(jsonb)", "infrx.read_result(uuid,text)",
    "infrx.touch_media_object(text,uuid)",
    "infrx.delete_media_object_if_idle(text,timestamp with time zone)")


def check_d2_function_privileges(conn) -> str:
    """SEC-3 as a named invariant: no role executes a D2 helper (service_role included);
    every D2 operation is executable by service_role and by no browser role; none is
    executable by PUBLIC."""
    problems = []
    for signature in D2_HELPERS + D2_OPERATIONS:
        roles = {role: conn.execute("select has_function_privilege(%s, %s::regprocedure, "
                                    "'execute')", (role, signature)).fetchone()[0]
                 for role in ("anon", "authenticated", "service_role")}
        public = conn.execute("select exists (select 1 from aclexplode((select proacl from "
                              "pg_proc where oid = %s::regprocedure)) a where a.grantee = 0)",
                              (signature,)).fetchone()[0]
        want_service = signature in D2_OPERATIONS
        if roles["anon"] or roles["authenticated"] or public:
            problems.append(f"{signature}: browser/PUBLIC execute {roles} public={public}")
        if roles["service_role"] != want_service:
            problems.append(f"{signature}: service_role execute={roles['service_role']}, "
                            f"expected {want_service}")
    assert not problems, "D2 function privileges:\n  " + "\n  ".join(problems)
    return (f"{len(D2_HELPERS)} helpers executable by nobody, {len(D2_OPERATIONS)} "
            f"operations by service_role only")
