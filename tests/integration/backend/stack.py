"""E3B phase 1: what the backend gate adds on top of E2's layer-2 stack.

E2's `infrx-e2` stack already runs PostgreSQL (migrations 0001-0005), Valkey (the selected
queue mode, driven through Q2's `ValkeyScheduler`) and S3-compatible storage. The backend
profile adds the one service it lacks, a pinned **PostgREST** (`backend/compose.yaml`,
project `infrx-e3b`), attached to E2's network. Nothing here starts a GPU, a Next.js app or
anything hosted.

Three things live here, each small:

* `PENDING` - the only vocabulary a pending case may use. A case that cannot run today skips
  with `PENDING[<ids>]`, every id must be a task or P-input of `research/plan`, and
  `run.py --layer 3` counts those skips as pending, never as passes.
* `provision_two_tenants` - the two-tenant fixture. **G6B call site**: today it is built on
  the contracts-v2 fakes (`conformance/v2_fakes.py`) plus the v1 fake JobStore's grant hook;
  when G6B's operator adapter merges, this one function is what switches to it.
* `postgrest_*` - lifecycle of `infrx-e3b-postgrest`, ownership by label exactly like E2's.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import harness                                          # noqa: E402

# `infrx` normally comes from the checkout. The E3B mutants (tests/integration/mutants.py)
# run the backend suite against a *copy* of `infrx` through PYTHONPATH, and
# `api_on_path()` would put the real one in front of it, so only add it when nothing else
# provides the package.
if importlib.util.find_spec("infrx") is None:
    harness.api_on_path()

# ------------------------------------------------------------------ pending vocabulary

# Every id a pending case may name, with what it delivers. A typo is a failure, not a new
# kind of pending: `pending()` refuses an id that is not here.
PENDING = {
    "G1R": "pilot ingress mounted in gateway.app.ROUTERS (cutover from the legacy chat "
           "route) with consumer/provider audiences",
    "G2": "synchronous chat and the persistent SSE relay over the journal",
    "G3": "explicit async job routes: create, status, cancel, replay",
    "G4U": "owned upload HTTP adapter",
    "G6B": "operator provisioning adapter (infrx/operations): identities, keys, wallets",
    "D1R": "product-v2 schema (CREDIT wallets, rate cards, pins) in PostgreSQL",
    "D2": "atomic admission RPC: job + hold + reservations + outbox in one transaction",
    "D3": "fenced leases, recovery and cancellation RPCs in PostgreSQL",
    "D4": "persistent stream journal append/replay RPCs in PostgreSQL",
    "D5": "terminal settlement transaction, grants and reconciliation in PostgreSQL",
    "M3": "owned uploads, expiry and orphan collection",
    "Q3": "outbox dispatcher/reconciler feeding the index from PostgreSQL",
    "W3": "worker wiring: drain, engine pin, media root, measured concurrency",
    "I2B": "the deployed PostgREST/auth pairing (claim form auth.uid() reads)",
    "P-04": "allocated GPU target with the pinned Marlin engine",
}


def pending(*ids: str, why: str):
    """Skip as PENDING. Never a pass: `run.py --layer 3` counts it, and the stage exits 3."""
    import pytest
    unknown = [task for task in ids if task not in PENDING]
    if not ids or unknown:
        raise AssertionError(f"a pending case must name known unblocking ids, got {ids}")
    pytest.skip(f"PENDING[{','.join(ids)}] {why}")


def ingress_is_mounted() -> bool:
    """The probe every journey case runs first: is the pilot ingress (G1R's cutover) the
    router the composition root mounts? Today it is not - `ROUTERS` is the legacy chat
    route - and the day it is, every journey case stops being pending and fails until its
    body is written, rather than passing on an empty body."""
    from infrx.gateway import app as composition
    return any(module.__name__.endswith(".ingress") for module in composition.ROUTERS)


# ------------------------------------------------------------------ two tenants

@dataclass(frozen=True)
class Tenant:
    name: str
    user_id: str
    org_id: str
    key_id: str
    auth: object          # contracts.v2 AuthContextV2 (consumer audience)
    auth_v1: object       # contracts v1 AuthContext for the v1 JobStore ports
    wallet: object        # contracts.v2 WalletRef, RESOLVED from the auth context
    pins: object          # contracts.v2 AdmissionPins for the published Marlin deployment
    provisioned_by: str


def _uuid(name: str) -> str:
    """Deterministic, and a UUIDv4 in shape (the records refuse any other version)."""
    return str(uuid.UUID(bytes=uuid.uuid5(uuid.NAMESPACE_URL, f"infrx-e3b/{name}").bytes,
                         version=4))


def provision_two_tenants(jobs=None, *, grant: str = "5") -> tuple[Tenant, Tenant]:
    """Two consumer tenants, each with a user, a personal org, a scoped key, a wallet
    resolved from the credential (R66) and admission pins for the public Marlin deployment.

    G6B CALL SITE. The operator adapter does not exist on this base, so identities come
    from the v2 fakes and are recorded as `provisioned_by = "v2-fakes (G6B pending)"`. The
    replacement is this function only; the journeys never build a tenant any other way.

    `jobs` is a v1 fake JobStore (or, later, the real one): the grant goes through its
    `grant` hook, the one way a test gives an org balance - never a direct wallet edit.
    `grant` is in the v1 pilot regime's USD-shaped numbers; CREDIT is D1R's (R64/R65: no
    conversion exists, so none is attempted here).
    """
    from infrx.contracts import records as v1
    from infrx.contracts.conformance import v2_fakes
    from infrx.contracts.v2 import fixtures as v2fix, ports, records as v2

    directory = v2_fakes.fake_v2_harness()
    tenants = []
    for name in ("alpha", "beta"):
        user_id, org_id, key_id = (_uuid(f"{name}/user"), _uuid(f"{name}/org"),
                                   _uuid(f"{name}/key"))
        directory.wallets.by_user[user_id] = v2.WalletRef(
            wallet_id=_uuid(f"{name}/wallet"), kind=v2.WalletKind.consumer,
            owner_user_id=user_id, personal_org_id=org_id)
        auth = v2.AuthContextV2(audience=v2.CredentialAudience.consumer, org_id=org_id,
                                key_id=key_id, principal=key_id, role=v1.Role.owner,
                                entitlement_version=1, user_id=user_id)

        async def resolve(auth=auth):
            wallet = ports.resolve_wallet(
                auth, await directory.wallets.consumer_wallet_for_user(auth.user_id))
            deployment = await directory.catalog.resolve(
                v2fix.REQUESTED_MODEL, audience=auth.audience, endpoint_id=auth.endpoint_id)
            pins, _card = ports.pin_admission(
                auth=auth, requested_model=v2fix.REQUESTED_MODEL, deployment=deployment,
                serving=await directory.catalog.serving_revision(deployment.serving_version_id),
                rate_card=await directory.catalog.active_rate_card(
                    deployment.deployment_revision_id),
                policy=await directory.catalog.data_access_policy(
                    deployment.deployment_revision_id))
            return wallet, pins

        wallet, pins = asyncio.run(resolve())
        if jobs is not None:
            jobs.grant(org_id, grant)
        tenants.append(Tenant(
            name=name, user_id=user_id, org_id=org_id, key_id=key_id, auth=auth,
            auth_v1=v1.AuthContext(org_id=org_id, key_id=key_id, principal=key_id,
                                   role=v1.Role.owner, entitlement_version=1),
            wallet=wallet, pins=pins, provisioned_by="v2-fakes (G6B pending)"))
    return tenants[0], tenants[1]


# ------------------------------------------------------------------ PostgREST

COMPOSE_FILE = HERE / "compose.yaml"
PROJECT = "infrx-e3b"
POSTGREST = f"{PROJECT}-postgrest"
POSTGREST_PORT = 55530                     # inside E's 55500-55599 (08 §8), loopback only
CHECKOUT_LABEL = "ai.infrx.e3b.checkout"
# A local literal that exists in no other file and signs only tokens this suite mints.
JWT_SECRET = "infrx-e3b-local-jwt-secret-not-a-real-one-0001"


def postgrest_url() -> str:
    return f"http://127.0.0.1:{POSTGREST_PORT}"


def _checkout() -> str:
    return os.environ.get("INFRX_E3B_CHECKOUT") or str(HERE)


def _compose(*args: str, check: bool = True):
    return harness.run(["docker", "compose", "-p", PROJECT, "-f", str(COMPOSE_FILE), *args],
                       check=check, timeout=300.0,
                       env={"INFRX_E3B_CHECKOUT": _checkout()})


def postgrest_owner() -> str | None:
    """`None` if absent, "ours" if this checkout created it, else the foreign label."""
    import shutil
    if shutil.which("docker") is None:
        return None
    probe = harness.run(["docker", "inspect", POSTGREST, "--format",
                         "{{json .Config.Labels}}"], check=False, timeout=60)
    if probe.returncode != 0:
        return None
    labels = json.loads(probe.stdout.strip() or "null") or {}
    return "ours" if labels.get(CHECKOUT_LABEL) == _checkout() else \
        f"foreign ({labels.get(CHECKOUT_LABEL)!r})"


def postgrest_up() -> str:
    """Start PostgREST on E2's network and wait for it. Refuses a container it did not
    create. PostgREST reads its schema cache at start, so this runs AFTER `migrate`."""
    owner = postgrest_owner()
    if owner not in (None, "ours"):
        raise harness.HarnessError(f"refusing to touch {POSTGREST}: {owner}")
    _compose("up", "-d", "--no-build", "--force-recreate")
    import time

    import httpx
    last = ""
    for _ in range(60):
        try:
            answer = httpx.get(postgrest_url() + "/", timeout=2.0)
            if answer.status_code < 500:
                return answer.headers.get("server", "postgrest")
            last = f"{answer.status_code} {answer.text[:120]}"
        except httpx.HTTPError as exc:
            last = type(exc).__name__
        time.sleep(1.0)
    raise harness.HarnessError(f"{POSTGREST} not ready: {last}")


def postgrest_down() -> list[str]:
    """Remove exactly our container (it must go before E2's network can)."""
    owner = postgrest_owner()
    if owner is None:
        return []
    if owner != "ours":
        raise harness.HarnessError(f"refusing to remove {POSTGREST}: {owner}")
    _compose("down", "--remove-orphans", check=False)
    if postgrest_owner() is not None:
        raise harness.HarnessError(f"{POSTGREST} survived its teardown")
    return [POSTGREST]


def jwt(role: str, sub: str | None = None) -> str:
    """HS256, stdlib only: a token only this suite's PostgREST accepts."""
    import base64
    import hashlib
    import hmac
    import time

    def b64(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    claims = {"role": role, "exp": int(time.time()) + 600}
    if sub:
        claims["sub"] = sub
    head = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = b64(json.dumps(claims).encode())
    sig = hmac.new(JWT_SECRET.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{b64(sig)}"
