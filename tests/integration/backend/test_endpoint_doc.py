"""E4B.c: the endpoint document says what the code does, and the release decision's links
resolve. No stack, no network: every case reads the checkout.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import endpoint_doc                                     # noqa: E402

from infrx.contracts import errors                     # noqa: E402

EVIDENCE = endpoint_doc.DOC.parent
DECISION = EVIDENCE / "E4B-release-decision.md"


def test_e4b_the_endpoint_doc_is_what_the_code_generates():
    """The committed document is the generator's output at this commit, up to its log:
    a route, limit, code or shape that changed without a regeneration fails here."""
    committed = endpoint_doc.DOC.read_text()
    assert endpoint_doc.LOG in committed, "the document keeps a verification log"
    assert committed.split(endpoint_doc.LOG)[0] == endpoint_doc.render(), \
        "stale: run tests/integration/backend/endpoint_doc.py --write"
    assert endpoint_doc.main(["--check"]) == 0


def test_e4b_a_regeneration_keeps_the_verification_log(tmp_path, monkeypatch):
    """`--write` replaces the generated part and keeps the log: history is appended to,
    never rewritten (the research convention)."""
    doc = tmp_path / "E4B-endpoint.md"
    doc.write_text("# old\n" + endpoint_doc.LOG + "\n- 2026-09-23: an earlier entry\n")
    monkeypatch.setattr(endpoint_doc, "DOC", doc)
    assert endpoint_doc.main(["--check"]) == 1
    assert endpoint_doc.main(["--write"]) == 0
    assert doc.read_text() == (endpoint_doc.render() + endpoint_doc.LOG
                               + "\n- 2026-09-23: an earlier entry\n")
    assert endpoint_doc.main(["--check"]) == 0


def test_e4b_every_mounted_route_has_one_description_and_every_description_a_route():
    """The route table is the decorators of the mounted modules; the prose may not add a
    route the code lacks or leave one out."""
    table = endpoint_doc.route_table()
    routes = {(method, path) for method, path, _ in table}
    assert routes == set(endpoint_doc.DESCRIPTIONS), (
        sorted(routes - set(endpoint_doc.DESCRIPTIONS)),
        sorted(set(endpoint_doc.DESCRIPTIONS) - routes))
    assert len(routes) == len(table), "one module per route"
    families = {path.split("/")[2] for _, path in routes if path.startswith("/v1/")}
    assert families == {"chat", "jobs", "models", "uploads"}
    assert ("DELETE", "/v1/jobs/{handle}") in routes and ("PUT", "/v1/uploads/{handle}") in routes


def test_e4b_the_error_catalogue_is_complete_and_only_public():
    """Every public code appears with its status and its fixed message, the Retry-After
    column is `RETRY_AFTER_CODES`, and no internal code is documented."""
    doc = endpoint_doc.render()
    section = doc.split("## Error codes")[1].split("\n## ")[0]
    rows = dict(re.findall(r"^\| `(\w+)` \| (\d{3}) \|", section, re.M))
    assert rows == {code: str(status) for code, (status, _) in errors.HTTP_ERRORS.items()}
    retry = set(re.findall(r"^\| `(\w+)` \| \d{3} \| \w+ \| yes \|", section, re.M))
    assert retry == set(errors.RETRY_AFTER_CODES)
    for code in errors.HTTP_ERRORS:
        assert errors.MESSAGES[code] in doc, code
    for code in errors.STREAM_CODES:
        assert f"`{code}`" in doc
    assert not [code for code in errors.INTERNAL_CODES if f"`{code}`" in section]


def test_e4b_the_examples_call_only_mounted_routes_with_the_headers_the_contract_needs():
    """Each curl names a mounted route; every request that can create a job carries an
    Idempotency-Key; no key is ever written into a command line."""
    text = "\n".join(endpoint_doc.examples("m"))
    patterns = [re.compile("^" + re.sub(r"\\\{handle\\\}", r"\\$[A-Z]+", re.escape(path)) + "$")
                for _, path in endpoint_doc.DESCRIPTIONS]
    calls = re.findall(r'"\$BASE(/[^"]+)"', text)
    assert calls and all(any(p.match(call) for p in patterns) for call in calls), calls
    for block in text.split("\n\n"):
        creates = re.search(r'"\$BASE(/v1/chat/completions|/v1/jobs)"', block)
        if creates:
            assert "Idempotency-Key:" in block, block
    assert "Bearer $INFRX_API_KEY" not in text and "sk-" not in text
    assert "409 idempotency_conflict" in text, "the R94 cross-mode rule is shown"


def _anchors(path: Path) -> set[str]:
    slugs = set()
    for heading in re.findall(r"^#+ (.+)$", path.read_text(), re.M):
        slug = re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")
        slugs.add(slug)
    return slugs


def test_e4b_the_release_decision_links_resolve_to_files_and_sections():
    """The decision's rollback triggers and runbook index point at I2B's and I3B's runbooks:
    every relative link must reach an existing file, and an anchor an existing heading."""
    missing = []
    for doc in (DECISION, endpoint_doc.DOC):
        for target in re.findall(r"\]\(([^)#\s]+)(?:#([\w\-]+))?\)", doc.read_text()):
            link, anchor = target
            if re.match(r"^[a-z]+://", link):
                continue
            path = (doc.parent / link).resolve()
            if not path.exists():
                missing.append(f"{doc.name}: {link}")
            elif anchor and path.suffix == ".md" and anchor not in _anchors(path):
                missing.append(f"{doc.name}: {link}#{anchor}")
    assert missing == []
    assert "BACKEND-READY" in DECISION.read_text()


def test_e4b_every_status_the_prose_cites_is_the_one_the_code_answers():
    """Review F9: an error code cited with a status - "`code` (NNN)" in a description or
    "NNN code" in an example - carries the catalogue's status; a success status a
    description cites is the one its module answers."""
    doc = endpoint_doc.render()
    cited = [*re.findall(r"`(\w+)` \((\d{3})\)", doc),
             *[(name, status) for status, name in re.findall(r"\b(\d{3}) (\w+)\b", doc)
               if name in errors.HTTP_ERRORS]]
    assert len(cited) >= 5, cited
    wrong = [(name, status) for name, status in cited
             if name in errors.HTTP_ERRORS and int(status) != errors.http_status(name)]
    assert wrong == [] and all(name in errors.HTTP_ERRORS for name, _ in cited), cited
    from infrx.gateway.routes import jobs, uploads
    for source, status in ((jobs, 202), (uploads, 201), (uploads, 204)):
        assert f"status_code={status}" in Path(source.__file__).read_text(), (source, status)


def test_e4b_the_prose_names_the_cause_the_auth_and_the_headers_the_modules_implement():
    """Review F9: DELETE's cause is the one jobs.py passes to the relay; the routes said to
    need no key are exactly those whose module never authenticates; the Headers list has the
    202's `Location`; and the streaming refusal on POST /v1/jobs is documented as the module
    raises it."""
    from infrx.gateway.routes import jobs
    source = Path(jobs.__file__).read_text()
    (cause,) = set(re.findall(r"cause=TerminalCause\.(\w+)", source))
    delete = endpoint_doc.DESCRIPTIONS[("DELETE", "/v1/jobs/{handle}")]
    assert f"`{cause}`" in delete and len(re.findall(r"`(\w+)`", delete)) == 1
    assert endpoint_doc.unauthenticated() == ["/v1/models"]
    doc = endpoint_doc.render()
    assert "Every `/v1/` route but `/v1/models` takes `Authorization" in doc
    headers = doc.split("## Headers")[1].split("\n## ")[0]
    assert f"`{jobs.HEADER_LOCATION}`" in headers
    assert 'param="stream"' in source and "errors.InvalidRequest" in source
    assert ("`POST /v1/jobs` is always async: a body with `\"stream\": true` is refused "
            f"`invalid_request` ({errors.http_status('invalid_request')}) with `param` `stream`"
            in " ".join(doc.split()))


def test_e4b_every_success_status_the_prose_cites_is_the_one_its_route_answers():
    """Review V1: the 2xx statuses are read from the function that builds each route's
    answer (its `status_code=`, or Starlette's default 200), every 2xx the document cites is
    one of those, at the place that names it, and there is no other."""
    import ast
    routes = sorted(endpoint_doc.SUCCESS_BUILT_BY)
    for route in routes:
        source, node = endpoint_doc.success_function(*route)
        segment = ast.get_source_segment(source, node)
        status = endpoint_doc.ok(*route)
        assert (f"status_code={status}" in segment) if "status_code=" in segment \
            else status == 200, (route, status)
    jobs_ok = endpoint_doc.ok("POST", "/v1/jobs")
    upload, put, done = (endpoint_doc.ok(*route) for route in (
        ("POST", "/v1/uploads"), ("PUT", "/v1/uploads/{handle}"),
        ("POST", "/v1/uploads/{handle}/complete")))
    doc = " ".join(endpoint_doc.render().split())
    expected = [f"a {jobs_ok} job with", f"answered {jobs_ok} `JobAccepted`",
                f"constraints: {upload} `UploadCreated`", f"destination: {put} |",
                f"(no body): {done} `UploadCompleted`", f"A {jobs_ok} carries",
                "a stream that already answered 200", f"polled at the {jobs_ok}'s Retry-After",
                f"# {jobs_ok} JobAccepted", f"# {upload} UploadCreated", f"# {put} curl"]
    assert [snippet for snippet in expected if snippet not in doc] == []
    assert len(re.findall(r"\b2\d\d\b", doc)) == len(expected)


def test_e4b_the_model_table_says_whether_the_published_release_is_the_measured_pin(
        monkeypatch):
    """Review V6: the Model table's notes are read from the published release against W3's
    serving record - a placeholder or a moving tag is flagged, the measured pin is named -
    so the fixture fix cannot leave a false note behind."""
    import certify
    record = certify.serving_record()
    pinned = {"requested_model": certify.published_release()["requested_model"],
              "rate_card_version": "rc",
              "engine_options_digest": record["engine_options_digest"],
              "runtime_image_ref": record["runtime_image"]["ref"]}
    for published, flagged in ((pinned, 0),
                               ({**pinned, "engine_options_digest": "sha256:" + "44" * 32,
                                 "runtime_image_ref": "vllm/vllm-openai:nightly"}, 2)):
        monkeypatch.setattr(certify, "published_release", lambda published=published: published)
        model = endpoint_doc.render().split("## Model")[1].split("## Routes")[0]
        assert model.count("⚠️ not W3's measured pin") == flagged, model
        assert model.count("W3's measured pin (`models/marlin2b/serving-version.json`)") \
            == 2 - flagged, model
