"""The conformance `Harness` for the PostgreSQL JobStore - TEST SUPPORT, never wired.

The fakes' factories (`contracts/fakes/factories.py`) are the reference; this is the same
contract over a real task-local database. It lives beside the adapter so another track's
suite (E3B's drills) can build the same rig on its own PostgreSQL:

    factory = make_jobstore_factory(fresh_database, dsn_for)
    harness = factory()            # a fresh migrated, seeded, FROZEN database per call

`fresh_database() -> str` returns the name of an empty database carrying every migration,
`infrx/state/test_clock.sql` and `seed(conn)` (see `tests/d/pgstore.py`, which clones a
template); `dsn_for(name) -> str` is a superuser/owner DSN for it.

What a hook does to the database, and why some of them step around a guard:

* `grant`, `balance`, `active_jobs`, `outbox`, `outbox_kinds`, `journal_bytes`,
  `suspend_org`, `revoke_key`, `unentitle`/`entitle`, `retune` are ordinary writes/reads.
* `publish` is one committed chunk through D4's real `append` (the fence, the chunk and the
  publication marker in one transaction), as the fakes' hook is; `stream` is the
  `PgStreamStore` on the same database (`make_streamstore_factory` makes it the port).
* `unrevoke_key` and `set_price` UNDO things production can never undo (0009 makes a
  revocation one-way; `price_versions` is immutable). They disable exactly that one
  trigger for one statement, as the table owner, in the test database only - they model
  the fake's in-memory "withdraw/restore" and prove nothing about the guards, which
  `tests/d/checks*.py` prove on their own.
* The clock is the database's: `infrx_test.freeze` pins `infrx.now()`, and `advance`
  moves it for every session (R7), so `harness.clock.now()` IS the store's `db_now`.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Callable

from ..contracts import errors, money
from ..contracts.conformance import Harness
from ..contracts.conformance import builders as b
from ..contracts.fakes.support import (DEFAULT_START, CrashAfterCommit, FailurePlan,
                                       SequentialIds)
from ..contracts.limits import DEFAULTS, PilotSettings
from ..contracts.records import (ChunkEventType, EngineEvent, NormalizedRequest, OutboxEvent,
                                 OutboxKind, PriceSnapshot)
from .jobstore import PgJobStore, connector
from .journal import PgStreamStore

USERS = {b.ORG_A: "1a1a1a1a-0000-4000-8000-0000000000a1",
         b.ORG_B: "2b2b2b2b-0000-4000-8000-0000000000b2"}
KEYS = {b.KEY_A: b.ORG_A, b.KEY_B: b.ORG_B}


def seed(conn) -> None:
    """The builders' world as rows: two organizations with one owner each, KEY_A in ORG_A
    and KEY_B in ORG_B (a key belongs to ONE organization in PostgreSQL - R59-6), and the
    `DEFAULT_PRICE` row for `MODEL`."""
    for org, user in USERS.items():
        conn.execute("insert into auth.users (id, email) values (%s, %s)",
                     (user, f"owner-{org[:4]}@example.com"))
        conn.execute("insert into public.organizations (id, name, slug, created_by) "
                     "values (%s, %s, %s, %s)", (org, f"org {org[:4]}", f"org-{org[:8]}", user))
        conn.execute("insert into public.org_members (org_id, user_id, role) "
                     "values (%s, %s, 'owner')", (org, user))
    for key, org in KEYS.items():
        conn.execute("insert into public.api_keys (id, org_id, created_by, name, prefix, "
                     "key_hash) values (%s, %s, %s, 'k', 'sk-infrx-conform', %s)",
                     (key, org, USERS[org], f"hash-{key}"))
    _insert_price(conn, b.DEFAULT_PRICE)


def _insert_price(conn, snapshot: PriceSnapshot) -> None:
    conn.execute(
        "insert into infrx.price_versions (price_version, model_revision, "
        "input_rate_per_million, output_rate_per_million, token_rules_version, "
        "effective_from, captured_at) values (%s, %s, %s, %s, %s, "
        "least(%s::timestamptz, infrx.now()), %s)",
        (snapshot.price_version, snapshot.model_revision, snapshot.input_rate_per_million,
         snapshot.output_rate_per_million, snapshot.token_rules_version,
         snapshot.captured_at, snapshot.captured_at))


class PgClock:
    """`FakeClock`'s interface over the database clock, frozen at `start`."""

    def __init__(self, conn, start: datetime = DEFAULT_START) -> None:
        self._conn = conn
        self._now, = conn.execute("select infrx_test.freeze(%s)", (start,)).fetchone()

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> datetime:
        self._now, = self._conn.execute("select infrx_test.advance(%s)",
                                        (float(seconds),)).fetchone()
        return self._now

    def at(self, seconds: float) -> datetime:
        return self._now + timedelta(seconds=seconds)


