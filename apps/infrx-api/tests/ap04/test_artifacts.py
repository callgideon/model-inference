"""API-ARTIFACT (AP-04a-e): model projects, verified artifacts, serving revisions, adoption -
over HTTP on the routes as mounted, on the fake world and (marked `pg`, `INFRX_D_TASK=ap4`) on
PostgreSQL 0061 as the Lab control login with real bytes on MinIO. Every case is named by a
mutant in `mutants.py`.

    uv run --frozen pytest -q tests/ap04                              # fake world
    INFRX_D_TASK=ap4 uv run --frozen pytest -q tests/ap04             # + PostgreSQL + MinIO
"""
from __future__ import annotations

import json

import pytest

from infrx.contracts.v2 import fixtures as v2fix
from infrx.lab.artifacts import verify
from infrx.lab.artifacts.store import MemoryControlOps

from .conftest import (COMMIT, REPO, SERVING_VERSION, TOKEN, entry, manifest, marlin_files,
                       run)

PROJECTS = "/lab/v1/control/model-projects"
UPLOADS = "/lab/v1/artifacts/uploads"


# =================================================================== helpers ===
def project(w, actor="dev_a", slug="marlin-test", name="Marlin test", key=None):
    r = w.call("POST", PROJECTS, actor, key or w.key(), json={"name": name, "slug": slug})
    assert r.status_code == 201, r.text
    return r.json()


def open_upload(w, files, project_id, actor="dev_a"):
    r = w.call("POST", UPLOADS, actor, w.key(),
               json={"project_id": project_id, "files": manifest(files)})
    assert r.status_code == 201, r.text
    return r.json()


def put(w, upload_id, path, blob, actor="dev_a"):
    grant = w.call("POST", f"{UPLOADS}/{upload_id}/parts", actor,
                   json={"relative_path": path})
    assert grant.status_code == 200, grant.text
    w.put(grant.json(), blob)


def complete(w, upload, actor="dev_a", key=None):
    return w.call("POST", f"{UPLOADS}/{upload['upload_id']}/complete", actor, key or w.key(),
                  json={"manifest_sha256": upload["manifest_sha256"]})


def operation(w, op_id, actor="dev_a"):
    r = w.call("GET", f"/lab/v1/operations/{op_id}", actor)
    assert r.status_code == 200, r.text
    return r.json()


def verified(w, files=None, slug="marlin-test"):
    """An artifact through the upload path: (project, artifact)."""
    files = files or marlin_files()
    p = project(w, slug=slug)
    up = open_upload(w, files, p["project_id"])
    for path, blob in files.items():
        put(w, up["upload_id"], path, blob)
    op = complete(w, up).json()
    w.worker()
    assert operation(w, op["operation_id"])["state"] == "succeeded"
    artifact = w.call("GET", f"/lab/v1/artifacts/{op['resource_id']}")
    assert artifact.status_code == 200, artifact.text
    return p, artifact.json()


def reasons(response) -> set[str]:
    return {e["code"] for e in response.json()["error"]["field_errors"]}


def start_import(w, p, files=None, **change):
    body = {"project_id": p["project_id"], "files": manifest(files or marlin_files()),
            "source": {"host": "huggingface.co", "repo": REPO, "commit": COMMIT},
            "secret_ref": "env:HF_STUB_TOKEN", **change}
    return w.call("POST", "/lab/v1/artifacts/imports", "dev_a", w.key(), json=body)


# ======================================================================= 04a ===
def test_ap04__an_empty_provider_creates_a_project_without_a_serving_claim(world) -> None:
    """04a exit: no preseeded serving revision; a repeated action returns the same id."""
    w = world
    servings = len(w.w.control_store.servings)
    key = w.key()
    first = project(w, key=key)
    again = project(w, key=key)
    assert again["project_id"] == first["project_id"]
    assert first["model_uuid"] is None and first["public_model_id"] is None
    listed = w.call("GET", PROJECTS, "viewer_a")
    assert listed.status_code == 200 and listed.headers["cache-control"] == "no-store"
    assert [p["project_id"] for p in listed.json()["data"]] == [first["project_id"]]
    assert len(w.w.control_store.servings) == servings, "a project made a serving claim"


