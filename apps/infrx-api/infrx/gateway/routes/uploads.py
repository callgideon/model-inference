"""G4U: the owned-upload flow of 01 §HTTP behavior, over M3's `MediaUploads`.

    POST /v1/uploads                    the constraint object -> 201 `UploadCreated`
    PUT  /v1/uploads/{handle}           the constrained destination: the bytes -> 204
    POST /v1/uploads/{handle}/complete  no body -> 200 `UploadCompleted` (never the ref)

The frozen `UploadCreated` carries no URL and the pilot has no signed object-store URL,
so the destination is this authenticated route at the one path the handle names. The
tenant is the key's on every call (R66): nothing in a path or body names an org.

What the store already enforces - the closed constraint set, the per-upload cap,
write-once, finalize-once, expiry, `(org, handle)` scope - is not repeated here. What
only a route can do is:

* **identity before any body byte**, so an anonymous caller never makes us buffer;
* **bounded bytes and time**: the running total stops at `MAX_MEDIA_BYTES` (the ceiling
  of every upload's `max_bytes`; `put_upload` then applies the upload's own cap) under
  the intake deadline, and a large body holds one of the per-process `LargeBodies` slots
  it shares with chat until the store has it;
* **only the frozen wire names** in the answers; completion projects the ref (R47).

Mounted by nobody until the coordinator adds it to `app.ROUTERS`, and even then only when
the runtime carries a media store (09: an adapter is enabled only when its dependency
exists).
"""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from ...auth.context import AuthResolver
from ...contracts import errors, ids, wire
from ...contracts.records import UploadState
from . import intake
from .catalog import CALLABLE
from .ingress import install_error_handlers
from .validate import UPLOAD_SCHEME

UPLOADS_PATH = "/v1/uploads"
DESTINATION_PATH = UPLOADS_PATH + "/{handle}"
COMPLETE_PATH = DESTINATION_PATH + "/complete"
# The create body is four constraint fields and completion takes none; nothing we issue
# comes near this, so a larger control body is not one of ours.
MAX_CONTROL_BYTES = 4096


def register(app, rt, store=None, large_bodies=None, new_request_id=ids.new_request_id):
    """Mount the three upload routes over `store` (default `rt.media_store`). Without a
    store nothing is mounted and `None` is returned. `large_bodies` (default
    `rt.large_bodies`) must be the one the chat ingress uses, so both share the bound."""
    install_error_handlers(app, new_request_id)
    store = store if store is not None else getattr(rt, "media_store", None)
    if store is None:
        return None
    slots = large_bodies or getattr(rt, "large_bodies", None) or intake.LargeBodies()
    # Built at mount, like the ingress's: in `pilot` a shared legacy key refuses here (R51).
    auth = AuthResolver(rt)
    limits = rt.settings.pilot
    guard = intake.guard(new_request_id)

    def guarded(handler):
        """`intake.guard`, and every refusal closes the connection. A refusal raised before
        the read leaves the caller's body still arriving - the intake's rule for its own
        pre-read refusals - and an upload body is the largest this API takes."""
        translated = guard(handler)

        async def closing(request: Request):
            answer = await translated(request)
            if answer.status_code >= 400:
                answer.headers["Connection"] = "close"
            return answer

        closing.__name__ = handler.__name__
        return closing

    async def tenant(request):
        """The key's identity. An operator credential runs no inference (catalog's rule),
        so it owns no upload either."""
        context = await auth.context(request)
        if context.audience not in CALLABLE:
            raise errors.Forbidden("an operator credential does not run inference")
        return context

    def handle_of(request) -> str:
        # Never echoed: an unknown, malformed or org-qualified handle is one `not_found`.
        handle = request.path_params["handle"]
        if not ids.UPLOAD_HANDLE_RE.fullmatch(handle):
            raise errors.NotFound("not an upload handle")
        return handle

    async def control_body(request) -> dict:
        raw = await intake.read_body(request, max_bytes=MAX_CONTROL_BYTES,
                                     timeout_s=limits.intake_timeout_s, clock=rt.clock)
        # A 4 KiB body cannot carry the structure `check_structure` exists for; the
        # parser's own depth failure is already a 400 (`parse_object`).
        return intake.parse_object(intake.decode_utf8(raw)) if raw else {}

    @app.post(UPLOADS_PATH)
    @guarded
    async def create_upload(request: Request, request_id: str):
        context = await tenant(request)
        intake.check_content_type(request)
        body = await control_body(request)
        created = await store.create_upload(context.org_id, body)
        ticket = wire.UploadCreated.model_validate(created).model_dump(mode="json")
        # R61(1)/R47 at the port boundary: the values too, not only the keys. A store that
        # put an object key or an org into the destination is ours to refuse, not render.
        handle = ticket["upload_handle"]
        if not ids.UPLOAD_HANDLE_RE.fullmatch(handle) \
                or ticket["destination_ref"] != UPLOAD_SCHEME + handle:
            raise errors.InternalError("the store issued a ticket outside the contract")
        return JSONResponse(ticket, status_code=201,
                            headers={wire.HEADER_INFERENCE_ID: request_id})

    @app.put(DESTINATION_PATH)
    @guarded
    async def put_upload(request: Request, request_id: str):
        context = await tenant(request)
        handle = handle_of(request)
        mime = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
        if mime not in store.fetcher.allowed_mime:
            raise errors.UnsupportedMedia("the destination takes an allowed media type",
                                          param="Content-Type")
        slot = slots.slot()
        try:
            # ponytail: cut at MAX_MEDIA_BYTES, not the upload's own max_bytes (put_upload
            # refuses after the read); a public cap read on MediaUploads is the upgrade.
            data = await intake.read_body(request, max_bytes=limits.max_media_bytes,
                                          timeout_s=limits.intake_timeout_s, clock=rt.clock,
                                          large=slot)
            await store.put_upload(context.org_id, handle, data, mime)
        finally:
            # Every exit, refusals included; held until the store has the bytes, because
            # until then they are this process's to hold.
            slot.release()
        return Response(status_code=204, headers={wire.HEADER_INFERENCE_ID: request_id})

    @app.post(COMPLETE_PATH)
    @guarded
    async def complete_upload(request: Request, request_id: str):
        context = await tenant(request)
        handle = handle_of(request)
        if await control_body(request) != {}:
            # R17: the constraints were fixed at create; completion takes none.
            raise errors.InvalidRequest("completion takes no fields")
        ref = await store.finalize_upload(context.org_id, handle)
        completed = wire.UploadCompleted.of(handle, UploadState.finalized, ref)
        return JSONResponse(completed.model_dump(mode="json"),
                            headers={wire.HEADER_INFERENCE_ID: request_id})

    return store