class FailingJobStore:
    """The `FailurePlan` around a real store: `fail` raises before the call (nothing
    committed), `crash_after_commit` lets the transaction commit and then loses the
    answer - exactly a process dying between COMMIT and the response."""

    def __init__(self, store: PgJobStore, plan: FailurePlan) -> None:
        self.store, self.plan = store, plan

    def __getattr__(self, name: str) -> Any:
        target = getattr(self.store, name)
        if not callable(target) or name.startswith("_"):
            return target

        async def call(*args, **kw):
            self.plan.before(name)
            result = await target(*args, **kw)
            self.plan.after_commit(name)
            return result
        return call


class WorkerResults(PgJobStore):
    """TEST RIG (R30): the conformance builders name a result as an opaque text
    (`results/test/result.json`); the real worker stores its result first (`put_result`) and
    completes with the `infrx-result:<job_id>` reference it gets back, which is all the store
    accepts. This does the same with the builder's text, for the lease's job, so a case's
    proposal reaches the settlement the way a worker's does. Replays stay identical (the same
    text is the same stored result); a proposal without a reference is sent unchanged."""

    async def _terminalize(self, lease, outcome, regime: str) -> dict:
        if outcome.result_ref is not None and not outcome.result_ref.startswith("infrx-result:"):
            ref = await self.put_result(lease.job_id, outcome.result_ref)
            outcome = outcome.model_copy(update={"result_ref": ref})
        return await super()._terminalize(lease, outcome, regime)


def assert_test_database(conn) -> None:
    """The same gate as the movable clock (0003): only a task-local `infrx_<task>`
    database may have a production guard stepped around (review SEC-1)."""
    ok, name = conn.execute("select current_database() like 'infrx@_%' escape '@', "
                            "current_database()").fetchone()
    if not ok:
        raise RuntimeError(f"refusing to disable a guard in {name!r}: not a task-local "
                           "infrx_<task> test database")


def _without_trigger(conn, table: str, trigger: str, sql: str, params: tuple) -> None:
    assert_test_database(conn)
    with conn.transaction():
        conn.execute(f"alter table {table} disable trigger {trigger}")
        conn.execute(sql, params)
        conn.execute(f"alter table {table} enable trigger {trigger}")


