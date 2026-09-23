"""E3B.a: BACKEND-JOURNEY - two provisioned tenants, every supported input x mode, no App.

Phase 1 is honest about what exists. The journey matrix is written out case by case and
each one is **PENDING** with the ids that unblock it; the provisioning fixture and the
PostgREST service are real and run today. A pending case is a skip that names its ids, and
`run.py --layer 3` counts it as pending: never a pass.

The matrix (18 §E3B.a, 04 BACKEND-JOURNEY): inputs text / video by URL / video by upload,
modes sync / SSE / explicit async, each for two tenants. Unblocking ids per cell (E3B phase
2: only unmerged tasks; G1R, G6B, D2, D3, D4, W3, Q3, M3, F2P and G4U have merged):

* every cell: D5 (settlement, and the PostgreSQL adapters the pilot composes with);
* sync and SSE: G2 (the relay and the cutover that mounts the ingress; D4's persistent
  journal merged); async: G2 + G3 (the job routes);
* video by URL and by upload: nothing more (M2's fetch/probe/persist, M3's uploads and
  G4U's upload routes are merged; the routes are mounted by G2's cutover).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import stack                                            # noqa: E402

COMMON = ("D5",)
BY_MODE = {"sync": ("G2",), "sse": ("G2",), "async": ("G2", "G3")}
BY_INPUT = {"text": (), "video_url": (), "video_upload": ()}


def unblocking(input_kind: str, mode: str) -> tuple[str, ...]:
    return COMMON + BY_MODE[mode] + BY_INPUT[input_kind]


@pytest.mark.parametrize("mode", sorted(BY_MODE))
@pytest.mark.parametrize("input_kind", sorted(BY_INPUT))
def test_backend_journey(input_kind, mode):
    """Two tenants call the metered endpoint; accepted identity, output replay, rate pins
    and exact usage reconcile; the other tenant's handle is 404; nothing leaks."""
    if not stack.ingress_is_mounted():
        stack.pending(*unblocking(input_kind, mode),
                      why=f"{input_kind} x {mode}: the pilot ingress is not mounted, so "
                          f"there is no metered endpoint to call (legacy chat route only)")
    pytest.fail(f"the pilot ingress is mounted: write the {input_kind} x {mode} journey "
                f"body now (E3B phase 2) - an unwritten journey is not a pass")


def test_backend_journey__dataset_client_resume():
    """04 BACKEND-JOURNEY: resume a bounded dataset client; no duplicate accepted items or
    charges after an interrupted run (E1B's bench client is the client)."""
    if not stack.ingress_is_mounted():
        stack.pending("G2", "G3", "D5",
                      why="resume needs idempotent explicit jobs on the metered endpoint")
    pytest.fail("the pilot ingress is mounted: write the dataset-resume journey body now")


# ------------------------------------------------------------------ provisioning fixture

def test_two_tenants_are_provisioned_with_their_own_resolved_wallets_and_pins(caplog):
    """API-OPS (R66, R71, R72, R85): the fixture the journeys use, through G6B's `Operations`
    on the real store - distinct users, orgs and keys; A1's real grant (10,000 CREDIT) read
    back through `tenant(secret).balance()`; each wallet RESOLVED from the credential and the
    other tenant's refused; an operator key spends no wallet; both pin the same published
    deployment; a replayed grant under another idempotency key is the same grant; no secret
    reaches a log line or an argv."""
    import logging

    from infrx.contracts import errors
    from infrx.contracts.v2 import ports
    from infrx.operations import cli

    caplog.set_level(logging.DEBUG)
    world = stack.provision_two_tenants()
    alpha, beta = world.alpha, world.beta
    assert len({alpha.user_id, beta.user_id}) == len({alpha.org_id, beta.org_id}) == 2
    assert alpha.key_id != beta.key_id
    assert (alpha.wallet.owner_user_id, alpha.wallet.personal_org_id) == (alpha.user_id,
                                                                          alpha.org_id)
    with pytest.raises(errors.Forbidden):
        ports.resolve_wallet(alpha.auth, beta.wallet)
    assert alpha.pins == beta.pins
    assert "real: IdentityDirectory=PgSignup" in alpha.provisioned_by
    assert "fake: TenantStore, AuditLog, Registry, AccountView" in alpha.provisioned_by

    async def checks():
        for tenant in (alpha, beta):
            balance = await (await world.ops.tenant(tenant.secret)).balance()
            assert (str(balance.ledger_total), str(balance.reserved_total)) == (
                "10000.00000000", "0.00000000"), balance
        with pytest.raises(errors.Forbidden):
            await world.ops.tenant(world.operator_secret)
        return await assert_grant_replays(world, "alpha")
    stack.asyncio.run(checks())
    secrets = (alpha.secret, beta.secret, world.operator_secret)
    assert all(secrets) and not any(secret in caplog.text for secret in secrets)
    assert not any(secret in repr(tenant) for tenant in (alpha, beta) for secret in secrets)
    for secret in secrets:
        with pytest.raises(SystemExit, match="refusing a key on the command line"):
            cli.refuse_secret_argv(["grant", "--user", alpha.user_id, secret])


