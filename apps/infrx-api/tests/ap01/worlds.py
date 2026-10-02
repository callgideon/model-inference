"""AP-01's identity world on two stores: in memory (`FakeIdentity` beside L2's
`FakeAccessStore`) and PostgreSQL on the ap1 harness - `state.identity.PgIdentity` over 0065's
functions (`pg`), with `PgAccessStore` and 0060's claims. The same people in all:

- CONSUMER: verified, claimed the signup grant (a consumer wallet); SUSPENDED: the same, and
  its organization is suspended; FRESH: verified, no grant yet (onboarding); UNVERIFIED: the
  email is not confirmed; OPERATOR: `profiles.is_operator`, no wallet;
- provider A ("lab-a"): ADMIN_A administrator, DEV_A developer, VIEWER_A viewer; provider B
  ("lab-b"): DEV_B developer; NEMO ("nemostation") owns the model `nemostation/marlin-2b-test`.

Sessions are the GoTrue stub's (`infrx.auth_facade.stub`), verified by the real
`GoTrueSessions`, so the whole session path runs in both worlds.
"""
from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta

from infrx.auth_facade.stub import GoTrueStub
from infrx.console.session import Account, Claim, Member, Provider
from infrx.contracts import errors
from infrx.state import identity as functions
from infrx.state.control_ops import owner_of
from infrx.contracts.v2.records import ProviderMembership
from infrx.lab.access import LabAccess
from infrx.lab.access.fakes import FakeAccessStore
from infrx.state.jobstore import connector
from infrx.state.lab_access import PgAccessStore

from tests.d import checks_signup

T0 = datetime(2026, 9, 20, 12, tzinfo=UTC)


def _id(n: int) -> str:
    return f"a1a1a1a1-0000-4000-8000-{n:012d}"


CONSUMER, FRESH, UNVERIFIED, SUSPENDED, OPERATOR = (_id(n) for n in range(1, 6))
ADMIN_A, DEV_A, VIEWER_A, DEV_B = (_id(n) for n in range(6, 10))
A, B, NEMO = (_id(n) for n in range(11, 14))
USERS = {CONSUMER: "consumer", FRESH: "fresh", UNVERIFIED: "unverified",
         SUSPENDED: "suspended", OPERATOR: "operator", ADMIN_A: "admin-a", DEV_A: "dev-a",
         VIEWER_A: "viewer-a", DEV_B: "dev-b"}
EMAIL = {user: f"{name}@example.com" for user, name in USERS.items()}
GRANTED = (CONSUMER, SUSPENDED)
SLUG = {A: "lab-a", B: "lab-b", NEMO: "nemostation"}
NAME = {A: "Lab A", B: "Lab B", NEMO: "NemoStation"}
ROLES = {(A, ADMIN_A): "administrator", (A, DEV_A): "developer", (A, VIEWER_A): "viewer",
         (B, DEV_B): "developer"}
MODEL = "nemostation/marlin-2b-test"
GRANT = "10000.00000000"


def stub() -> GoTrueStub:
    made = GoTrueStub(apikey="anon-key")
    for user, name in USERS.items():
        made.user(EMAIL[user], user_id=user)
    return made


# --- in memory ------------------------------------------------------------------------------
@dataclasses.dataclass
class FakeIdentity:
    """`IdentityStore` in memory, writing memberships into L2's fake store so `LabAccess`
    judges them as it judges the database's."""

    store: FakeAccessStore
    emails: dict[str, str] = dataclasses.field(default_factory=lambda: dict(EMAIL))
    providers: dict[str, Provider] = dataclasses.field(default_factory=dict)
    flag: bool = True
    claims: dict = dataclasses.field(default_factory=dict)

    async def _once(self, claim: Claim, write):
        """0060's rules in memory: a replay answers the first outcome without writing, another
        request under the key is 409, a refused write claims nothing."""
        scope = (owner_of(claim.actor), claim.kind, claim.key)
        if scope in self.claims:
            digest, outcome = self.claims[scope]
            if digest != claim.input_hash:
                raise errors.IdempotencyConflict("this Idempotency-Key was used with a "
                                                 "different request")
            return outcome, True
        outcome = await write()
        self.claims[scope] = (claim.input_hash, outcome)
        return outcome, False

    async def account(self, user_id: str) -> Account | None:
        if user_id not in self.emails:
            return None
        granted = user_id in GRANTED
        return Account(user_id=user_id, operator=user_id == OPERATOR,
                       verified=user_id != UNVERIFIED, org_id=FakeWorld.org(user_id),
                       wallet=granted, suspended=user_id == SUSPENDED,
                       grant_amount=GRANT if granted else None,
                       granted_at=T0 if granted else None)

    async def signup_grant_enabled(self) -> bool:
        return self.flag

    async def user_by_email(self, email: str) -> str | None:
        found = [u for u, e in self.emails.items() if e.lower() == email.lower()]
        return found[0] if len(found) == 1 else None

    def _member(self, m: ProviderMembership) -> Member:
        return Member(user_id=m.user_id, email=self.emails[m.user_id], role=m.role.value,
                      granted_by=m.granted_by, granted_at=m.granted_at, revoked_at=m.revoked_at)

    async def members(self, provider_org_id: str) -> list[Member]:
        return [self._member(m) for (p, _), m in sorted(self.store.memberships.items(),
                                                        key=lambda kv: kv[1].granted_at)
                if p == provider_org_id and m.revoked_at is None]

    async def add_member(self, provider_org_id, user_id, role, granted_by, claim):
        async def write():
            return await self._grant(provider_org_id, user_id, role, granted_by)
        (member, created), replayed = await self._once(claim, write)
        return member, created and not replayed

    async def _grant(self, provider_org_id, user_id, role, granted_by):
        current = self.store.memberships.get((provider_org_id, user_id))
        created = current is None or current.revoked_at is not None
        if created:
            current = ProviderMembership(provider_org_id=provider_org_id, user_id=user_id,
                                         role=role, granted_by=granted_by,
                                         granted_at=self.store.now)
            self.store.memberships[(provider_org_id, user_id)] = current
        if current.role != role:
            raise errors.Conflict("the user holds another current role in this workspace")
        return self._member(current), created

    async def revoke_member(self, provider_org_id, user_id, claim):
        async def write():
            return await self._revoke(provider_org_id, user_id)
        return (await self._once(claim, write))[0]

    async def _revoke(self, provider_org_id, user_id):
        current = self.store.memberships.get((provider_org_id, user_id))
        if current is None:
            raise errors.NotFound("no such member")
        if current.revoked_at is None:
            current = current.model_copy(update={"revoked_at": self.store.now})
            self.store.memberships[(provider_org_id, user_id)] = current
        return self._member(current)

    async def create_provider(self, slug, display_name, created_by, administrator, claim):
        async def write():
            provider, created = await self._create(slug, display_name, created_by)
            admin = None if administrator is None else (await self._grant(
                provider.provider_org_id, administrator, "administrator", created_by))[0]
            return provider, admin, created
        (provider, admin, created), replayed = await self._once(claim, write)
        return provider, admin, created and not replayed

    async def _create(self, slug, display_name, created_by):
        provider = self.providers.get(slug)
        created = provider is None
        if created:
            provider = Provider(provider_org_id=_id(100 + len(self.providers)), slug=slug,
                                display_name=display_name, created_by=created_by,
                                created_at=self.store.now)
            self.providers[slug] = provider
            self.store.provider_names[provider.provider_org_id] = display_name
        if provider.display_name != display_name:
            raise errors.Conflict("a provider with this slug already exists")
        return provider, created


