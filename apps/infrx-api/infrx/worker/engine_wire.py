"""W1's wire helpers: the pure request/JSON half of the vLLM adapter (A12, W6).

Split mechanically out of `engine.py` (which re-exports every name, so importers of
`infrx.worker.engine` are unchanged): the `PreparedRequest` a lease holder builds, the
tenant cache salt and media ids, the storage-ref and local-media checks, the JSON byte
costing that bounds one journal event, and the strict usage parser. No HTTP, no clock,
no adapter state - `VllmEngine` in `engine.py` is the only caller of the private ones.
"""
from __future__ import annotations

import hashlib
import posixpath
import re

from ..contracts import errors
from ..contracts.limits import DEFAULTS, PilotSettings
from ..contracts.records import MediaRef, PreparedRequest, Usage, Work


# r1 R58 / the round-2 review: the adapter is the last hop before the engine, so it checks
# the *shape* of a prepared reference as well as its tenant. M owns the grammar
# (`FakeMediaStore._key`: `media/<org>/<profile>/<16 hex of digest>/<part>`); this mirrors it
# loosely enough to accept a profile with fewer segments and strictly enough that
# `http://169.254.169.254/…`, `data:…`, `../../etc/passwd` and another tenant's prefix are
# all refused. A `PreparedRequest` can be hand-built, so trusting the field is trusting the
# caller. If M changes the layout, this constant changes with it (integration request).
STORAGE_REF_SEGMENT = r"[A-Za-z0-9][A-Za-z0-9._-]*"
STORAGE_REF_PATTERN = re.compile(
    r"^media/(?P<org>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/"
    rf"(?:{STORAGE_REF_SEGMENT}/){{1,3}}{STORAGE_REF_SEGMENT}")

# S2M's pinned serving profile (`research/workloads/marlin-sop.md` §2, discrepancy D3)
# settles the media form: the engine is handed `file://<path>` for the prepared object
# materialized under the processing-cache root, tenant-prefixed, with vLLM started as
# `--allowed-local-media-path <that root>`. A bare object key is not something vLLM can
# open, and the customer's own URL is never forwarded whatever it said.
#
# The root is a **deployment** fact, and one setting: `PROCESSING_CACHE_DIR`
# (`PilotSettings.processing_cache_dir`), which is also M2's `ProcessingCache` root and
# serve.sh's `--allowed-local-media-path` (W3). Unset, no video can be served: the adapter
# refuses rather than guessing a path.
LOCAL_MEDIA_SCHEME = "file://"
# M2's file name inside a digest directory (`prepare.SOURCE_FILENAME` + a probed extension).
LOCAL_MEDIA_FILE = re.compile(r"source\.[a-z0-9]{1,8}")


# --- translation --------------------------------------------------------------
def prepared_request(work: Work, prompt_tokens: int, *, profile_version: str | None = None,
                     limits: PilotSettings = DEFAULTS) -> PreparedRequest:
    """The `PreparedRequest` for the `Work` a lease holder loaded (r1 R46).

    `prompt_tokens` is preparation's exact count; it is an argument because `Work` does
    not carry one, and guessing it here would be the estimator `02` forbids.

    Every ref must belong to the request's organization (R10 tenant coherence: the two
    tenant-bearing arguments are the request and its media), and `tenant_salt` is
    **overwritten**, never defaulted - a customer-supplied salt would let one tenant name
    another tenant's cache namespace.
    """
    request = work.request
    if not 0 <= prompt_tokens <= limits.max_context_tokens:
        # Preparation's count, not the caller's guess: 10**30 tokens is not a prompt, and a
        # number nothing could have measured must not become a hold or a context check.
        raise errors.InvalidRequest(
            f"prompt_tokens must be in 0..{limits.max_context_tokens}", param="prompt_tokens")
    refs = work.prepared_refs or work.media_refs
    for ref in refs:
        if ref.org_id != request.org_id:
            raise errors.NotFound("prepared media must belong to the request's organization")
    parameters = dict(request.parameters or {})
    parameters["tenant_salt"] = request.org_id
    return PreparedRequest(
        request_id=request.request_id, model_revision=request.model_revision,
        messages=request.messages, parameters=parameters, media=refs,
        max_output_tokens=request.max_output_tokens, prompt_tokens=prompt_tokens,
        profile_version=profile_version or (refs[0].profile_version if refs else "v1"))