def hooks(conn, store: PgJobStore, stream: PgStreamStore | None = None) -> dict[str, Callable]:
    def grant(org_id: str, amount) -> Decimal:
        value = money.parse(amount) if not isinstance(amount, Decimal) else amount
        if value < 0:
            raise errors.InvalidRequest(f"a credit grant must not be negative: {value}")
        if value > 0:          # the legacy ledger refuses a zero row; a zero grant is a no-op
            conn.execute("insert into public.credit_ledger (org_id, delta_usd, kind, reason) "
                         "values (%s, %s, 'grant', 'conformance grant')", (org_id, value))
        return balance(org_id)["ledger"]

    def balance(org_id: str) -> dict:
        row = conn.execute("select ledger_total, reserved_total, available from infrx.wallets "
                           "where org_id = %s", (org_id,)).fetchone() or (money.ZERO,) * 3
        return {"ledger": row[0], "reserved": row[1], "available": row[2], "zero": money.ZERO}

    def active_jobs(org_id: str | None = None, key_id: str | None = None) -> list:
        rows = conn.execute(
            "select request_record from infrx.jobs where state in ('preparing','queued',"
            "'running') and (%s::uuid is null or org_id = %s::uuid) and "
            "(%s::uuid is null or key_id = %s::uuid) order by created_at, request_id",
            (org_id, org_id, key_id, key_id)).fetchall()
        return [SimpleNamespace(request=NormalizedRequest.model_validate(r))
                for r, in rows]

    def outbox(aggregate_id: str | None = None) -> list[OutboxEvent]:
        rows = conn.execute(
            "select event_id, aggregate_id, kind, version, payload, available_at "
            "from infrx.outbox where %s::uuid is null or aggregate_id = %s::uuid "
            "order by created_at, array_position(array['prepare_dispatch',"
            "'inference_dispatch','usage_projection','trace_projection','feedback_projection',"
            "'judge_projection','callback_delivery'], kind), event_id",
            (aggregate_id, aggregate_id)).fetchall()
        return [OutboxEvent(event_id=str(e), aggregate_id=str(a), kind=k, version=v,
                            payload=p, available_at=t) for e, a, k, v, p, t in rows]

    def outbox_kinds(aggregate_id: str) -> list[OutboxKind]:
        return [event.kind for event in outbox(aggregate_id)]

    def journal_bytes() -> int:
        # The same sum admission checks (0011 `journal_bytes_charged`), read as the owner.
        return conn.execute("select infrx.journal_bytes_charged()").fetchone()[0]

    def revoke_key(key_id: str) -> None:
        conn.execute("update public.api_keys set revoked_at = infrx.now() "
                     "where id = %s and revoked_at is null", (key_id,))

    def unrevoke_key(key_id: str) -> None:
        _without_trigger(conn, "public.api_keys", "api_keys_identity_guard",
                         "update public.api_keys set revoked_at = null where id = %s",
                         (key_id,))

    def suspend_org(org_id: str) -> None:
        conn.execute("update public.organizations set suspended = true, "
                     "suspended_at = infrx.now(), suspension_reason = 'other' where id = %s",
                     (org_id,))

    def _entitled_set(org_id: str) -> list[str] | None:
        row = conn.execute("select model_ids from infrx.org_entitlements where org_id = %s",
                           (org_id,)).fetchone()
        return None if row is None else row[0]

    def _set_entitlement(org_id: str, models: list[str] | None) -> None:
        conn.execute("insert into infrx.org_entitlements (org_id, model_ids) values (%s, %s) "
                     "on conflict (org_id) do update set model_ids = excluded.model_ids, "
                     "entitlement_version = infrx.org_entitlements.entitlement_version + 1",
                     (org_id, models))

    def unentitle(org_id: str, model_revision: str) -> None:
        current = _entitled_set(org_id)
        if current is None:        # the default set: every priced model
            current = [m for m, in conn.execute(
                "select distinct model_revision from infrx.price_versions").fetchall()]
        _set_entitlement(org_id, [m for m in current if m != model_revision])

    def entitle(org_id: str, model_revision: str) -> None:
        current = _entitled_set(org_id)
        if current is not None and model_revision not in current:
            _set_entitlement(org_id, [*current, model_revision])

    def set_price(model_revision: str, snapshot: PriceSnapshot | None) -> None:
        """Withdraw every effective price of `model_revision`, then make `snapshot` the
        effective one (its row is inserted, or restored and corrected in place)."""
        assert_test_database(conn)
        with conn.transaction():
            conn.execute("alter table infrx.price_versions disable trigger "
                         "price_versions_immutable")
            conn.execute("update infrx.price_versions set effective_to = infrx.now() "
                         "where model_revision = %s and effective_to is null "
                         "and effective_from < infrx.now()", (model_revision,))
            if snapshot is not None:
                found = conn.execute("select 1 from infrx.price_versions where "
                                     "price_version = %s", (snapshot.price_version,)).fetchone()
                if found:
                    conn.execute(
                        "update infrx.price_versions set model_revision = %s, "
                        "input_rate_per_million = %s, output_rate_per_million = %s, "
                        "token_rules_version = %s, captured_at = %s, effective_to = null, "
                        "effective_from = least(%s::timestamptz, infrx.now()) "
                        "where price_version = %s",
                        (snapshot.model_revision, snapshot.input_rate_per_million,
                         snapshot.output_rate_per_million, snapshot.token_rules_version,
                         snapshot.captured_at, snapshot.captured_at, snapshot.price_version))
                else:
                    _insert_price(conn, snapshot)
            conn.execute("alter table infrx.price_versions enable trigger "
                         "price_versions_immutable")

    async def publish(lease):
        """One committed chunk through the real `append` (the fakes' hook, same payload)."""
        return await stream.append(lease, (EngineEvent(type=ChunkEventType.delta,
                                                       payload={"content": "x"}),))

    def retune(**changes) -> PilotSettings:
        store.limits = store.limits.replace(**changes)
        if stream is not None:
            stream.limits = store.limits
        return store.limits

    return {"grant": grant, "balance": balance, "active_jobs": active_jobs, "outbox": outbox,
            "outbox_kinds": outbox_kinds, "journal_bytes": journal_bytes,
            "revoke_key": revoke_key, "unrevoke_key": unrevoke_key, "suspend_org": suspend_org,
            "unentitle": unentitle, "entitle": entitle, "set_price": set_price,
            "retune": retune, "publish": publish,
            "unsettleable": lambda: dict(store.unsettleable)}


