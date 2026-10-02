"""AP-00 00b: the checked OpenAPI artifacts of the enabled compositions (R270, R271).

    uv run --frozen python -m infrx.contracts.openapi.export     # rewrite apps/infrx-api/openapi/

The public docs stay disabled (`docs_url=None`, `openapi_url=None`): nothing here is served.
Each composition is built the way its process builds it - `gateway.app.create_app` and
`lab.control.app.create_app` - over inert stand-ins for the stores, so only the route table
matters and nothing connects anywhere:

- `consumer`: the gateway with every consumer switch on (`FEEDBACK_API`, `TRACE_EXPORT_API`,
  `IDENTITY_API` over inert identity/access stand-ins - its `SessionActors` are the session
  actors the console routes need - `AUTH_FACADE` over an inert publishable key,
  `CONSOLE_READS` over an unreachable DSN, `CONSOLE_ACTIONS_API` over an inert repository
  stand-in, `CONSOLE_DATA_USE` over an inert data-use stand-in);
  the Lab switches stay off on the gateway (R237: the Lab unit serves them);
- `consumer-launched`: the gateway as launched (every switch at its default, OFF);
- `lab-control`: the Lab control unit with its optional families (traces, checkpoints,
  `LAB_JUDGE_API`'s judge and trace reviews) on.

`document` adds what FastAPI does not: an operationId derived from method + path (unique by
construction), and a security declaration per operation from `FAMILIES` unless the route
declares its own; a route in no family that declares none refuses the export.
`legacy` lists the schema-less routes; `baseline.json` freezes today's (it may only shrink).
"""
from __future__ import annotations

import copy
import json
import logging
import os
import pathlib
import re
from typing import Any
from unittest import mock

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute

API_DIR = pathlib.Path(__file__).resolve().parents[3]
ARTIFACT_DIR = API_DIR / "openapi"
BASELINE_PATH = pathlib.Path(__file__).with_name("baseline.json")
#: The compositions with a checked artifact (`consumer-launched` is a subset of `consumer`).
ARTIFACTS = ("consumer", "lab-control")
VERSION = "v1"

PUBLIC: list[dict[str, list[str]]] = []
SCHEMES = {
    "ApiKeyAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "sk-infrx",
                   "description": "A consumer or provider-dev API key (`/v1/*`)."},
    "SessionBearer": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT",
                      "description": "The signed-in user's Supabase session, forwarded by "
                                     "the web app's server; verified by the auth server."},
    "OperatorKey": {"type": "http", "scheme": "bearer", "bearerFormat": "sk-infrx",
                    "description": "An operator-audience API key."},
    "CheckpointSignature": {"type": "apiKey", "in": "header", "name": "X-Infrx-Signature",
                            "description": "HMAC of the raw body by a LAB_CHECKPOINT_KEYS key."},
}
#: (path prefix or exact path, security, envelope, audience): first match wins, so the more
#: specific entries come first. Security None = the route declares its own (`/auth/v1/*`:
#: sign-in is public, a password change is a session's - its exact paths below).
FAMILIES: tuple[tuple[str, list[dict[str, list[str]]] | None, str, str], ...] = (
    ("/health", PUBLIC, "none", "public"),
    ("/healthz", PUBLIC, "none", "public"),
    ("/readyz", PUBLIC, "none", "public"),
    ("/metrics", PUBLIC, "none", "loopback"),
    ("/v1/models", PUBLIC, "openai", "public"),
    ("/v1/", [{"ApiKeyAuth": []}], "openai", "consumer or provider_dev key"),
    ("/lab/v1/checkpoints", [{"CheckpointSignature": []}], "refusal", "signed training loop"),
    ("/lab/v1/", [{"SessionBearer": []}], "refusal", "provider session"),
    ("/console/v1/", [{"SessionBearer": []}], "r270", "consumer session"),
    ("/operator/v1/", [{"SessionBearer": []}, {"OperatorKey": []}], "r270", "operator"),
    # WR-AP01-1: the facade's two session-bearing calls; every other `/auth/v1/*` is public
    ("/auth/v1/sign-out", [{"SessionBearer": []}], "r270", "identity transport"),
    ("/auth/v1/password", [{"SessionBearer": []}], "r270", "identity transport"),
    ("/auth/v1/", PUBLIC, "r270", "identity transport"),
)


def family(path: str) -> tuple[list[dict[str, list[str]]] | None, str, str] | None:
    for prefix, security, envelope, audience in FAMILIES:
        if path == prefix or (prefix.endswith("/") and path.startswith(prefix)):
            return security, envelope, audience
    return None


def operation_id(method: str, path: str) -> str:
    """`post /a/{b_c}/d` -> `postAByBCD`: one name per (method, path), stable across runs."""
    words = re.findall(r"[A-Za-z0-9]+", path.replace("{", " by "))
    return method.lower() + "".join(w[:1].upper() + w[1:] for w in words)


def shape(route: APIRoute) -> tuple[str, str]:
    """(request, response): `typed` / `none` / `raw` (the handler reads the `Request`).
    A declared 204 No Content is a declared response: it has no body to model (WR-AP01-1)."""
    request = "typed" if route.body_field is not None else \
        "raw" if route.dependant.request_param_name else "none"
    declared = route.status_code == 204 or route.response_model is not None or any(
        str(code).isdigit() and 200 <= int(code) < 300 and "model" in answer
        for code, answer in route.responses.items())
    return request, "typed" if declared else "none"


