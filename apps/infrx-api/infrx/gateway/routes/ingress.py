"""The pilot ingress router: `register(app, rt)` (r1 R44).

What this module owns is the part of a request that happens before any durable
state exists: mint the identity, bound the body, authenticate the tenant, validate
the shape, resolve the model for the credential's audience, and hand a
`NormalizedRequest` plus an `AuthContextV2` to whatever accepts it. It never touches money, capacity or the queue - `JobStore.admit` does all
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
* `catalog` - G1R: the trusted `CatalogDirectory` a requested model resolves through
  for the credential's audience. Required: there is no default catalog, because an
  ingress without one could only guess what a model name means.

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

from starlette.exceptions import HTTPException as StarletteHTTPException

from ...auth.context import AuthResolver
from ...config import RuntimeMisconfigured
from ...contracts import errors, ids, wire
from ...observe.route import is_direct_loopback
from . import intake
from .validate import MAX_OPENERS, MAX_SEPARATORS, Validator, idempotency

CHAT_PATH = "/v1/chat/completions"
HEALTH_PATH = "/healthz"
READY_PATH = "/readyz"
CHAT_OPERATION = "chat.completions"
# `pilot` asserts these at startup (infra/README.md `M-FAILCLOSED`): a usable price
# version for the served model must resolve (contracts v1: a missing model or rate
# rejects admission) and the journal must be reachable, because an unjournalled
# pilot cannot honour the output guarantees it makes.
REQUIRED_CHECKS = ("price_source", "journal")
# G3: the jobs router's routes, as (method, path). They are mounted only with a relay
# (M-FAILCLOSED), so none may be absent unless all are.
JOBS_ROUTES = (("POST", "/v1/jobs"), ("GET", "/v1/jobs/{handle}"),
               ("DELETE", "/v1/jobs/{handle}"), ("GET", "/v1/jobs/{handle}/result"),
               ("GET", "/v1/jobs/{handle}/events"))
JOBS_MODULE = __name__.rpartition(".")[0] + ".jobs"
OK = "ok"
UNAVAILABLE = "unavailable"


@dataclass
class IngressDeps:
    """Everything the ingress needs from outside itself. Defaults fail safe.

    The composition root puts one of these on the runtime as `rt.ingress`;
    `register(app, rt)` reads it there, so a track router keeps the one calling
    convention `ROUTERS` uses (r1 R44). The explicit third argument exists for tests.
    """

    accept: Callable | None = None
    checks: dict[str, Callable[[], bool]] = field(default_factory=dict)
    consent_for: Callable | None = None
    entitlement_version: Callable[[str], int] | None = None
    catalog: object | None = None               # contracts.v2.ports.CatalogDirectory
    # One per process, shared by every request: the bound is on the process's loop.
    large_bodies: "intake.LargeBodies | None" = None
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
        if self.deps.catalog is None:
            raise RuntimeMisconfigured(rt.mode, detail="the ingress needs a model catalog "
                                                       "(IngressDeps.catalog)")
        self.validator = Validator(rt, catalog=self.deps.catalog,
                                   consent_for=self.deps.consent_for)
        self.slots = self.deps.large_bodies or intake.LargeBodies()
        self.startup_state = assert_startup(rt, self.deps)

    async def validated(self, request: Request, request_id: str):
        """Tenant, then bounded body, then structure, then shape. In that order.

        Identity comes **first**, from the headers alone: an unauthenticated caller
        must never be able to make this process buffer 96 MiB, and the body we do
        read is read on behalf of a known tenant. The byte and time bounds come next,
        because they are the only defence that has to work before anything is trusted.
        Then the cheap structural count on the raw bytes, because the caps that run on
        the parsed tree arrive too late to stop it being built. Only then `json.loads`,
        with at most `LargeBodies.limit` large bodies reaching it at once.
        """
        limits = self.rt.settings.pilot
        auth = await self.auth.context(request)
        intake.check_content_type(request)
        large = self.slots.slot()
        try:
            raw = await intake.read_body(request, max_bytes=limits.max_request_bytes,
                                         timeout_s=limits.intake_timeout_s,
                                         clock=self.rt.clock, large=large)
            # Decode before counting: `json.loads` on bytes sniffs UTF-16/32, so a
            # byte-level count would measure something other than what gets parsed.
            text = intake.decode_utf8(raw)
            intake.check_structure(text, MAX_OPENERS, MAX_SEPARATORS)
            body = intake.parse_object(text)
            normalized = await self.validator.normalize(body, auth, request_id,
                                                        request.headers)
            idem = idempotency(auth, request.headers, normalized.payload_digest, CHAT_OPERATION)
            return auth, normalized, idem
        finally:
            # Every exit path, refusals included: a slot that is not released is a slot
            # nobody gets again.
            #
            # For G2/G3: the slot is released *here*, before `accept` runs, because it
            # bounds parsing rather than the request's lifetime. The `NormalizedRequest`
            # returned can still carry a 96 MiB inline payload inside `messages`, so a
            # synchronous wait that keeps that record alive keeps the payload resident
            # with nothing accounting for it. Stage the payload and drop the reference
            # (M's job) before waiting, or carry the slot through staging.
            large.release()


def install_error_handlers(app, mint_request_id=ids.new_request_id) -> None:
    """Everything FastAPI would answer by itself, in the contract's envelope.

    Without these, a 404 for an unknown path, a 405 for the wrong method and an
    unhandled exception leave as `{"detail": …}` with no code, no request id and -
    for the last one - a stack trace. `register` installs them, so the composition
    root gets them by mounting the router; it can also call this directly.
    """

    async def http_exception(request: Request, exc: StarletteHTTPException):
        # Only statuses the contract's table has (08 §3). A 405 becomes `not_found`
        # deliberately: "that method is not allowed here" confirms the path exists,
        # and 405 is not a status this API promises anywhere else.
        code = "not_found" if exc.status_code in (404, 405) else "invalid_request"
        if exc.status_code >= 500:
            code = "internal_error"
        return intake.response(errors.DomainError(code=code), intake.mint(mint_request_id))

    async def unhandled(request: Request, exc: Exception):
        request_id = intake.mint(mint_request_id)
        intake.log.exception("%s: unhandled error on request %s", request.url.path, request_id)
        return intake.response(errors.InternalError(), request_id)

    app.add_exception_handler(StarletteHTTPException, http_exception)
    app.add_exception_handler(Exception, unhandled)


def assert_route_table(app) -> None:
    """G1R review C4: exactly one `/v1/chat/completions` handler, and it is this module's.

    `validate_runtime` checks which *modules* `ROUTERS` names; this checks what the route
    table serves, which is what a client reaches. The composition root calls it after its
    router loop, so neither a later router nor a second registration can put another
    handler - the legacy one, say, which admits nothing and holds nothing - on the path.
    """
    served = [route for route in app.routes if getattr(route, "path", None) == CHAT_PATH]
    if len(served) != 1 or served[0].endpoint.__module__ != __name__:
        mode = getattr(getattr(app.state, "runtime", None), "mode", "")
        raise RuntimeMisconfigured(mode, detail=f"{CHAT_PATH} must have exactly one handler, "
                                                f"the metered ingress")
    # G3: the jobs routes, all or none, each with exactly one handler - the jobs router's.
    jobs = [[route.endpoint.__module__ for route in app.routes
             if getattr(route, "path", None) == path
             and method in (getattr(route, "methods", None) or ())]
            for method, path in JOBS_ROUTES]
    if any(jobs) and any(found != [JOBS_MODULE] for found in jobs):
        mode = getattr(getattr(app.state, "runtime", None), "mode", "")
        raise RuntimeMisconfigured(mode, detail="each /v1/jobs route must have exactly one "
                                                "handler, the jobs router's")


def register(app, rt, deps: IngressDeps | None = None):
    """Mount the ingress. Returns the `Ingress` so a test can drive it directly.

    `deps` comes from `rt.ingress` when it is not passed, so the coordinator's
    `ROUTERS` protocol - `register(app, rt)` - is the only calling convention the
    composition root needs (r1 R44).
    """
    ingress = Ingress(rt, deps if deps is not None else getattr(rt, "ingress", None))
    deps = ingress.deps
    guarded = intake.guard(deps.new_request_id)
    install_error_handlers(app, deps.new_request_id)

    @app.get(HEALTH_PATH)
    async def healthz():
        """Public and generic (01): liveness only, no component state, no counters."""
        return JSONResponse({"status": OK})

    @app.get(READY_PATH)
    @guarded
    async def readyz(request: Request, request_id: str):
        """Readiness for the host itself: component state for a direct loopback peer (I2B's
        `deploy/lib.sh wait_ready` polls it unauthenticated), the unknown-path answer for
        anyone else. A request relayed by the edge carries a proxy header, so it is refused
        here even if the edge rule that 404s `/readyz` were lost (observe/route.py's rule)."""
        if not is_direct_loopback(request):
            raise errors.NotFound("readiness is answered to a direct loopback peer only")
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

    # The guard's wrapper is defined in `intake`; the route table names the ingress as the
    # handler of the metered path (`assert_route_table`, and E3B dr17's module check).
    chat.__module__ = __name__

    return ingress
