"""G4T: `GET /v1/traces`, the owner's trace export over the content-access service (C2).

    GET /v1/traces?since=<iso>&until=<iso>&cursor=<opaque>&limit=<1..1000, default 100>
        -> 200 {"data": [TraceExport, ...], "next_cursor": <opaque> | null}

* **identity first**, and the organization is the key's: a parameter naming an organization
  (or anything else unknown) is a 400, and an operator credential owns no traces (403);
* **owned, unexpired only**: T2I's projection of that organization, minus what T3 deleted or
  whose metadata expired; content availability is `content_state` (T3's bound included);
* **no storage key, no content**: each row is the frozen R47 `TraceExport` without a content
  handle - a provider reads content only through a C2 ref, never here;
* **bounded**: at most `limit` rows per page, the cursor opaque within the organization, and
  the store call bounded by `DEPENDENCY_BOUND_S` (a 503, never a hang).

Mounted by nobody until the coordinator adds it to `app.ROUTERS`, and even then only when the
composition put an export on `rt.trace_export`, which it does only when the deployment
enables it (off by default) and ClickHouse is configured.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import Request
from fastapi.responses import JSONResponse

from ...auth.context import AuthResolver
from ...content import EXPORT_DEFAULT_LIMIT
from ...contracts import errors, ids, wire
from . import intake
from .ingress import install_error_handlers
from .jobs import OWNERS

EXPORT_PATH = "/v1/traces"
PARAMETERS = frozenset({"since", "until", "cursor", "limit"})


def _instant(value: str | None, name: str) -> datetime | None:
    if value is None:
        return None
    try:
        at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        at = None
    if at is None or at.tzinfo is None:
        raise errors.InvalidRequest(f"{name} must be an ISO 8601 instant with a zone", param=name)
    return at


def _limit(value: str | None) -> int:
    if value is None:
        return EXPORT_DEFAULT_LIMIT
    if not value.isdigit():
        raise errors.InvalidRequest("limit must be an integer", param="limit")
    return int(value)


def register(app, rt, export=None, new_request_id=ids.new_request_id):
    """Mount `GET /v1/traces` over `export` (default `rt.trace_export`, a C2
    `OwnedExport`). Without one nothing is mounted and `None` is returned."""
    install_error_handlers(app, new_request_id)
    export = export if export is not None else getattr(rt, "trace_export", None)
    if export is None:
        return None
    auth = AuthResolver(rt)

    @app.get(EXPORT_PATH)
    @intake.guard(new_request_id, rt.settings.pilot)
    async def export_traces(request: Request, request_id: str):
        context = await auth.context(request)
        if context.audience not in OWNERS:
            raise errors.Forbidden("an operator credential owns no traces")
        query = request.query_params
        for name in query:
            if name not in PARAMETERS or len(query.getlist(name)) > 1:
                # never echoed: a caller's string in a detail is a log-forging vector
                raise errors.InvalidRequest("an unsupported or repeated query parameter")
        since, until = _instant(query.get("since"), "since"), _instant(query.get("until"), "until")
        rows, cursor = await intake.bounded(
            export.page(context.org_id, cursor=query.get("cursor"), since=since, until=until,
                        limit=_limit(query.get("limit"))), intake.DEPENDENCY_BOUND_S)
        return JSONResponse({"data": [row.model_dump(mode="json") for row in rows],
                             "next_cursor": cursor},
                            headers={wire.HEADER_INFERENCE_ID: request_id})

    return export