def routes(app: FastAPI) -> list[APIRoute]:
    """Every APIRoute the app serves. FastAPI 0.141 records `app.include_router(router)` as a
    lazy `_IncludedRouter` entry (its `original_router` holds the routes), so the export walks
    into those too; the repo's own convention is the app's table, but a lane that mounted a
    router is still documented."""
    found: list[APIRoute] = []
    def walk(entries, prefix=""):
        for r in entries:
            if isinstance(r, APIRoute):
                found.append(r if not prefix else _prefixed(r, prefix))
            elif hasattr(r, "original_router"):
                ctx = getattr(r, "include_context", None)
                walk(r.original_router.routes, prefix + (getattr(ctx, "prefix", "") or ""))
    walk(app.routes)
    return found


def _prefixed(route: APIRoute, prefix: str) -> APIRoute:
    """A copy whose path carries the include prefix (none of the wave-7 routers use one)."""
    clone = copy.copy(route)
    clone.path = prefix + route.path
    clone.path_format = prefix + route.path_format
    return clone


NO_BODY_MARK = "x-infrx-no-body"   # openapi_extra: a body method that declares it takes no body


BODY_METHODS = {"POST", "PUT", "PATCH"}


def legacy(apps: dict[str, FastAPI]) -> dict[str, list[str]]:
    """Per composition, the routes that lack a declared response, or carry a body method
    without a typed body. An R270 handler reads the raw `Request` for its request id and
    actor; with a declared response that is documented, not legacy - unless a body method
    parses that raw request by hand (no typed body)."""
    def undocumented(route: APIRoute, method: str) -> bool:
        request, response = shape(route)
        no_body = bool((route.openapi_extra or {}).get(NO_BODY_MARK))
        return response == "none" or (method in BODY_METHODS and request == "raw" and not no_body)
    return {name: sorted(f"{method} {route.path_format}" for route in routes(app)
                         for method in route.methods or () if undocumented(route, method))
            for name, app in apps.items()}


def document(app: FastAPI, title: str) -> dict[str, Any]:
    """The composition's OpenAPI document: every mounted FastAPI route, including the ones
    hidden from FastAPI's own schema (`include_in_schema=False`, e.g. `/metrics`)."""
    shown = [copy.copy(route) for route in routes(app)]
    for route in shown:
        route.include_in_schema = True
    doc = get_openapi(title=f"infrx {title}", version=VERSION, routes=shown)
    for path, item in doc["paths"].items():
        for method, op in item.items():
            op["operationId"] = operation_id(method, path)
            if "security" in op:
                continue
            found = family(path)
            if found is None or found[0] is None:
                raise ValueError(f"{method.upper()} {path}: no security declaration")
            op["security"] = found[0]
    doc.setdefault("components", {})["securitySchemes"] = SCHEMES
    return doc


def render(doc: dict[str, Any]) -> str:
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"


def _gateway(env: dict[str, str]) -> FastAPI:
    from ...config import from_env
    from ...gateway import app as gateway
    inert = object()
    logging.disable(logging.CRITICAL)      # the startup probes of the inert stores log a failure
    try:
        # WR-AP01-1: IDENTITY_API composes `rt.actors` (SessionActors over the inert stores)
        return gateway.create_app(from_env(env), catalog=inert, stream=inert, objects=inert,
                                  jobs=inert, index=inert, feedback=inert, trace_export=inert,
                                  console_actions=inert, data_use=inert, identity=inert,
                                  lab_access=inert)
    finally:
        logging.disable(logging.NOTSET)


def _lab_control() -> FastAPI:
    from ...lab import compose
    from ...lab.control import app as control
    inert = object()
    env = {control.DATABASE_URL: "postgresql://export@127.0.0.1:1/export",
           control.SUPABASE_URL: "http://127.0.0.1:1", control.SUPABASE_KEY: "export",
           "CLICKHOUSE_URL": "http://127.0.0.1:1", "LAB_CHECKPOINT_KEYS": "export",
           "LAB_JUDGE_API": "1"}                                           # WR-1 api-judge
    # ponytail: the two families that connect at composition (ClickHouse, the key directory)
    # are patched to inert stand-ins; the rest compose lazily over an unreachable DSN.
    with mock.patch.dict(os.environ, env, clear=True), \
            mock.patch.object(compose, "lab_traces", lambda *_: inert), \
            mock.patch.object(compose, "lab_checkpoints", lambda *_: {"lab_checkpoints": inert}):
        return control.create_app()


def compositions() -> dict[str, FastAPI]:
    test = {"INFRX_MODE": "test"}         # the pilot's route table without its startup probes
    console = {"CONSOLE_READS": "1", "CONSOLE_DATABASE_URL": "postgresql://export@127.0.0.1:1/export",
               "CONSOLE_CURSOR_SECRET": "export-cursor-secret",       # WR-AP02-1
               "CONSOLE_ACTIONS_API": "1",                             # WR-AP03-3
               "CONSOLE_DATA_USE": "1",                                # W1 api-traces
               "IDENTITY_API": "1", "AUTH_FACADE": "1",                # WR-AP01-1
               "SUPABASE_ANON_KEY": "export-anon"}
    return {"consumer": _gateway({**test, "FEEDBACK_API": "1", "TRACE_EXPORT_API": "1",
                                  **console}),
            "consumer-launched": _gateway(test),
            "lab-control": _lab_control()}


def main() -> int:
    apps = compositions()
    ARTIFACT_DIR.mkdir(exist_ok=True)
    for name in ARTIFACTS:
        (ARTIFACT_DIR / f"{name}.json").write_text(render(document(apps[name], name)))
    from . import inventory
    inventory.write(apps)
    print(f"wrote {', '.join(ARTIFACTS)} and the route inventory")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