def cache_salt(prepared: PreparedRequest) -> str:
    """The per-tenant cache namespace (`01` "Privacy and retention").

    Tenant source digest **plus** profile version, so the same customer's second
    request hits the prefix and multimodal caches and another customer's never can.
    With neither a `tenant_salt` nor a media reference to identify the tenant the
    request id is used: caching nothing is a cost, sharing a cache across an unknown
    boundary is a leak.
    """
    parameters = prepared.parameters or {}
    tenant = str(parameters.get("tenant_salt") or "").strip()
    if not tenant:
        orgs = sorted({ref.org_id for ref in prepared.media})
        tenant = "|".join(orgs) if orgs else prepared.request_id
    parts = [tenant, prepared.profile_version, prepared.model_revision,
             *sorted(f"{ref.digest}@{ref.profile_version}" for ref in prepared.media)]
    return "salt_" + hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:32]


def media_uuid(ref: MediaRef, salt: str) -> str:
    """A deterministic identifier for one prepared media object, namespaced by the same
    salt as the prefix cache, and distinct per object: two sources in one tenant are two
    cache entries, not one."""
    return hashlib.sha256(f"{salt}\x1f{ref.digest}".encode()).hexdigest()[:32]


def check_storage_ref(ref: MediaRef) -> None:
    """The prepared reference the engine will be handed, or `not_found`.

    Shape *and* tenant: the key must be the store's own grammar and must sit under this
    ref's organization. Without this the adapter forwarded whatever `storage_ref` said -
    `http://169.254.169.254/…` included - to an engine that would fetch it from inside our
    network, and the allow-list on message *parts* did not help, because the reference is
    ours to trust rather than the customer's to supply.
    """
    # `fullmatch`, not `match`: `$` also matches before a trailing newline, so
    # `media/<org>/v1/source\n` passed and was forwarded.
    matched = STORAGE_REF_PATTERN.fullmatch(ref.storage_ref or "")
    if matched is None or matched.group("org") != ref.org_id:
        raise errors.NotFound(f"media {ref.handle} has no usable prepared reference")


def _inside_tenant_root(path: str, root: str, org_id: str, ref: MediaRef) -> bool:
    """Is `path` the file the engine may open for **this** request's `ref`?

    R61 (2) as amended: M2 materializes a prepared object at
    `<root>/<org uuid>/<profile_version>/<digest16>/source.<ext>`. The path is checked
    segment by segment against facts this adapter already holds - the request's
    organization, the ref's profile version and its digest - so the organization is read
    **from the path** and compared, never taken from whatever produced the path. Inside the
    root vLLM was started with (`--allowed-local-media-path`) is necessary but not
    sufficient: a path that only satisfies that is another organization's video with a
    legal name.
    """
    if not root.startswith("/") or not path.startswith("/") or not org_id:
        # A relative root cannot be compared with anything, vLLM's allow-list is absolute,
        # and a request with no organization owns nothing: refusing is the only safe answer.
        return False
    # No normalization step: after the literal root prefix exactly four segments must
    # equal known values or M2's file name, so `.`, `..` and `` can never pass.
    prefix = posixpath.normpath(root) + "/"
    if not path.startswith(prefix):
        return False
    segments = path[len(prefix):].split("/")
    digest16 = ref.digest.removeprefix("sha256:")[:16]
    return (len(segments) == 4 and segments[:3] == [org_id, ref.profile_version, digest16]
            and LOCAL_MEDIA_FILE.fullmatch(segments[3]) is not None)


