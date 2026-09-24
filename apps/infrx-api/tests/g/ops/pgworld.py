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