def make_jobstore_factory(fresh_database: Callable[[], str], dsn_for: Callable[[str], str],
                          *, keep: int = 6,
                          connect_for: Callable[[str], Any] | None = None
                          ) -> Callable[..., Harness]:
    """`factory(limits=None) -> Harness`, a fresh frozen database per call. The last `keep`
    connections stay open (a case may hold two harnesses); older ones are closed.
    `connect_for(database) -> Connect` is how the STORES connect (D10: the dedicated runtime
    login, 0021 `infrx_runtime`); the hooks always act as the owner."""
    connect_for = connect_for or (lambda name: connector(dsn_for(name)))
    import psycopg
    opened: deque = deque()

    def factory(limits: PilotSettings | None = None, **_: object) -> Harness:
        name = fresh_database()
        conn = psycopg.connect(dsn_for(name), autocommit=True)
        opened.append(conn)
        while len(opened) > keep:
            opened.popleft().close()
        clock = PgClock(conn)
        store = WorkerResults(connect_for(name), limits=limits or DEFAULTS)
        stream = PgStreamStore(connect_for(name), limits=limits or DEFAULTS)
        plan = FailurePlan()
        extra = hooks(conn, store, stream)
        extra["store"] = store
        extra["database"] = name
        extra["conn"] = conn                                 # the owner's rows (test reads)
        extra["stream"] = FailingJobStore(stream, plan)
        return Harness(port=FailingJobStore(store, plan), clock=clock, ids=SequentialIds(),
                       failures=plan, extra=extra)

    return factory


def seed_credit_world(conn, seed_sql: str) -> None:
    """The v2 fixture world as rows, on top of `seed` (the CREDIT conformance suite's
    trusted rows, `contracts/fakes/factories.credit_jobstore_factory`): the operator's Marlin
    seed (`seed_sql`, the v2 fixtures verbatim), CREDIT admission and the signup grant ON,
    the consumer individual with the fixture's personal organization and wallet (+10000
    through A1's grant), the provider's workspace organization and its zero dev wallet, and
    the two fixture key rows (consumer; provider_dev scoped to the dev endpoint)."""
    from ..contracts.v2 import fixtures as v2fix
    ids = v2fix.IDS
    assert_test_database(conn)
    conn.execute(seed_sql)
    for flag in ("credit_admission", "signup_grant"):
        conn.execute("update infrx.feature_flags set enabled = true, updated_by = 'rig', "
                     "reason = 'conformance' where name = %s", (flag,))
    # The fixture's personal organization id. 0001's `handle_new_user` mints a personal
    # organization only for a user with no membership yet, so the user, profile, fixture
    # organization and owner membership are ONE statement: the AFTER trigger runs at its end
    # and finds the membership. No trigger is disabled (`postgres` does not own `auth.users`
    # on the Supabase image).
    conn.execute(
        "with u as (insert into auth.users (id, email) values (%(user)s, 'consumer@example.com') "
        "returning id), p as (insert into public.profiles (id, email) select id, "
        "'consumer@example.com' from u returning id), o as (insert into public.organizations "
        "(id, name, slug, created_by) select %(org)s, 'consumer', 'consumer-fixture', id from p "
        "returning id) insert into public.org_members (org_id, user_id, role) "
        "select o.id, %(user)s, 'owner' from o",
        {"user": ids.consumer_user, "org": ids.consumer_org})
    assert conn.execute("select array_agg(org_id::text) from public.org_members "
                        "where user_id = %s", (ids.consumer_user,)).fetchone()[0] == \
        [ids.consumer_org], "the signup trigger minted a second personal organization"
    conn.execute("insert into infrx.credit_wallets (wallet_id, kind, owner_user_id, "
                 "personal_org_id) values (%s, 'consumer', %s, %s)",
                 (ids.consumer_wallet, ids.consumer_user, ids.consumer_org))
    conn.execute("select * from infrx.grant_signup_credit(%s, 'conformance/verified')",
                 (ids.consumer_user,))
    conn.execute("insert into public.organizations (id, name, slug) values (%s, 'provider', "
                 "'provider-fixture')", (ids.provider_org,))
    conn.execute("insert into infrx.credit_wallets (wallet_id, kind, owner_provider_org_id) "
                 "values (%s, 'provider_dev', %s) on conflict do nothing",
                 (ids.provider_dev_wallet, ids.provider_org))
    for name in ("auth_context_consumer.json", "auth_context_provider_dev.json"):
        register_credential(conn, v2fix.BUILDERS[name]())