def local_media_url(ref: MediaRef, root: str, org_id: str, local_uri) -> str:
    """The `file://` URL the pinned engine opens the prepared object with (R61 (2), S2M D3).

    `local_uri` is preparation's (M2 `MediaPreparation.local_uri`): the path is M2's to
    build and ours to check. Whatever it returns is validated against the configured root
    and the **request's** organization before it can reach the engine.
    """
    if local_uri is None:
        raise errors.DependencyUnavailable("no local media resolver is configured")
    if not root:
        raise errors.DependencyUnavailable("no processing cache root is configured")
    uri = local_uri(ref)
    path = uri[len(LOCAL_MEDIA_SCHEME):] if isinstance(uri, str) \
        and uri.startswith(LOCAL_MEDIA_SCHEME) else ""
    if not _inside_tenant_root(path, root, org_id, ref):
        raise errors.NotFound(f"media {ref.handle} is not inside this tenant's media root")
    return f"{LOCAL_MEDIA_SCHEME}{path}"


def _encodable(text: str) -> bool:
    """False for an unpaired surrogate: such a string cannot be serialised, so an event
    carrying it could not be journalled or relayed (it would break W2's `append`)."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


# The short escapes Python's encoder uses for these control characters (`\n`, `\t`, …);
# every other character below 0x20 becomes a six-byte `\\uXXXX` escape.
_SHORT_ESCAPES = "\b\f\n\r\t"


def _json_cost(char: str) -> int:
    """What one code point costs inside a JSON string, computed rather than remembered.

    A memo table keyed by character was bounded only by Unicode - a process serving varied
    text would have grown it to a million entries - and the arithmetic is the same four
    cases the encoder itself uses (`ensure_ascii=False`, which is what the store's
    `compact_bytes` serializes with): a short escape, a six-byte escape, an escaped quote or
    backslash, or the code point's own UTF-8 length. `test_reasoning`'s sweep checks every
    branch against `json.dumps` itself.
    """
    code = ord(char)
    if code < 0x20:
        return 2 if char in _SHORT_ESCAPES else 6
    if char in '"\\':
        return 2
    if code < 0x80:
        return 1
    if code < 0x800:
        return 2
    if code < 0x10000:
        return 3
    return 4


def _split_encoded(text: str, budget: int) -> list[str]:
    """`text` in pieces whose JSON-escaped encoded size is at most `budget` bytes.

    Cut only at code-point boundaries, so an emoji is never halved into an unpaired
    surrogate (Python slices strings by code point, which is what makes that true), and a
    single code point costing more than the budget is a piece of its own rather than an
    infinite loop.
    """
    if not text:
        return []
    pieces, start, cost = [], 0, 0
    for index, char in enumerate(text):
        char_cost = _json_cost(char)
        if cost and cost + char_cost > budget:
            pieces.append(text[start:index])
            start, cost = index, 0
        cost += char_cost
    pieces.append(text[start:])
    return pieces


def _delta_payload(raw: str, visible: str) -> dict[str, str]:
    """r1 R58 / R80: `visible` is the customer's text, `raw` is for trace capture only.

    Exactly these two keys: the transitional `content` alias of `raw` is gone (F2R item
    2). No relay may read `raw` - a relay that does leaks the reasoning block.
    """
    return {"visible": visible, "raw": raw}


def _parse_usage(raw: object) -> Usage | None:
    """vLLM's usage object, or `None` when it is not one.

    Strict on purpose: a non-object, a string, a float, a boolean, a negative or a
    `total_tokens` that does not add up all mean the engine did not tell us what it did,
    and a guessed number here becomes a customer's debit.
    """
    if not isinstance(raw, dict):
        return None
    prompt, completion = raw.get("prompt_tokens"), raw.get("completion_tokens")
    for value in (prompt, completion):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
    total = raw.get("total_tokens")
    if total is not None and (isinstance(total, bool) or not isinstance(total, int)
                             or total != prompt + completion):
        return None
    return Usage.of(prompt, completion)
