#!/usr/bin/env python3
"""E4B.c: the endpoint's capability document and headless request examples, generated.

    apps/infrx-api/.venv/bin/python tests/integration/backend/endpoint_doc.py --write
    apps/infrx-api/.venv/bin/python tests/integration/backend/endpoint_doc.py --check

Every table is read from the code: the route table (the `@app.<method>(...)` decorators of
the modules the cutover mounts), the error catalogue (`contracts.errors`), the limits
(`contracts.limits.DEFAULTS` and the validator's bounds), the parameters, execution modes,
cancel causes, settlement states, headers and wire shapes. Only the one-line route
descriptions and the curl examples are prose, and `test_endpoint_doc.py` holds them to the
code: a route with no description, a description with no route, a missing error code, an
example on a path that is not mounted, or a committed document that is not what this prints,
fails. The document's verification log is kept across regenerations.
"""
from __future__ import annotations

import argparse
import ast
import importlib
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import certify                                          # noqa: E402  (infrx on path)

from infrx.contracts import errors, records, wire     # noqa: E402
from infrx.contracts.limits import DEFAULTS            # noqa: E402
from infrx.gateway.routes import ingress, jobs, uploads, validate  # noqa: E402

DOC = certify.harness.REPO_ROOT / "research" / "plan" / "evidence" / "e" / "E4B-endpoint.md"
LOG = "\n## Verification log\n"
# The routers the cutover mounts (G2's `CUTOVER` plus G3's jobs and G4U's uploads) and the
# loopback metrics route (I3B). The legacy chat route (`routes/chat.py`) is what the cutover
# replaces, so it is not documented.
ROUTE_MODULES = ("infrx.gateway.routes.health", "infrx.gateway.routes.models",
                 "infrx.gateway.routes.ingress", "infrx.gateway.routes.jobs",
                 "infrx.gateway.routes.uploads", "infrx.observe.route")
METHODS = ("get", "post", "put", "delete", "patch")


def code(name: str) -> str:
    """An error code as the prose cites it, its status read from the catalogue (review F9)."""
    return f"`{name}` ({errors.http_status(name)})"


def cancel_cause() -> str:
    """The cause DELETE /v1/jobs/{handle} cancels with, read from the jobs module's call."""
    tree = ast.parse(Path(jobs.__file__).read_text())
    causes = {kw.value.attr for node in ast.walk(tree) if isinstance(node, ast.Call)
              and getattr(node.func, "attr", None) == "cancel" for kw in node.keywords
              if kw.arg == "cause" and isinstance(kw.value, ast.Attribute)}
    (cause,) = causes
    return cause