def register_credential(conn, auth) -> None:
    """The key row a CREDIT admission reads its audience and identities from (0009): a
    provider_dev row carries its provider and endpoint and no individual."""
    provider = auth.audience.value == "provider_dev"
    conn.execute("insert into public.api_keys (id, org_id, created_by, name, prefix, key_hash, "
                 "audience, user_id, provider_org_id, endpoint_id) values (%s, %s, %s, 'k', "
                 "'sk-infrx-conform', %s, %s, %s, %s, %s)",
                 (auth.key_id, auth.org_id, None if provider else auth.user_id,
                  f"hash-{auth.key_id}", auth.audience.value, None if provider else auth.user_id,
                  auth.provider_org_id, auth.endpoint_id))


def credit_hooks(conn) -> dict[str, Callable]:
    """The CREDIT suite's required hooks (`credit_jobstore_cases`), over rows."""
    def credit_balance(wallet_id: str) -> dict:
        row = conn.execute("select ledger_total, reserved_total, available from "
                           "infrx.credit_wallets where wallet_id = %s", (wallet_id,)).fetchone()
        return {"ledger": row[0], "reserved": row[1], "available": row[2]}

    def credit_grant(wallet_id: str, amount) -> Decimal:
        """An audited operator movement through D5's `grant_credit` (an allocation to a
        provider_dev wallet, an adjustment to a consumer one)."""
        import uuid
        from psycopg.types.json import Jsonb
        kind, = conn.execute("select kind from infrx.credit_wallets where wallet_id = %s",
                             (wallet_id,)).fetchone()
        conn.execute("select infrx.grant_credit(%s)", (Jsonb({
            "wallet_id": wallet_id, "amount": money.format_money(money.parse(amount)),
            "kind": "operator_allocation" if kind == "provider_dev" else "operator_adjustment",
            "operation_id": str(uuid.uuid4()), "actor": "conformance", "reason": "funding",
            "at": None}),))
        return credit_balance(wallet_id)["ledger"]

    def publish_rate_card(card) -> None:
        """An operator publishing an approved card; a card for a public deployment also gets
        the catalog listing that points new admissions at it (0007: a new version)."""
        conn.execute("insert into infrx.rate_card_versions (rate_card_version, model_id, "
                     "deployment_revision_id, serving_version_id, input_rate_per_million, "
                     "output_rate_per_million, effective_at, approved_by, provisional) values "
                     "(%s, %s, %s, %s, %s, %s, %s, %s, false)",
                     (card.rate_card_version, card.model_id, card.deployment_revision_id,
                      card.serving_version_id, card.input_rate_per_million.raw("CREDIT"),
                      card.output_rate_per_million.raw("CREDIT"), card.effective_at,
                      card.approved_by))
        conn.execute("insert into infrx.catalog_listings (public_model_id, version, model_id, "
                     "deployment_revision_id, serving_version_id, rate_card_version, "
                     "effective_at, approved_by) select l.public_model_id, l.version + 1, "
                     "l.model_id, l.deployment_revision_id, l.serving_version_id, %s, "
                     "infrx.now(), 'conformance' from infrx.catalog_listings l where "
                     "l.deployment_revision_id = %s order by l.version desc limit 1",
                     (card.rate_card_version, card.deployment_revision_id))

    return {"credit_balance": credit_balance, "credit_grant": credit_grant,
            "register_credential": lambda auth: register_credential(conn, auth),
            "publish_rate_card": publish_rate_card}


