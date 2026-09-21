"""The pilot ingress router: `register(app, rt)` (r1 R44).

What this module owns is the part of a request that happens before any durable
state exists: mint the identity, bound the body, authenticate the tenant, validate
the shape, and hand a `NormalizedRequest` plus an `AuthContext` to whatever accepts
it. It never touches money, capacity or the queue - `JobStore.admit` does all
three in one transaction, and G never duplicates settlement in a route.

Three seams, all on one injected object (`IngressDeps`) so the coordinator's
integration request is a single wiring change rather than four:

* `accept(auth, request, idem)` - durable acceptance (D2 through G2/G3). Until it
  is wired, a request that passes every check is answered `503
  dependency_unavailable` with retry guidance: the correct answer for "the durable
  dependency is not there", and never a 200 that implies work was accepted.
* `checks` - the startup reachability probes of `M-FAILCLOSED`: in `pilot` the
  price source and the journal must both be present **and** answer, or the router
  refuses to register, which refuses to start. A missing probe is not a passing
  probe.
* `consent_for` / `entitlement_version` - the trace policy and entitlement version
  sources; both default to the fail-safe answer (`off`, version 0).

Cutover (the integration request): the coordinator replaces `chat` with this
module in `app.ROUTERS` and flips `config.validate_runtime`'s unset branch from
`legacy` to a refusal, together with I2's fail-closed installer. Until then this
module is mounted by nobody, which is why registering it on an app that still
carries the legacy chat route leaves that route in charge of its path.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from fastapi import Request
from fastapi.responses import JSONResponse

from ...auth.context import AuthResolver
from ...config import RuntimeMisconfigured
from ...contracts import errors, ids, wire
from . import intake
from .validate import Validator, idempotency

CHAT_PATH = "/v1/chat/completions"
HEALTH_PATH = "/healthz"
READY_PATH = "/readyz"
CHAT_OPERATION = "chat.completions"
# `pilot` asserts these at startup (infra/README.md `M-FAILCLOSED`): a usable price
# version for the served model must resolve (contracts v1: a missing model or rate
# rejects admission) and the journal must be reachable, because an unjournalled
# pilot cannot honour the output guarantees it makes.
REQUIRED_CHECKS = ("price_source", "journal")
OK = "ok"
UNAVAILABLE = "unavailable"


@dataclass
class IngressDeps:
    """Everything the ingress needs from outside itself. Defaults fail safe."""

    accept: Callable | None = None
    checks: dict[str, Callable[[], bool]] = field(default_factory=dict)
    consent_for: Callable | None = None
    entitlement_version: Callable[[str], int] | None = None
    new_request_id: Callable[[], str] = ids.new_request_id


def component_state(checks: dict[str, Callable[[], bool]]) -> dict[str, str]:
    """Each required component's state. An absent probe is `unavailable`, and a
    probe that raises is `unavailable` - its exception never leaves this function."""
    state = {}
    for name in REQUIRED_CHECKS:
        probe = checks.get(name)
        try:
            state[name] = OK if probe is not None and probe() else UNAVAILABLE
        except Exception:
            intake.log.exception("readiness probe %s failed", name)
            state[name] = UNAVAILABLE
    return state


def assert_startup(rt, deps: IngressDeps) -> dict[str, str]:
    """r1 R51 / `M-FAILCLOSED`: in `pilot`, refuse to start unless every required
    component answers. `dev`/`test` report the state and start regardless."""
    state = component_state(deps.checks)
    unavailable = sorted(name for name, value in state.items() if value != OK)
    if rt.mode == "pilot" and unavailable:
        raise RuntimeMisconfigured(rt.mode,
                                   detail="unreachable at startup: " + ", ".join(unavailable))
    return state


class Ingress:
    """One per app: the resolver, the validator and the startup state."""

    def __init__(self, rt, deps: IngressDeps | None = None) -> None:
        self.rt = rt
        self.deps = deps if deps is not None else IngressDeps()
        # Built before the routes exist: in `pilot` a shared legacy key or a missing
        # identity source raises here (r1 R51), so the app never serves one request.
        self.auth = AuthResolver(rt, entitlement_version=self.deps.entitlement_version)
        self.validator = Validator(rt, consent_for=self.deps.consent_for)
        self.startup_state = assert_startup(rt, self.deps)

    async def validated(self, request: Request, request_id: str):
        """Bounded body, then tenant, then shape. In that order, always.

        The bounds come first because they are the only defence that has to work
        before anything is trusted; identity comes before the parse so that an
        unauthenticated caller cannot use parser behaviour as an oracle and so the
        body is parsed on behalf of a known tenant.
        """
        limits = self.rt.settings.pilot
        raw = await intake.read_body(request, max_bytes=limits.max_request_bytes,
                                     timeout_s=limits.intake_timeout_s, clock=self.rt.clock)
        auth = await self.auth.context(request)
        body = intake.parse_object(raw)
        normalized = self.validator.normalize(body, auth, request_id, request.headers)
        idem = idempotency(auth, request.headers, normalized.payload_digest, CHAT_OPERATION)
        return auth, normalized, idem


def register(app, rt, deps: IngressDeps | None = None):
    """Mount the ingress. Returns the `Ingress` so a test can drive it directly."""
    ingress = Ingress(rt, deps)
    deps = ingress.deps
    guarded = intake.guard(deps.new_request_id)

    @app.get(HEALTH_PATH)
    async def healthz():
        """Public and generic (01): liveness only, no component state, no counters."""
        return JSONResponse({"status": OK})

    @app.get(READY_PATH)
    @guarded
    async def readyz(request: Request, request_id: str):
        """Protected readiness: explains component state to an authenticated tenant."""
        await ingress.auth.context(request)
        state = component_state(deps.checks)
        headers = {wire.HEADER_INFERENCE_ID: request_id}
        if any(value != OK for value in state.values()):
            return intake.response(
                errors.DependencyUnavailable("a required component is unavailable",
                                             infrx={"components": state}), request_id)
        return JSONResponse({"status": OK, "mode": rt.mode, "components": state}, headers=headers)

    @app.post(CHAT_PATH)
    @guarded
    async def chat(request: Request, request_id: str):
        auth, normalized, idem = await ingress.validated(request, request_id)
        if deps.accept is None:
            # Nothing durable exists yet, so nothing may be reported as accepted.
            raise errors.DependencyUnavailable("durable acceptance is not wired (D2/G2)")
        accepted = await deps.accept(auth, normalized, idem)
        # 01: `Inference-Id` is the request id on every answer. Set here rather than
        # left to each acceptor, so no success path can be the one that forgets it.
        accepted.headers.setdefault(wire.HEADER_INFERENCE_ID, request_id)
        return accepted

    return ingress
