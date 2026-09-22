"""E3B.a: BACKEND-JOURNEY - two provisioned tenants, every supported input x mode, no App.

Phase 1 is honest about what exists. The journey matrix is written out case by case and
each one is **PENDING** with the ids that unblock it; the provisioning fixture and the
PostgREST service are real and run today. A pending case is a skip that names its ids, and
`run.py --layer 3` counts it as pending: never a pass.

The matrix (18 §E3B.a, 04 BACKEND-JOURNEY): inputs text / video by URL / video by upload,
modes sync / SSE / explicit async, each for two tenants. Unblocking ids per cell:

* every cell: G1R (the pilot ingress is not what `gateway.app.ROUTERS` mounts), G6B (the
  operator provisioning adapter), D2 (durable admission), D5 (settlement), W3 (worker);
* sync: G2; SSE: G2 + D4 (persistent journal replay); async: G3 + Q3 (dispatch);
* video by URL: nothing more (M2's fetch/probe/persist is merged);
* video by upload: M3 + G4U.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import stack                                            # noqa: E402

COMMON = ("G1R", "G6B", "D2", "D5", "W3")
BY_MODE = {"sync": ("G2",), "sse": ("G2", "D4"), "async": ("G3", "Q3")}
BY_INPUT = {"text": (), "video_url": (), "video_upload": ("M3", "G4U")}


def unblocking(input_kind: str, mode: str) -> tuple[str, ...]:
    return COMMON + BY_MODE[mode] + BY_INPUT[input_kind]


@pytest.mark.parametrize("mode", sorted(BY_MODE))
@pytest.mark.parametrize("input_kind", sorted(BY_INPUT))
def test_backend_journey(input_kind, mode):
    """Two tenants call the metered endpoint; accepted identity, output replay, rate pins
    and exact usage reconcile; the other tenant's handle is 404; nothing leaks."""
    alpha, beta = stack.provision_two_tenants()
    assert alpha.org_id != beta.org_id
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
        stack.pending("G1R", "G3", "D2", "D5", "G6B",
                      why="resume needs idempotent explicit jobs on the metered endpoint")
    pytest.fail("the pilot ingress is mounted: write the dataset-resume journey body now")


# ------------------------------------------------------------------ provisioning fixture

def test_two_tenants_are_provisioned_with_their_own_resolved_wallets_and_pins():
    """The fixture the journeys use (v2 fakes until G6B): distinct users, orgs and keys;
    each wallet is resolved FROM the credential (R66) and the other tenant's wallet is
    refused rather than used; both pin the same published deployment and rate card."""
    from infrx.contracts import errors
    from infrx.contracts.fakes.factories import jobstore_factory
    from infrx.contracts.v2 import ports

    h = jobstore_factory()
    alpha, beta = stack.provision_two_tenants(h.port, grant="5")
    assert len({alpha.user_id, beta.user_id}) == len({alpha.org_id, beta.org_id}) == 2
    assert alpha.wallet.owner_user_id == alpha.user_id
    assert alpha.wallet.personal_org_id == alpha.org_id
    with pytest.raises(errors.Forbidden):
        ports.resolve_wallet(alpha.auth, beta.wallet)
    assert alpha.pins == beta.pins
    assert alpha.provisioned_by == "v2-fakes (G6B pending)"
    for tenant in (alpha, beta):
        balance = h.extra["balance"](tenant.org_id)
        assert (balance["ledger"], balance["reserved"]) == (5, 0), balance


def test_an_unknown_pending_id_is_refused():
    """The pending vocabulary is closed: a typo cannot invent a new way to not run."""
    with pytest.raises(AssertionError):
        stack.pending("G9Z", why="not a task")


# ------------------------------------------------------------------ PostgREST (layer 3)

def _postgrest_or_skip():
    if stack.postgrest_owner() != "ours":
        pytest.skip("no infrx-e3b-postgrest of this checkout: run "
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


def test_postgrest_member_session_sees_its_own_organization():
    """A member's JWT through PostgREST reads its own organization. E2R measured that the
    pinned image's `auth.uid()` reads only the legacy claim GUC while PostgREST 13 sets only
    the JSON claims; which pairing the deployment runs is I2B's (and the console's) call, so
    the observation is recorded and the case stays pending on it."""
    import harness
    state = harness.load_state() or {}
    owner = (state.get("fixtures") or {}).get("principals", {}).get("owner_alpha")
    if not owner:
        pytest.skip("no seeded fixtures: run `tests/integration/run.py --layer 3`")
    with _postgrest_or_skip() as client:
        answer = client.get("/organizations", params={"select": "id"},
                            headers={"Authorization":
                                     f"Bearer {stack.jwt('authenticated', owner['user_id'])}"})
    rows = answer.json() if answer.status_code == 200 else None
    if rows:
        assert all(row["id"] for row in rows)
        return
    stack.pending("I2B", why=f"member JWT via PostgREST 13.0.4 observed status "
                             f"{answer.status_code}, rows {rows!r}: auth.uid() does not read "
                             f"the claim form this PostgREST sets")