def make_credit_jobstore_factory(fresh_database: Callable[[], str],
                                 dsn_for: Callable[[str], str], seed_sql: str,
                                 **kw: Any) -> Callable[..., Harness]:
    """The v1 rig in the CREDIT regime: the same `PgJobStore` (and its hooks) on a database
    that also carries `seed_credit_world`, plus `credit_hooks`."""
    import psycopg
    jobs_factory = make_jobstore_factory(fresh_database, dsn_for, **kw)

    def factory(limits: PilotSettings | None = None, **_: object) -> Harness:
        harness = jobs_factory(limits)
        conn = psycopg.connect(dsn_for(harness.extra["database"]), autocommit=True)
        seed_credit_world(conn, seed_sql)
        harness.extra.update(credit_hooks(conn))
        harness.extra["credit_conn"] = conn
        return harness

    return factory


def make_lifecycle_factory(fresh_database: Callable[[], str], dsn_for: Callable[[str], str],
                           seed_sql: str, *, upload_window_s: float = 3600.0,
                           grace_s: float = 60.0, claim_ttl_s: float = 30.0,
                           retention_s: float = 600.0,
                           connect_for: Callable[[str], Any] | None = None,
                           **kw: Any) -> Callable[..., Harness]:
    """D10: the F2C.a lifecycle suite's `Harness` (`conformance/lifecycle.py`) on a fresh
    CREDIT-world database: `port` is a `PgLifecycle`, `reopen()` a NEW adapter over the same
    database (another process), `jobs` the CREDIT `PgJobStore` whose `admit_credit` is the
    previous runtime's `infrx.admit` (no marker), `credit_balance` and `set_capability` over
    rows. The windows are the fake factory's test sizes (grace < upload window)."""
    from ..contracts.v2 import fixtures as v2fix
    from .lifecycle import PgLifecycle
    connect_for = connect_for or (lambda name: connector(dsn_for(name)))
    credit_factory = make_credit_jobstore_factory(fresh_database, dsn_for, seed_sql, **kw)

    def factory(limits: PilotSettings | None = None, **_: object) -> Harness:
        credit = credit_factory(limits)
        name, conn = credit.extra["database"], credit.extra["credit_conn"]
        # The suite's second tenant (its content rows need a real organization, R55).
        conn.execute("insert into public.organizations (id, name, slug) values (%s, 'other', "
                     "'other-fixture') on conflict (id) do nothing", (v2fix.IDS.other_org,))

        def reopen() -> PgLifecycle:
            return PgLifecycle(connect_for(name), limits=limits or DEFAULTS,
                               upload_window_s=upload_window_s, grace_s=grace_s,
                               claim_ttl_s=claim_ttl_s, retention_s=retention_s)

        def set_capability(serving_version_id: str, input_modalities: tuple[str, ...]) -> None:
            from psycopg.types.json import Jsonb
            _without_trigger(conn, "infrx.serving_versions", "serving_versions_immutable",
                             "update infrx.serving_versions set capability = jsonb_set("
                             "capability, '{input_modalities}', %s) "
                             "where serving_version_id = %s",
                             (Jsonb(list(input_modalities)), serving_version_id))

        extra = {**credit.extra, "reopen": reopen, "jobs": credit.port,
                 "set_capability": set_capability}
        return Harness(port=reopen(), clock=credit.clock, ids=credit.ids,
                       failures=credit.failures, extra=extra)

    return factory