async def assert_grant_replays(world, name):
    """R71: a second grant for the same individual under ANOTHER idempotency key reaches the
    database (the operator's audit dedupe cannot answer it) and is the same grant."""
    tenant = getattr(world, name)
    operator = await world.ops.operator(world.operator_secret)
    again = await operator.grant_initial(tenant.user_id, idempotency_key=f"e3b2-regrant-{name}",
                                         reason="E3B2 replay drill")
    balance = await (await world.ops.tenant(tenant.secret)).balance()
    first = world.grants[name]
    assert (again["replayed"], again["ledger_operation_id"], str(balance.ledger_total)) == (
        True, first["ledger_operation_id"], "10000.00000000"), f"R71: not one grant: {again}"


def _signup_grant_not_unique() -> None:
    """The grant's uniqueness dropped, all four layers of it: the entitlement's key, the
    one-signup-grant-per-wallet ledger index, the replay answer of `claim_signup_grant` and
    the once-only guard of `grant_signup_credit`."""
    claim = stack.function_source("public.claim_signup_grant", "uuid, text, uuid")
    grant = stack.function_source("infrx.grant_signup_credit", "uuid, text, text, uuid")
    replay = ("if exists (select 1 from infrx.signup_entitlements e\n"
              "             where e.user_id = p_user_id and e.entitlement = "
              "'initial_signup_grant') then")
    once = ("if not exists (select 1 from infrx.signup_entitlements e\n"
            "                 where e.user_id = p_user_id and e.entitlement = "
            "'initial_signup_grant') then")
    assert claim.count(replay) == 1 and grant.count(once) == 1, "0006/0015 moved: stale drill"
    stack.defect("alter table infrx.signup_entitlements "
                 "drop constraint signup_entitlements_pkey")
    stack.defect("drop index infrx.credit_ledger_one_signup_grant_per_wallet")
    stack.defect(claim.replace(replay, "if false then"))
    stack.defect(grant.replace(once, "if true then"))


def test_e3b_db09_detects_a_signup_grant_that_is_not_unique():
    """Intentional defect on the REAL store (API-OPS, R71): with the grant's uniqueness
    dropped on the provisioning clone, the replay case must report a second grant."""
    world = stack.provision_two_tenants()
    _signup_grant_not_unique()
    with pytest.raises(AssertionError, match="R71: not one grant"):
        stack.asyncio.run(assert_grant_replays(world, "alpha"))


def test_an_unknown_pending_id_is_refused():
    """The pending vocabulary is closed: a typo cannot invent a new way to not run."""
    with pytest.raises(AssertionError):
        stack.pending("G9Z", why="not a task")


# ------------------------------------------------------------------ PostgREST (layer 3)

def _postgrest_or_skip():
    if stack.postgrest_owner() != "ours":
        pytest.skip(f"no {stack.POSTGREST} of this checkout: run "
                    "`tests/integration/run.py --layer 3`")
    import httpx
    return httpx.Client(base_url=stack.postgrest_url(), timeout=5.0)


def test_postgrest_refuses_anon_on_the_tenant_tables():
    """R59-4 through the real HTTP layer: `anon` has no grant on `organizations` after 0004,
    so PostgREST answers a permission error, not an empty 200 list."""
    with _postgrest_or_skip() as client:
        answer = client.get("/organizations", params={"select": "id"})
    assert answer.status_code in (401, 403), (answer.status_code, answer.text[:200])
    assert answer.json().get("code") == "42501", answer.text[:200]


def test_postgrest_service_role_reads_every_tenant():
    """The service role is what the gateway's PostgREST path uses; it bypasses RLS
    (E2R Limits 4), so tenant safety there is route-side - this measures that it does."""
    with _postgrest_or_skip() as client:
        answer = client.get("/organizations", params={"select": "id"},
                            headers={"Authorization": f"Bearer {stack.jwt('service_role')}"})
    assert answer.status_code == 200, answer.text[:200]
    assert len(answer.json()) >= 2


def test_postgrest_member_session_is_nobody_on_the_pinned_pairing():
    """A MEASUREMENT of the pinned pairing, over real HTTP: a member's JWT through PostgREST
    13.0.4 to the pinned `supabase/postgres` 17.6.1.173 is answered 200 with NO rows - the
    image's `auth.uid()` reads only the legacy `request.jwt.claim.sub` GUC and PostgREST 13
    sets only the JSON `request.jwt.claims` (E2R's SQL-level measurement, reproduced end to
    end). Hosted `auth.uid()` reads both forms (I1B), so the pinned image is the odd one out;
    bumping it is E's integration request. The case fails the day the pairing changes, so the
    assertion is updated deliberately rather than drifting."""
    import harness
    state = harness.load_state() or {}
    owner = (state.get("fixtures") or {}).get("principals", {}).get("owner_alpha")
    if not owner:
        pytest.skip("no seeded fixtures: run `tests/integration/run.py --layer 3`")
    with _postgrest_or_skip() as client:
        answer = client.get("/organizations", params={"select": "id"},
                            headers={"Authorization":
                                     f"Bearer {stack.jwt('authenticated', owner['user_id'])}"})
    assert (answer.status_code, answer.json()) == (200, []), answer.text[:200]