def test_ap04__a_key_reused_with_another_body_is_an_idempotency_conflict(world) -> None:
    w = world
    key = w.key()
    project(w, key=key)
    r = w.call("POST", PROJECTS, "dev_a", key, json={"name": "Other", "slug": "marlin-test"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "idempotency_conflict"


def test_ap04__a_slug_is_unique_within_its_provider_only(world) -> None:
    w = world
    project(w)
    taken = w.call("POST", PROJECTS, "dev_a", w.key(), json={"name": "M", "slug": "marlin-test"})
    assert taken.status_code == 409 and taken.json()["error"]["code"] == "state_conflict"
    assert project(w, actor="dev_b")["provider_org_id"] == w.B


def test_ap04__membership_and_role_decide_every_door(world) -> None:
    """A viewer reads and never creates; another provider's member and a non-member see
    nothing (404, never a 403 that confirms the row); no session is a 401."""
    w = world
    p = project(w)
    viewer = w.call("POST", PROJECTS, "viewer_a", w.key(), json={"name": "V", "slug": "v"})
    assert viewer.status_code == 403
    assert w.call("GET", PROJECTS, "viewer_a").status_code == 200
    assert w.call("POST", UPLOADS, "viewer_a", w.key(), json={
        "project_id": p["project_id"], "files": manifest(marlin_files())}).status_code == 403
    foreign = w.call("GET", f"{PROJECTS}/{p['project_id']}/revisions", "dev_b")
    assert foreign.status_code == 404
    assert w.call("GET", PROJECTS, "outsider").status_code == 404
    assert w.call("GET", PROJECTS, "nobody").status_code == 401


def test_ap04__lists_page_with_opaque_cursors(world) -> None:
    w = world
    made = []
    for i in range(3):
        made.append(project(w, slug=f"p-{i}")["project_id"])
        w.advance(1)                                 # (created_at, id): creation order
    first = w.call("GET", PROJECTS + "?limit=2").json()
    assert len(first["data"]) == 2 and first["next_cursor"]
    rest = w.call("GET", PROJECTS + f"?limit=2&cursor={first['next_cursor']}").json()
    assert [p["project_id"] for p in first["data"] + rest["data"]] == made
    assert rest["next_cursor"] is None
    bad = w.call("GET", PROJECTS + "?cursor=not-a-cursor")
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_cursor"


def test_ap04__refusals_are_the_r270_envelope(world) -> None:
    """A mutation without Idempotency-Key and a body FastAPI refuses are 422 envelopes that
    name the field and never echo the input; nothing is created."""
    w = world
    missing = w.call("POST", PROJECTS, "dev_a", json={"name": "M", "slug": "m"})
    body = missing.json()
    assert missing.status_code == 422 and set(body) == {"error"}, body
    assert body["error"]["code"] == "invalid_request"
    assert any("idempotency-key" in e["field"].lower() for e in body["error"]["field_errors"])
    bad = w.call("POST", PROJECTS, "dev_a", w.key(),
                 json={"name": "M", "slug": "NOT A SLUG", "role": "administrator"})
    assert bad.status_code == 422 and "NOT A SLUG" not in bad.text and "error" in bad.json()
    assert {e["field"] for e in bad.json()["error"]["field_errors"]} >= {"body.slug", "body.role"}
    assert w.call("GET", PROJECTS).json()["data"] == []


# ======================================================================= 04b ===
def test_ap04__reordered_resumed_parts_verify_into_an_artifact(world) -> None:
    w = world
    files = marlin_files()
    p = project(w)
    up = open_upload(w, files, p["project_id"])
    assert up["state"] == "open" and up["manifest_sha256"].startswith("sha256:")
    for path in sorted(files, reverse=True):              # reordered
        put(w, up["upload_id"], path, files[path])
    first = "model-00001-of-00002.safetensors"
    put(w, up["upload_id"], first, files[first])           # resumed: a fresh URL, same bytes
    undeclared = w.call("POST", f"{UPLOADS}/{up['upload_id']}/parts", json={
        "relative_path": "modeling_marlin.json"})
    assert undeclared.status_code == 422, "a URL was issued for an undeclared path"
    r = complete(w, up)
    assert r.status_code == 202 and r.headers["location"] == \
        f"/lab/v1/operations/{r.json()['operation_id']}"
    op = r.json()
    assert op["state"] == "queued" and op["kind"] == "artifact.upload.verify"
    assert w.call("GET", f"/lab/v1/artifacts/{op['resource_id']}").status_code == 404
    assert w.worker() == 1
    done = operation(w, op["operation_id"])
    assert done["state"] == "succeeded" and done["error"] is None
    got = w.call("GET", f"/lab/v1/artifacts/{op['resource_id']}", "viewer_a")
    assert got.status_code == 200, got.text
    art = got.json()
    assert art["source"] == "upload" and art["compatibility"]["supported"] is True
    assert {f["relative_path"]: f["sha256"] for f in art["files"]} == \
        {e["relative_path"]: e["sha256"] for e in manifest(files)}
    assert art["manifest_sha256"] == up["manifest_sha256"]


@pytest.mark.parametrize("defect", ["wrong_hash", "missing_shard", "extra_path"])
def test_ap04__unverified_bytes_never_become_an_artifact(world, defect) -> None:
    """Wrong hash, a missing shard, an extra object: the operation fails naming the path and
    the session's bytes are removed."""
    w = world
    files = marlin_files()
    p = project(w)
    up = open_upload(w, files, p["project_id"])
    shard = "model-00002-of-00002.safetensors"
    for path, blob in files.items():
        if defect == "missing_shard" and path == shard:
            continue
        put(w, up["upload_id"], path, b"tampered" if defect == "wrong_hash" and path == shard
            else blob)
    if defect == "extra_path":
        w.put({"url": run(w.objects.presign_put(
            f"artifacts/{w.A}/{up['upload_id']}/modeling_marlin.py", expires_s=60))}, b"code")
    op = complete(w, up).json()
    w.worker()
    done = operation(w, op["operation_id"])
    assert done["state"] == "failed"
    codes = {(e["field"], e["code"]) for e in done["error"]["field_errors"]}
    assert codes == {{"wrong_hash": (f"files.{shard}", "digest_mismatch"),
                      "missing_shard": (f"files.{shard}", "missing_file"),
                      "extra_path": ("files.modeling_marlin.py", "extra_path")}[defect]}
    assert w.stored_keys() == [], "unverified bytes were kept"
    assert w.call("GET", f"/lab/v1/artifacts/{op['resource_id']}").status_code == 404


def test_ap04__unsafe_paths_and_code_bearing_files_are_refused_before_any_byte(world) -> None:
    w = world
    p = project(w)
    for path, code in (("../escape.json", "unsafe_path"), ("/abs.json", "unsafe_path"),
                       (".git/config.json", "unsafe_path"), ("a\\b.json", "unsafe_path"),
                       ("dir//x.json", "unsafe_path"), ("modeling_marlin.py", "code_bearing"),
                       ("pytorch_model.bin", "code_bearing")):
        r = w.call("POST", UPLOADS, "dev_a", w.key(), json={
            "project_id": p["project_id"], "files": [entry(path, b"x")]})
        assert r.status_code == 422, (path, r.text)
        assert code in json.dumps(r.json()["error"]["field_errors"]), (path, r.text)
    too_big = entry("model-00001-of-00002.safetensors", b"")
    too_big["bytes"] = 6 * 1024 ** 3
    many = [entry(f"f{i}.json", b"{}") for i in range(257)]
    for files in ([too_big], many, [entry("a.json", b"1"), entry("a.json", b"2")]):
        assert w.call("POST", UPLOADS, "dev_a", w.key(), json={
            "project_id": p["project_id"], "files": files}).status_code == 422
    assert w.call("POST", UPLOADS, "dev_b", w.key(), json={
        "project_id": p["project_id"], "files": manifest(marlin_files())}).status_code == 404


def test_ap04__an_interrupted_upload_expires_cleanly(world) -> None:
    w = world
    files = marlin_files()
    up = open_upload(w, files, project(w)["project_id"])
    put(w, up["upload_id"], "config.json", files["config.json"])
    w.advance(24 * 3600 + 1)
    late = w.call("POST", f"{UPLOADS}/{up['upload_id']}/parts", json={
        "relative_path": "tokenizer.json"})
    assert late.status_code == 410 and late.json()["error"]["code"] == "upload_expired"
    assert w.worker() == 1
    assert w.stored_keys() == []
    assert complete(w, up).status_code == 410
    assert w.worker() == 0, "an expired session is swept once"


def test_ap04__a_duplicate_completion_returns_the_one_operation(world) -> None:
    w = world
    files = marlin_files()
    up = open_upload(w, files, project(w)["project_id"])
    for path, blob in files.items():
        put(w, up["upload_id"], path, blob)
    wrong = w.call("POST", f"{UPLOADS}/{up['upload_id']}/complete", "dev_a", w.key(),
                   json={"manifest_sha256": "sha256:" + "0" * 64})
    assert wrong.status_code == 422
    first, second = complete(w, up).json(), complete(w, up).json()
    assert first["operation_id"] == second["operation_id"]
    assert w.worker() == 1 and w.worker() == 0
    third = complete(w, up)
    assert third.status_code == 202 and third.json()["state"] == "succeeded"
    assert w.call("POST", f"{UPLOADS}/{up['upload_id']}/parts", json={
        "relative_path": "config.json"}).status_code == 409


def test_ap04__a_worker_killed_while_hashing_is_fenced_out(world) -> None:
    """A lease that expires mid-hash is taken over under a new fence; the first worker's
    late writes are refused and the artifact is written once."""
    w = world
    files = marlin_files()
    up = open_upload(w, files, project(w)["project_id"])
    for path, blob in files.items():
        put(w, up["upload_id"], path, blob)
    op = complete(w, up).json()
    from infrx.contracts import errors
    stale = run(w.ops.lease(op["operation_id"], "killed-worker", 600))
    assert stale == 1 and w.worker("second") == 0, "a live lease was taken over"
    w.advance(601)
    newer = run(w.ops.lease(op["operation_id"], "second", 600))
    assert newer == 2
    with pytest.raises(errors.StaleLease):
        run(w.ops.advance(op["operation_id"], stale, "hashing:late"))
    w.advance(601)
    assert w.worker("third") == 1
    assert operation(w, op["operation_id"])["state"] == "succeeded"
    with pytest.raises(errors.StaleLease):
        run(w.ops.finish(op["operation_id"], stale, "failed"))
    assert w.call("GET", f"/lab/v1/artifacts/{op['resource_id']}").status_code == 200


def test_ap04__a_retry_after_the_artifact_write_keeps_one_artifact(world) -> None:
    """The process dies after the artifact row and before the session moves: the rerun under
    a new fence finds the same artifact id and finishes once."""
    w = world
    files = marlin_files()
    up = open_upload(w, files, project(w)["project_id"])
    for path, blob in files.items():
        put(w, up["upload_id"], path, blob)
    op = complete(w, up).json()
    store, real = w.store, w.store.update

    async def dies(row, *, expected_state):
        raise RuntimeError("killed after the artifact insert")
    store.update = dies
    with pytest.raises(RuntimeError):
        w.worker("first")
    store.update = real
    w.advance(601)
    assert w.worker("second") == 1
    assert operation(w, op["operation_id"])["state"] == "succeeded"
    assert w.call("GET", f"/lab/v1/artifacts/{op['resource_id']}").status_code == 200


def test_ap04__a_session_moves_only_from_the_state_it_was_read_in(world) -> None:
    """The intake rows are compare-and-set on `state`: a sweep holding a stale `open` copy
    never expires a session a completion already moved."""
    from infrx.contracts import errors
    from infrx.lab.artifacts.store import Upload
    w = world
    up = open_upload(w, marlin_files(), project(w)["project_id"])
    stale = run(w.store.get(Upload, w.A, up["upload_id"]))
    assert complete(w, up).status_code == 202
    with pytest.raises(errors.StateConflict):
        run(w.store.update(stale.model_copy(update={"state": "expired"}), expected_state="open"))
    assert run(w.store.get(Upload, w.A, up["upload_id"])).state == "verifying"


def test_ap04__an_operation_is_read_only_inside_its_workspace(world) -> None:
    w = world
    files = marlin_files()
    up = open_upload(w, files, project(w)["project_id"])
    op = complete(w, up).json()
    assert operation(w, op["operation_id"], "viewer_a")["operation_id"] == op["operation_id"]
    assert w.call("GET", f"/lab/v1/operations/{op['operation_id']}", "dev_b").status_code == 404
    assert w.call("GET", "/lab/v1/operations/not-an-id").status_code == 404


# ======================================================================= 04c ===
def test_ap04__a_pinned_import_fetches_and_verifies_every_file(world) -> None:
    w = world
    p = project(w)
    r = start_import(w, p)
    assert r.status_code == 202 and r.json()["kind"] == "artifact.import"
    op = r.json()
    assert w.worker() == 1
    assert operation(w, op["operation_id"])["state"] == "succeeded"
    got = w.call("GET", f"/lab/v1/artifacts/{op['resource_id']}")
    assert got.status_code == 200, got.text
    art = got.json()
    assert (art["source"], art["source_repo"], art["source_commit"]) == ("import", REPO, COMMIT)
    assert len(w.stub.seen) == len(marlin_files())
    assert all(f"/resolve/{COMMIT}/" in url for url in w.stub.seen)
    assert TOKEN not in r.text + w.tables_text(), "the source credential was stored or echoed"


def test_ap04__a_mutable_ref_a_prohibited_source_or_a_raw_token_is_refused(world) -> None:
    w = world
    p = project(w)
    for change, code in (({"source": {"host": "huggingface.co", "repo": REPO,
                                      "commit": "main"}}, "mutable_ref"),
                         ({"source": {"host": "evil.example", "repo": REPO, "commit": COMMIT}},
                          "prohibited_source"),
                         ({"secret_ref": "hf_" + "x" * 34}, "not_a_secret_reference")):
        r = start_import(w, p, **change)
        assert r.status_code == 422 and code in r.text, r.text
        assert "hf_xxxx" not in r.text, "a token-shaped value was echoed"
    assert w.stub.seen == [] and w.worker() == 0


@pytest.mark.parametrize("failure", ["stale_credential", "offhost_redirect", "missing_file"])
def test_ap04__a_source_that_refuses_fails_the_import_and_keeps_nothing(world, failure) -> None:
    w = world
    p = project(w)
    files = marlin_files()
    if failure == "stale_credential":
        w.secret["value"] = "rotated-away"
    elif failure == "offhost_redirect":
        w.stub.redirect = "tokenizer.json"
    else:
        files = marlin_files(**{"extra.json": b"{}"})
    op = start_import(w, p, files).json()
    w.worker()
    done = operation(w, op["operation_id"])
    assert done["state"] == "failed"
    assert [e["code"] for e in done["error"]["field_errors"]] == [{
        "stale_credential": "source_unauthorized", "offhost_redirect": "redirect_refused",
        "missing_file": "missing_at_source"}[failure]]
    assert w.stored_keys() == []
    assert not any("evil.test" in url for url in w.stub.seen[:-1])


# ======================================================================= 04d ===
def test_ap04__the_supported_profile_becomes_an_immutable_serving_revision(world) -> None:
    w = world
    p, art = verified(w)
    key = w.key()
    body = {"artifact_id": art["artifact_id"]}
    r = w.call("POST", f"{PROJECTS}/{p['project_id']}/revisions", "dev_a", key, json=body)
    assert r.status_code == 201, r.text
    rev = r.json()
    assert rev["revision_label"] == rev["serving_version_id"]
    assert rev["public_model_id"] == "nemostation/marlin-test"
    serving = w.serving(rev["serving_version_id"])
    shards = [f["sha256"] for f in sorted(art["files"], key=lambda f: f["relative_path"])
              if f["relative_path"].endswith(".safetensors")]
    assert list(serving.weight_shard_digests) == shards
    assert serving.runtime_image_ref == verify.RUNTIME_IMAGE and serving.precision == "bfloat16"
    assert serving.model_revision == rev["model_revision"]
    again = w.call("POST", f"{PROJECTS}/{p['project_id']}/revisions", "dev_a", key, json=body)
    assert again.status_code == 201 and again.json() == rev
    listed = w.call("GET", f"{PROJECTS}/{p['project_id']}/revisions", "viewer_a").json()
    assert [x["serving_version_id"] for x in listed["data"]] == [rev["serving_version_id"]]


def test_ap04__unsupported_architecture_runtime_and_input_format_name_their_reasons(
        world) -> None:
    w = world
    config = json.loads(marlin_files()["config.json"])
    config["architectures"] = ["LlamaForCausalLM"]
    p, art = verified(w, marlin_files(**{"config.json": json.dumps(config).encode()}))
    assert art["compatibility"]["supported"] is False
    path = f"{PROJECTS}/{p['project_id']}/revisions"
    r = w.call("POST", path, "dev_a", w.key(), json={"artifact_id": art["artifact_id"]})
    assert r.status_code == 422 and reasons(r) == {"unsupported_architecture"}
    _, good = verified(w, slug="good")
    projects = w.call("GET", PROJECTS).json()["data"]
    gp = next(x for x in projects if x["slug"] == "good")["project_id"]
    asked = w.call("POST", f"{PROJECTS}/{gp}/revisions", "dev_a", w.key(), json={
        "artifact_id": good["artifact_id"],
        "profile": {"runtime_image_ref": "vllm/vllm-openai:latest", "precision": "float16",
                    "hardware": "H100", "input_schema_ref": "infrx.request.embed.v1"}})
    assert asked.status_code == 422 and reasons(asked) == {
        "unsupported_runtime", "unsupported_precision", "unsupported_hardware",
        "unsupported_input_format"}
    other = w.call("POST", path, "dev_a", w.key(), json={"artifact_id": good["artifact_id"]})
    assert other.status_code == 404, "an artifact of another project made a revision"


def test_ap04__two_revisions_of_one_artifact_never_race_for_a_label(world) -> None:
    w = world
    p, art = verified(w)
    path = f"{PROJECTS}/{p['project_id']}/revisions"
    made = [w.call("POST", path, "dev_a", w.key(), json={"artifact_id": art["artifact_id"]})
            for _ in range(2)]
    assert [r.status_code for r in made] == [201, 201], [r.text for r in made]
    labels = {r.json()["revision_label"] for r in made}
    assert len(labels) == 2
    assert w.call("POST", path, "viewer_a", w.key(),
                  json={"artifact_id": art["artifact_id"]}).status_code == 403


def test_ap04__the_profile_is_the_measured_marlin_serving_version() -> None:
    """The fixture of truth: the supported profile IS models/marlin2b/serving-version.json."""
    pinned = json.loads(SERVING_VERSION.read_text())
    flags = pinned["flags"]
    assert verify.RUNTIME_IMAGE == pinned["runtime_image"]["ref"]
    assert verify.ENGINE_OPTIONS_DIGEST == pinned["engine_options_digest"]
    assert flags[flags.index("--dtype") + 1] == verify.PRECISION
    native = json.loads(flags[flags.index("--hf-overrides") + 1])["architectures"]
    assert list(verify.ARCHITECTURES.values()) == native
    config = json.loads((SERVING_VERSION.parents[2] / "research/models/marlin2b/config.json")
                        .read_text())
    assert config["architectures"] == list(verify.ARCHITECTURES)
    assert config["model_type"] == verify.MODEL_TYPE


# ======================================================================= 04e ===
def adoption(w, **change):
    pinned = json.loads(SERVING_VERSION.read_text())["model"]
    files = [{"relative_path": p, "bytes": 1, "sha256": d, "media_type": "application/json"}
             for p, d in (("model-00001-of-00002.safetensors", pinned["weight_shard_digests"][0]),
                          ("model-00002-of-00002.safetensors", pinned["weight_shard_digests"][1]),
                          ("tokenizer.json", pinned["tokenizer_digest"]),
                          ("chat_template.jinja", pinned["chat_template_digest"]),
                          ("config.json", pinned["config_digest"]),
                          ("generation_config.json", pinned["generation_config_digest"]),
                          ("processor_config.json", pinned["processor_config_digest"]),
                          ("preprocessor_config.json", pinned["preprocessor_config_digest"]))]
    return {"provider_org_id": w.A, "serving_version_id": v2fix.IDS.serving_version,
            "files": files, "evidence_ref": "inventory-20260926T174146Z", **change}


def test_ap04__an_operator_adopts_existing_bytes_keeping_the_production_identity(world) -> None:
    w = world
    servings = dict(w.w.control_store.servings)
    key = w.key()
    r = w.call("POST", "/operator/v1/artifacts/adopt", "ops", key, json=adoption(w))
    assert r.status_code == 201, r.text
    art = r.json()
    assert art["source"] == "adopted" and art["provenance"]["evidence_ref"] == \
        "inventory-20260926T174146Z"
    assert art["provenance"]["serving_version_id"] == v2fix.IDS.serving_version
    assert art["source_commit"] == json.loads(SERVING_VERSION.read_text())["model"]["commit"]
    assert w.call("POST", "/operator/v1/artifacts/adopt", "ops", w.key(),
                  json=adoption(w)).json() == art
    assert dict(w.w.control_store.servings) == servings, "adoption wrote a serving revision"
    projects = w.call("GET", PROJECTS, "viewer_a").json()["data"]
    adopted = next(p for p in projects if p["project_id"] == art["project_id"])
    assert adopted["public_model_id"] == v2fix.PUBLIC_MODEL_ID
    revs = w.call("GET", f"{PROJECTS}/{adopted['project_id']}/revisions").json()["data"]
    assert [x["serving_version_id"] for x in revs] == [v2fix.IDS.serving_version]


def test_ap04__adoption_refuses_non_operators_foreign_resources_and_other_bytes(world) -> None:
    w = world
    assert w.call("POST", "/operator/v1/artifacts/adopt", "admin_a", w.key(),
                  json=adoption(w)).status_code == 403
    foreign = w.call("POST", "/operator/v1/artifacts/adopt", "ops", w.key(),
                     json=adoption(w, provider_org_id=w.B))
    assert foreign.status_code == 404
    other = adoption(w)
    other["files"][0]["sha256"] = "sha256:" + "1" * 64
    r = w.call("POST", "/operator/v1/artifacts/adopt", "ops", w.key(), json=other)
    assert r.status_code == 422 and reasons(r) == {"digest_mismatch"}
    assert w.call("GET", PROJECTS, "viewer_a").json()["data"] == []


def test_ap04__the_memory_operations_replay_and_cancel_by_the_protocol() -> None:
    """The in-memory `ControlOps` (until 0060): same key+hash replays, another hash is 409,
    cancel of a queued operation is terminal and a terminal one is never leased."""
    from datetime import UTC, datetime

    from infrx.contracts import api, errors

    async def clock():
        return datetime(2026, 10, 1, tzinfo=UTC)
    ops, actor = MemoryControlOps(clock), api.Actor(audience="session", user_id="u")
    start = dict(kind="k", resource_kind="r", resource_id="x", actor=actor, scope="s", key="k1")
    first, replayed = run(ops.start(**start, input_hash="h1"))
    again, replayed_again = run(ops.start(**start, input_hash="h1"))
    assert (replayed, replayed_again, again.doc) == (False, True, first.doc)
    with pytest.raises(errors.IdempotencyConflict):
        run(ops.start(**start, input_hash="h2"))
    assert run(ops.cancel(first.doc.operation_id)).doc.state == "cancelled"
    assert run(ops.lease(first.doc.operation_id, "w", 60)) is None