class JobsView:
    """TEST RIG (Q3 PostgreSQL mode, D3 request 5): the store-side read of a job the Q3 cases
    make of the fake's `jobs[job_id]` - `state`, `terminal`, `attempts` (prepublication
    requeues), the live inference `lease` (its `expires_at`) and `admission.job_handle` -
    over the rows."""

    def __init__(self, conn) -> None:
        self._conn = conn

    def __getitem__(self, job_id: str) -> SimpleNamespace:
        from ..contracts.records import JobState
        row = self._conn.execute(
            "select j.state, j.job_handle, j.settled_at is not null, (select a.expires_at from "
            "infrx.attempts a where a.job_id = j.request_id and a.kind = 'inference' and "
            "a.released_at is null), j.attempts from infrx.jobs j where j.request_id = %s",
            (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        state, handle, terminal, expires, attempts = row
        return SimpleNamespace(state=JobState(state), terminal=terminal, attempts=attempts,
                               admission=SimpleNamespace(job_handle=handle),
                               lease=None if expires is None
                               else SimpleNamespace(expires_at=expires))


class PgDispatchOutbox:
    """TEST RIG (Q3 PostgreSQL mode): D2's dispatch outbox as the real `PgJobStore` serves it,
    plus the store-side reads and faults Q3's cases use on `outboxfake.FakeDispatchOutbox` -
    `unacknowledged()`, `last_error`, `deliveries`, `snapshots` and `ack_faults` ("before":
    the acknowledgment never commits; "after": it commits and the reply is lost, raised as
    `lost`, the caller's `OutboxLost`)."""

    def __init__(self, store: PgJobStore, conn, *, lost: type[Exception] = ConnectionError):
        self.store, self._conn, self._lost = store, conn, lost
        self.ack_faults: list[str] = []
        self.deliveries: dict[str, int] = {}
        self.snapshots = 0

    async def dispatch_pending(self, **kw):
        events = await self.store.dispatch_pending(**kw)
        for event in events:
            self.deliveries[str(event.event_id)] = self.deliveries.get(str(event.event_id), 0) + 1
        return events

    async def acknowledge_dispatch(self, event_ids, *, worker_id: str) -> int:
        fault = self.ack_faults.pop(0) if self.ack_faults else None
        if fault == "before":
            raise self._lost("acknowledgment lost before commit")
        done = await self.store.acknowledge_dispatch(event_ids, worker_id=worker_id)
        if fault == "after":
            raise self._lost("acknowledgment committed, reply lost")
        return done

    async def dispatch_snapshot(self):
        self.snapshots += 1
        return await self.store.dispatch_snapshot()

    async def release_dispatch(self, event_ids) -> int:
        return await self.store.release_dispatch(event_ids)

    async def record_dispatch_error(self, event_id, error: str) -> int:
        return await self.store.record_dispatch_error(event_id, error)

    async def db_now(self):
        return await self.store.db_now()

    async def reopen_dispatch(self, since) -> int:
        return await self.store.reopen_dispatch(since)

    def unacknowledged(self) -> list[str]:
        return [e for e, in self._conn.execute(
            "select event_id::text from infrx.outbox where kind in ('prepare_dispatch', "
            "'inference_dispatch') and acknowledged_at is null "
            "order by available_at, event_id").fetchall()]

    @property
    def last_error(self) -> dict[str, str]:
        return dict(self._conn.execute("select event_id::text, last_error from infrx.outbox "
                                       "where last_error is not null").fetchall())


def make_streamstore_factory(fresh_database: Callable[[], str], dsn_for: Callable[[str], str],
                             **kw: Any) -> Callable[..., Harness]:
    """The same rig with the `PgStreamStore` as the port and the JobStore as `jobs`, one
    `FailurePlan` for both (`crash_after_commit("append")` commits, then raises)."""
    jobs_factory = make_jobstore_factory(fresh_database, dsn_for, **kw)

    def factory(limits: PilotSettings | None = None, **_: object) -> Harness:
        harness = jobs_factory(limits)
        return Harness(port=harness.extra["stream"], clock=harness.clock, ids=harness.ids,
                       failures=harness.failures, extra={**harness.extra, "jobs": harness.port})

    return factory


__all__ = ["CrashAfterCommit", "FailingJobStore", "JobsView", "PgClock", "PgDispatchOutbox",
           "WorkerResults", "credit_hooks",
           "hooks", "make_credit_jobstore_factory", "make_jobstore_factory",
           "make_lifecycle_factory",
           "make_streamstore_factory", "register_credential", "seed", "seed_credit_world"]