DESCRIPTIONS = {
    ("GET", "/health"): "engine liveness (legacy shape; the edge answers it sanitized)",
    ("GET", "/healthz"): "gateway liveness: `{\"status\": \"ok\"}`, no component state",
    ("GET", "/readyz"): "readiness with component state, for a direct loopback peer only "
                        "(the edge and any proxied caller get the unknown-path 404)",
    ("GET", "/metrics"): "Prometheus text for a direct loopback peer only; anyone else gets "
                         "the byte-identical 404 of an unknown path",
    ("GET", "/v1/models"): "the model list (OpenAI shape)",
    ("POST", "/v1/chat/completions"): "chat: JSON by default, SSE with `\"stream\": true`, a "
                                      "202 job with `Prefer: respond-async`",
    ("POST", "/v1/jobs"): "an explicit asynchronous job: the chat body, answered 202 "
                          "`JobAccepted` once admission has committed",
    ("GET", "/v1/jobs/{handle}"): "`JobStatus` from the committed row",
    ("GET", "/v1/jobs/{handle}/result"): f"`JobResult`; {code('result_pending')} while it "
                                         f"runs, {code('result_expired')} past the result's TTL",
    ("GET", "/v1/jobs/{handle}/events"): "the committed journal as SSE from `Last-Event-ID`; "
                                         "an observer that leaves detaches, never cancels",
    ("DELETE", "/v1/jobs/{handle}"): f"cancel (`{cancel_cause()}`), answering the committed "
                                     "outcome",
    ("POST", "/v1/uploads"): "create an upload from its constraints: 201 `UploadCreated`",
    ("PUT", "/v1/uploads/{handle}"): "the bytes, to the constrained destination: 204",
    ("POST", "/v1/uploads/{handle}/complete"): "finalize (no body): 200 `UploadCompleted`; "
                                               "then send `infrx-upload:<handle>`",
}
LIMITS = (("max_request_bytes", "request body, bytes"),
          ("max_media_bytes", "one video, decoded bytes (also an upload's ceiling)"),
          ("max_video_seconds", "one video's duration, s (profile v1; the deployed cap is "
                                "configuration: P-20 applies 72)"),
          ("intake_timeout_s", "reading a request or upload body, s"),
          ("media_fetch_timeout_s", "fetching a video URL, s"),
          ("media_fetch_max_redirects", "redirects followed for a video URL"),
          ("queue_wait_interactive_s", "queue wait before a sync/SSE job expires "
                                       "(`queue_wait_expired`, free), s"),
          ("queue_wait_async_s", "queue wait before an async job expires, s"),
          ("ttft_timeout_s", "time to the first token, s"),
          ("generation_timeout_s", "generation, s"),
          ("max_output_tokens", "output tokens per request"),
          ("max_context_tokens", "prompt + output tokens"),
          ("max_active_jobs_per_key", "active jobs per API key"),
          ("max_active_jobs_per_org", "active jobs per organization"),
          ("max_active_jobs", "active jobs on the deployment"),
          ("result_ttl_s", "a result stays readable after terminal, s"),
          ("idempotency_ttl_s", "a key keeps answering its job after terminal, s"),
          ("journal_chunk_ttl_s", "the SSE replay window, s"),
          ("sse_keepalive_s", "SSE keep-alive comment interval, s"))
BOUNDS = ("MAX_MESSAGES", "MAX_PARTS_PER_MESSAGE", "MAX_VIDEO_PARTS", "MAX_TEXT_CODEPOINTS",
          "MAX_URL_CHARS", "MAX_STOP_SEQUENCES", "MAX_STOP_CHARS")
SHAPES = (wire.JobAccepted, wire.JobStatus, wire.JobResult, wire.UploadCreated,
          wire.UploadCompleted)


def route_table() -> list[tuple[str, str, str]]:
    """(METHOD, path, module) for every `@app.<method>(path)` in the mounted route modules;
    a path given as a module constant is resolved on the imported module."""
    rows = set()
    for name in ROUTE_MODULES:
        module = importlib.import_module(name)
        for node in ast.walk(ast.parse(Path(module.__file__).read_text())):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for deco in node.decorator_list:
                if isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute) \
                        and isinstance(deco.func.value, ast.Name) and deco.func.value.id == "app" \
                        and deco.func.attr in METHODS:
                    arg = deco.args[0]
                    path = arg.value if isinstance(arg, ast.Constant) else getattr(module, arg.id)
                    rows.add((deco.func.attr.upper(), path, name.rsplit(".", 1)[-1]))
    return sorted(rows, key=lambda row: (row[1], METHODS.index(row[0].lower())))


def unauthenticated() -> list[str]:
    """The `/v1/` paths whose module never authenticates (review F9: read, not asserted)."""
    paths = set()
    for name in ROUTE_MODULES:
        module = importlib.import_module(name)
        if "auth" not in Path(module.__file__).read_text().lower():
            paths |= {path for _, path, short in route_table()
                      if short == name.rsplit(".", 1)[-1] and path.startswith("/v1/")}
    return sorted(paths)


def headers() -> list[str]:
    """Every header name the contract declares, and the one the jobs route adds (`Location`
    on the 202)."""
    return sorted({*(getattr(wire, name) for name in vars(wire) if name.startswith("HEADER_")),
                   jobs.HEADER_LOCATION})