class FakeWorld:
    name = "fake"

    def __init__(self) -> None:
        self.stub = stub()
        store = FakeAccessStore(now=T0 + timedelta(minutes=1),
                                provider_names={p: NAME[p] for p in NAME})
        for (provider, user), role in ROLES.items():
            store.memberships[(provider, user)] = ProviderMembership(
                provider_org_id=provider, user_id=user, role=role, granted_by="ops",
                granted_at=T0)
        self.identity = FakeIdentity(store, providers={
            SLUG[p]: Provider(provider_org_id=p, slug=SLUG[p], display_name=NAME[p],
                              created_by="ops", created_at=T0) for p in NAME})
        self.access = LabAccess(store)

    @staticmethod
    def org(user: str) -> str:
        return f"0a0a0a0a-0000-4000-8000-{user[-12:]}"

    def model_owner(self) -> str:
        return NEMO


# --- PostgreSQL -------------------------------------------------------------------------------
def seed_pg(conn) -> None:
    """The people, the grants, the suspension, the providers and NemoStation's model, as the
    platform's own tooling would write them (no API involved)."""
    checks_signup.gotrue_columns(conn)
    for user in USERS:
        conn.execute("insert into auth.users (id, email, email_confirmed_at) values "
                     "(%s, %s, %s)", (user, EMAIL[user], None if user == UNVERIFIED else T0))
    conn.execute("update public.profiles set is_operator = true where id = %s", (OPERATOR,))
    conn.execute("update infrx.feature_flags set enabled = true where name = 'signup_grant'")
    for user in GRANTED:
        status, = conn.execute("select status from public.claim_signup_grant(%s, %s, null)",
                               (user, "consumer-v1")).fetchone()
        assert status == "granted", (user, status)
    conn.execute("update public.organizations set suspended = true, suspended_at = now(), "
                 "suspension_reason = 'other' where id = %s", (PgWorld.org_of(conn, SUSPENDED),))
    for provider in NAME:
        conn.execute("insert into infrx.provider_orgs (provider_org_id, slug, display_name, "
                     "created_by) values (%s, %s, %s, 'ops')",
                     (provider, SLUG[provider], NAME[provider]))
    for (provider, user), role in ROLES.items():
        conn.execute("insert into infrx.provider_memberships (provider_org_id, user_id, role, "
                     "granted_by) values (%s, %s, %s, 'ops')", (provider, user, role))
    conn.execute(
        "insert into public.models (id, name, provider, description, status, base_url, "
        "served_model, input_usd_per_m, output_usd_per_m, context_tokens, input_modalities, "
        "output_modalities, provider_org_id) values (%s, 'm', 'NemoStation', 'm', 'live', "
        "'https://m.example', 'm', 1, 1, 1024, '{text}', '{text}', %s)", (MODEL, NEMO))


class PgWorld:
    name = "pg"

    def __init__(self, conn, dsn: str) -> None:
        self.conn = conn
        self.stub = stub()
        self.identity = functions.PgIdentity(connector(dsn))
        self.access = LabAccess(PgAccessStore(connector(dsn)))

    @staticmethod
    def org_of(conn, user: str) -> str:
        return str(conn.execute("select personal_org_id from infrx.verified_user(%s)",
                                (user,)).fetchone()[0])

    def org(self, user: str) -> str:
        return self.org_of(self.conn, user)

    def model_owner(self) -> str:
        return str(self.conn.execute("select provider_org_id from public.models where id = %s",
                                     (MODEL,)).fetchone()[0])
