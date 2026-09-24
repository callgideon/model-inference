"""G8's real-PostgreSQL world: `tests/d`'s disposable harness, one fresh database per case.

    INFRX_D_TASK=g8 uv run --frozen pytest -q tests/g/ops     # infrx-g8-postgres, 55447

Each case gets the admission scenario `tests/d` seeds (legacy USD orgs A/B with USD 25
each and their keys, CREDIT individuals C1/C2 granted, the operator's provisional Marlin
card, every flag on), GoTrue's confirmation column, and a freshly bootstrapped operator
key whose secret only the case holds. `ops` is the operator tool's own composition root
(`cli.build_operations`) over the database: the PostgreSQL adapters, nothing faked.
Without Docker every case skips visibly (a skip is never a pass).
"""
from __future__ import annotations

import dataclasses
import uuid

import pytest

from infrx.config import Settings
from infrx.contracts.conformance import builders as b
from infrx.contracts.limits import DEFAULTS
from infrx.operations import cli, service
from infrx.state import migrations
from infrx.state.jobstore import connector

from tests.d import checks_admission, checks_signup, pgharness
from tests.d import checks_credit as cc

UNAVAILABLE = pgharness.unavailable()
needs_pg = pytest.mark.skipif(UNAVAILABLE is not None,
                              reason=f"task-local PostgreSQL unavailable: {UNAVAILABLE}")
R = "support ticket 42"


@dataclasses.dataclass
class World:
    owner: object                   # the migration owner's autocommit connection: seeds, oracles
    database: str
    ops: service.Operations
    operator_secret: str

    @property
    def dsn(self) -> str:
        return pgharness.dsn(self.database)

    def connect(self):
        """A `service_role` connection per call, as the gateway and the operator tool use."""
        return connector(self.dsn)

    def one(self, sql: str, params: tuple = ()):
        row = self.owner.execute(sql, params).fetchone()
        return None if row is None else (row[0] if len(row) == 1 else row)

    def set_flag(self, name: str, enabled: bool) -> None:
        cc.set_flag(self.owner, name, enabled)


def world(label: str) -> World:
    name = f"{pgharness.DATABASE}_{label}"
    pgharness.ensure()
    pgharness.recreate(name)
    pgharness.apply(name, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    owner = pgharness.connect(name)
    checks_admission.seed_admission(owner)
    checks_signup.gotrue_columns(owner)
    owner.execute("update public.api_keys set revoked_at = infrx.now() where audience = 'operator'")
    secret = service.new_secret()
    owner.execute("select infrx.bootstrap_operator_key(%s, 'bootstrap', %s, %s, 'ops@test', "
                  "'operator bootstrap')",
                  (b.ORG_B, secret[:service.PREFIX_CHARS], service.hash_key(secret)))
    ops = cli.build_operations(Settings(pilot=DEFAULTS.replace(database_url=pgharness.dsn(name))))
    return World(owner, name, ops, secret)


def individual(w: World, *, confirmed: bool = True) -> str:
    """A fresh individual; 0001's signup trigger makes the profile and personal org."""
    user = str(uuid.uuid4())
    checks_signup.individual(w.owner, user, f"{user[:13]}@example.com", confirmed=confirmed)
    return user


def personal_org(w: World, user: str) -> str:
    return cc.personal_org(w.owner, user)


def signup_rows(w: World, user: str) -> int:
    """Signup-grant ledger rows of the individual's wallet(s): exactly one, ever."""
    return w.one("select count(*) from infrx.credit_ledger l join infrx.credit_wallets w "
                 "using (wallet_id) where w.owner_user_id = %s and l.kind = 'signup_grant'",
                 (user,))


def drift(w: World) -> list:
    """Every wallet whose summary disagrees with its ledger or live holds, both units
    (0003's and 0006's reconciliation views); `[]` is reconciled."""
    return w.owner.execute(
        "select 'CREDIT', wallet_id::text, ledger_drift, reserved_drift "
        "from infrx.credit_wallet_reconciliation where ledger_drift <> 0 or reserved_drift <> 0 "
        "union all select 'USD', org_id::text, ledger_drift, reserved_drift "
        "from infrx.wallet_reconciliation where ledger_drift <> 0 or reserved_drift <> 0"
    ).fetchall()


# --- jobs, the way the gateway admits them and a worker settles them ------------------
def jobs(w: World):
    from infrx.state.jobstore import PgJobStore
    return PgJobStore(w.connect())


def clocked(w: World):
    """What `builders.request` needs: this database's (frozen) clock and fresh ids."""
    return checks_admission.World(w.owner)


async def admit_legacy(w: World, idem_key: str, *, org: str = b.ORG_A, key: str = b.KEY_A):
    request = b.request(clocked(w), org_id=org, key_id=key)
    return request, await jobs(w).admit(request, b.idem(request, idem_key))


async def admit_credit(w: World, idem_key: str, *, user: str = cc.CONSUMER_1,
                       key: str = checks_admission.C1_KEY, **kw):
    request = checks_admission.credit_request(clocked(w), key, personal_org(w, user), **kw)
    return request, await jobs(w).admit_credit(request, b.idem(request, idem_key))


async def settle(w: World, request, regime: str, *, tokens=None, text: str = "done"):
    """A worker's durable steps with a scripted engine answer: preparation, the claim, the
    result object and the settling transaction in the job's OWN regime."""
    from infrx.contracts.records import Usage
    store = jobs(w)
    await store.prepared(await store.claim_preparation(request.request_id, "prep"))
    lease = await store.claim(request.request_id, "worker")
    ref = await store.put_result(request.request_id, text)
    outcome = b.outcome(request.request_id, clocked(w),
                        tokens=Usage.of(1200, 340) if tokens is None else tokens,
                        result_ref=ref)
    if regime == "legacy_usd":
        return lease, outcome, await store.complete(lease, outcome)
    return lease, outcome, await store.complete_credit(lease, outcome)


def footprint(w: World) -> dict:
    """Every row a transition may not touch, as values: the money relations of both units,
    jobs, holds, usage, cards, listings, keys and the audit trail."""
    tables = ("public.credit_ledger", "infrx.wallets", "infrx.credit_holds",
              "infrx.credit_ledger", "infrx.credit_wallets", "infrx.credit_wallet_holds",
              "infrx.signup_entitlements", "infrx.jobs", "public.usage_events",
              "infrx.rate_card_versions", "infrx.catalog_listings", "public.api_keys",
              "infrx.audit_entries", "infrx.feature_flags")
    return {t: w.owner.execute(f"select md5(coalesce(string_agg(r::text, '|' order by r::text), "
                               f"'')) from {t} r").fetchone()[0] for t in tables}