def _table(header: tuple[str, ...], rows) -> list[str]:
    return ["| " + " | ".join(header) + " |", "|" + "---|" * len(header),
            *("| " + " | ".join(str(cell) for cell in row) + " |" for row in rows)]


def examples(model: str) -> list[str]:
    """Headless curl examples. The key comes from the environment and travels in a header
    file, never on the command line (G6B.c); paths are the route constants."""
    chat, job = ingress.CHAT_PATH, jobs.JOB_PATH.replace("{handle}", "$JOB")
    video = ('{"model": "%s", "max_tokens": 512, "messages": [{"role": "user", "content": ['
             '{"type": "video_url", "video_url": {"url": "https://media.example.com/clip.mp4"}}, '
             '{"type": "text", "text": "Provide a spatial description of this clip followed by '
             'time-ranged events."}]}]' % model)
    return [
        "```bash",
        "export BASE=https://<host>            # the edge; the gateway's /v1 routes sit under it",
        "umask 077; printf 'Authorization: Bearer %s\\n' \"$INFRX_API_KEY\" > .auth",
        "#          ^ the key is read from the environment into a 0600 header file: never argv",
        "",
        "# 1. sync JSON chat - one item, one Idempotency-Key (sop1.<item_key> for a dataset)",
        f"curl -sS -H @.auth -H 'Content-Type: application/json' -H 'Idempotency-Key: sop1.k1' \\",
        f"     \"$BASE{chat}\" -d '{video}}}'",
        "",
        "# 2. the same request as SSE: the first frame names the job, the last carries usage",
        f"curl -sS -N -H @.auth -H 'Content-Type: application/json' -H 'Idempotency-Key: sop1.k2' \\",
        f"     \"$BASE{chat}\" -d '{video}, \"stream\": true}}'",
        "",
        "# 3. an explicit async job, polled at the 202's Retry-After, then its result",
        f"curl -sS -D - -H @.auth -H 'Content-Type: application/json' -H 'Idempotency-Key: sop1.k3' \\",
        f"     \"$BASE{jobs.JOBS_PATH}\" -d '{video}}}'          # 202 JobAccepted: job_handle",
        "JOB=<job_handle>",
        f"curl -sS -H @.auth \"$BASE{job}\"                     # JobStatus",
        f"curl -sS -H @.auth \"$BASE{job}/result\"              # JobResult "
        f"({errors.http_status('result_pending')} result_pending while it runs)",
        f"curl -sS -N -H @.auth -H 'Last-Event-ID: <id>' \"$BASE{job}/events\"   # replay from a cursor",
        f"curl -sS -X DELETE -H @.auth \"$BASE{job}\"           # cancel: client_cancelled",
        "",
        "# 4. async on the chat route itself",
        f"curl -sS -D - -H @.auth -H 'Content-Type: application/json' -H 'Prefer: respond-async' \\",
        f"     -H 'Idempotency-Key: sop1.k4' \"$BASE{chat}\" -d '{video}}}'",
        "",
        "# 5. an owned upload, then the chat request names it",
        f"curl -sS -H @.auth -H 'Content-Type: application/json' \"$BASE{uploads.UPLOADS_PATH}\" \\",
        "     -d '{\"accepted_mime\": [\"video/mp4\"], \"max_bytes\": 67108864}'   # 201 UploadCreated",
        "UPL=<upload_handle>",
        f"curl -sS -X PUT -H @.auth -H 'Content-Type: video/mp4' --data-binary @clip.mp4 \\",
        f"     \"$BASE{uploads.DESTINATION_PATH.replace('{handle}', '$UPL')}\"   # 204",
        f"curl -sS -X POST -H @.auth \"$BASE{uploads.COMPLETE_PATH.replace('{handle}', '$UPL')}\"",
        "#   then in the chat body: {\"type\": \"video_url\", \"video_url\": "
        f"{{\"url\": \"{validate.UPLOAD_SCHEME}$UPL\"}}}}",
        "",
        "# 6. R94: the same key in another mode is a conflict, and writes nothing",
        f"curl -sS -H @.auth -H 'Content-Type: application/json' -H 'Idempotency-Key: sop1.k1' \\",
        f"     \"$BASE{chat}\" -d '{video}, \"stream\": true}}'   "
        f"# {errors.http_status('idempotency_conflict')} idempotency_conflict",
        "```",
    ]


def render() -> str:
    release = certify.published_release()
    retry = errors.RETRY_AFTER_CODES
    lines = [
        "# E4B — Marlin endpoint capability document and headless examples",
        "",
        "**Generated** by `tests/integration/backend/endpoint_doc.py --write` from the code at",
        "the commit this file is in; `tests/integration/backend/test_endpoint_doc.py` fails when",
        "it is stale. It describes the metered endpoint **as the cutover mounts it** (G2-R1,",
        "held at this commit): until then the deployed gateway is the legacy one. Nothing here",
        "is a measured limit or an SLO: measured limits are the box's (E4B-release-decision.md).",
        "",
        "## Model",
        "",
        *_table(("Field", "Value", "Source"), (
            ("requested model (pinned)", f"`{release['requested_model']}`",
             "G6B `marlin_release` / contracts v2 fixtures"),
            ("rate card", f"`{release['rate_card_version']}`",
             "provisional until P-01 decides the rates"),
            ("serving revision's engine-options digest", f"`{release['engine_options_digest']}`",
             "⚠️ the fixture placeholder, not W3's measured pin (E4B config-pin finding)"),
            ("runtime image", f"`{release['runtime_image_ref']}`",
             "⚠️ a moving tag in the published record (same finding)"))),
        "",
        "## Routes",
        "",
        f"Every `/v1/` route but {', '.join(f'`{path}`' for path in unauthenticated())} takes "
        "`Authorization: Bearer <key>` (a scoped key G6B's operator CLI issues); a handle of "
        "another tenant, an unknown handle and a malformed one are the same 404.",
        "",
        *_table(("Method", "Path", "Module", "What"),
                ((method, f"`{path}`", module, DESCRIPTIONS.get((method, path), "⚠️ undescribed"))
                 for method, path, module in route_table())),
        "",
        "## Execution modes and idempotency (R94)",
        "",
        *_table(("Mode", "How a client selects it"), (
            (f"`{records.ExecutionMode.sync}`", f"`POST {ingress.CHAT_PATH}` (the default)"),
            (f"`{records.ExecutionMode.stream}`", "`\"stream\": true` on the chat route (SSE)"),
            (f"`{records.ExecutionMode.async_}`",
             f"`POST {jobs.JOBS_PATH}`, or `{wire.HEADER_PREFER}: respond-async` on chat"))),
        "",
        f"`{wire.HEADER_IDEMPOTENCY_KEY}` names one operation, one canonical payload **and one "
        "mode** (R94): a replay in the same mode answers the same job (the "
        f"`{wire.HEADER_IDEMPOTENCY_REPLAYED}` header says so); the same key with another "
        "payload or another mode is `409 idempotency_conflict` and writes nothing. A key "
        f"keeps answering for `idempotency_ttl_s` = {DEFAULTS.idempotency_ttl_s:g} s after "
        f"terminal. A 202 carries `{wire.HEADER_RETRY_AFTER}: {jobs.POLL_AFTER_S}` as the poll hint "
        f"and `{jobs.HEADER_LOCATION}` naming the job. `POST {jobs.JOBS_PATH}` is always async: "
        f"a body with `\"stream\": true` is refused {code('invalid_request')} with `param` "
        "`stream`.",
        "",
        "## Request parameters",
        "",
        f"Accepted: {', '.join(f'`{name}`' for name in sorted(validate.SUPPORTED))}.",
        "",
        f"Every other name is refused as `unsupported_parameter` naming itself; these are named "
        f"so the refusal is deliberate: "
        f"{', '.join(f'`{name}`' for name in sorted(validate.UNSUPPORTED))}. A `null` value is "
        "`invalid_request`.",
        "",
        f"Content parts: `{validate.TEXT_TYPE}` and `{validate.VIDEO_TYPE}`; a video reference "
        f"is one of {', '.join(f'`{scheme}`' for scheme in validate.HTTP_SCHEMES)}, "
        f"`{validate.DATA_PREFIX}<mime>;base64,…` or `{validate.UPLOAD_SCHEME}<handle>`. Roles: "
        f"{', '.join(f'`{role}`' for role in sorted(validate.ROLES))}.",
        "",
        "## Limits (contracts `limits.DEFAULTS` and the validator)",
        "",
        *_table(("Setting", "Default", "Bounds"),
                ((f"`{name}`", f"{getattr(DEFAULTS, name):g}" if isinstance(
                    getattr(DEFAULTS, name), float) else getattr(DEFAULTS, name), meaning)
                 for name, meaning in LIMITS)),
        "",
        *_table(("Validator bound", "Value"),
                ((f"`{name}`", getattr(validate, name)) for name in BOUNDS)),
        "",
        "## Error codes (`contracts.errors`)",
        "",
        "The body is always `{\"error\": {\"message\", \"type\", \"code\", \"param\"}}` with the "
        "fixed message below; nothing upstream is ever echoed.",
        "",
        *_table(("Code", "HTTP", "Type", "Retry-After", "Message"),
                ((f"`{code}`", status, kind, "yes" if code in retry else "",
                  errors.MESSAGES[code])
                 for code, (status, kind) in sorted(errors.HTTP_ERRORS.items(),
                                                    key=lambda item: (item[1][0], item[0])))),
        "",
        "In a stream that already answered 200, a terminal error event carries one of "
        f"{', '.join(f'`{code}`' for code in sorted(errors.STREAM_CODES))}.",
        "",
        "## Terminal causes, cancellation and what is billed",
        "",
        *_table(("Cause", "States", "A canceller may give it", "Billable"),
                ((f"`{cause}`", ", ".join(sorted(records.states_for_cause(cause))),
                  "yes" if cause in records.CANCEL_CAUSES else "",
                  "yes" if cause in records.BILLABLE_CAUSES else "no (platform-absorbed)")
                 for cause in records.TerminalCause)),
        "",
        "## Usage certainty and settlement",
        "",
        f"Usage is {' or '.join(f'`{c}`' for c in records.UsageCertainty)}; only authoritative "
        "usage on a billable cause settles a debit. Settlement states: "
        f"{', '.join(f'`{s}`' for s in records.SettlementState)}. An unknown-usage hold is "
        f"reconciled after `unknown_usage_reconcile_s` = {DEFAULTS.unknown_usage_reconcile_s:g} s.",
        "",
        "## Headers",
        "",
        ", ".join(f"`{name}`" for name in headers()) + ".",
        "",
        "## Response shapes (`contracts.wire`)",
        "",
        *_table(("Shape", "Fields"),
                ((f"`{shape.__name__}`", ", ".join(f"`{field}`" for field in shape.model_fields))
                 for shape in SHAPES)),
        "",
        "## Headless examples",
        "",
        *examples(release["requested_model"]),
        "",
    ]
    return "\n".join(lines)


def committed_log() -> str:
    text = DOC.read_text() if DOC.exists() else ""
    return text[text.index(LOG):] if LOG in text else LOG + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--write", action="store_true", help="regenerate the document")
    parser.add_argument("--check", action="store_true", help="exit 1 if it is stale")
    args = parser.parse_args(argv)
    body = render()
    if args.write:
        DOC.write_text(body + committed_log())
        return 0
    if args.check:
        current = DOC.read_text() if DOC.exists() else ""
        return 0 if current.split(LOG)[0] == body else 1
    print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
