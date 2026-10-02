#!/usr/bin/env python3
"""R32/R83 for AP-04 (API-ARTIFACT): one single-edit defect per decision `tests/ap04` claims,
through the shared runner (`tests/contracts/mutants.py`).

`MUTANTS` run the fake world (`-m "not pg"`, no Docker). `PG_MUTANTS` edit
`PgArtifactStore` and the composition's durable store and are killed on PostgreSQL + MinIO
(`-m pg`, `INFRX_D_TASK=ap4`, a copy that carries the migrations). 0061's own SQL decisions are `tests/d/test_upgrade_0061_mutants.py`.

    INFRX_MUTANTS=all uv run --frozen pytest -q tests/ap04/test_mutants.py
    INFRX_MUTANTS=all INFRX_D_TASK=ap4 uv run --frozen pytest -q tests/ap04/test_mutants.py
    uv run --frozen python tests/ap04/mutants.py --list
"""
from __future__ import annotations

import pathlib
import re
import shutil
import sys

API_DIR = pathlib.Path(__file__).resolve().parents[2]
SUITE_FILE = "tests/ap04/test_artifacts.py"
if str(API_DIR) not in sys.path:        # `python tests/ap04/mutants.py`
    sys.path.insert(0, str(API_DIR))

from tests.contracts import mutants as shared  # noqa: E402
from tests.contracts.mutants import Mutant, Outcome, Result, Runner, _m  # noqa: E402,F401

P = "lab/artifacts/projects.py"
U = "lab/artifacts/uploads.py"
I = "lab/artifacts/imports.py"  # noqa: E741
V = "lab/artifacts/verify.py"
M = "lab/artifacts/manifest.py"
S = "lab/artifacts/store.py"
RA = "gateway/routes/lab_artifacts.py"
CO = "lab/artifacts/compose.py"


def c(name: str) -> str:
    return f"test_ap04__{name}"


NO_CLAIM = c("an_empty_provider_creates_a_project_without_a_serving_claim")
KEY_REUSED = c("a_key_reused_with_another_body_is_an_idempotency_conflict")
SLUG = c("a_slug_is_unique_within_its_provider_only")
DOORS = c("membership_and_role_decide_every_door")
PAGES = c("lists_page_with_opaque_cursors")
ENVELOPE = c("refusals_are_the_r270_envelope")
PARTS = c("reordered_resumed_parts_verify_into_an_artifact")
UNVERIFIED = c("unverified_bytes_never_become_an_artifact")
UNSAFE = c("unsafe_paths_and_code_bearing_files_are_refused_before_any_byte")
EXPIRES = c("an_interrupted_upload_expires_cleanly")
DUPLICATE = c("a_duplicate_completion_returns_the_one_operation")
FENCED = c("a_worker_killed_while_hashing_is_fenced_out")
RETRY = c("a_retry_after_the_artifact_write_keeps_one_artifact")
HEARTBEAT = c("a_verification_longer_than_its_lease_keeps_it_by_heartbeat")
CAS = c("a_session_moves_only_from_the_state_it_was_read_in")
OP_SCOPE = c("an_operation_is_read_only_inside_its_workspace")
IMPORT = c("a_pinned_import_fetches_and_verifies_every_file")
REFS = c("a_mutable_ref_a_prohibited_source_or_a_raw_token_is_refused")
SOURCE = c("a_source_that_refuses_fails_the_import_and_keeps_nothing")
REVISION = c("the_supported_profile_becomes_an_immutable_serving_revision")
REASONS = c("unsupported_architecture_runtime_and_input_format_name_their_reasons")
LABELS = c("two_revisions_of_one_artifact_never_race_for_a_label")
PROFILE = c("the_profile_is_the_measured_marlin_serving_version")
ADOPT = c("an_operator_adopts_existing_bytes_keeping_the_production_identity")
ADOPT_NO = c("adoption_refuses_non_operators_foreign_resources_and_other_bytes")
OPS = c("the_memory_operations_replay_and_cancel_by_the_protocol")
UNWIRED = c("nothing_mounts_while_the_surface_is_unwired")
WORKSPACE = c("a_session_names_its_workspace_and_every_door_checks_it")
SECRETS = c("an_import_resolves_only_the_secret_refs_its_role_names")
BOX = c("the_box_manifest_becomes_the_import_request")
UNIT = c("the_lab_unit_surface_runs_on_its_own_login")
KILLED = c("an_operation_survives_a_killed_worker_process")
RP = "gateway/routes/lab_model_projects.py"

MUTANTS: tuple[Mutant, ...] = (
    # --- 04a: projects, idempotency, doors, pages, the envelope ---------------------------
    _m("project_id_random", "a repeated create with the same key is the same project",
       P, 'project_id=stable_id("model_project", provider, key)',
       'project_id=str(__import__("uuid").uuid4())', NO_CLAIM),
    _m("project_binds_at_create", "creating a project claims no model row or serving",
       P, "        return _same(stored, digest)\n",
       "        return await self.store.bind(_same(stored, digest))\n", NO_CLAIM),
    _m("replay_ignores_body", "same key + another body is idempotency_conflict (R270)",
       P, 'if getattr(stored, "request_hash") != digest:', "if False:", KEY_REUSED),
    _m("fake_slug_not_unique", "a slug is unique within its provider",
       S, '            raise errors.Conflict("model_projects_slug_key")\n',
       "            pass\n", SLUG),
    _m("fake_slug_global", "slug uniqueness is per provider, never global",
       S, '(p.provider_org_id, getattr(p, "slug")) == (row.provider_org_id, row.slug)',
       'getattr(p, "slug") == row.slug', SLUG),
    _m("viewer_creates_projects", "creating a project needs manage_dev_deployment",
       P, 'provider, C.manage_dev_deployment)\n        digest',
       'provider, C.read_aggregate_health)\n        digest', DOORS),
    _m("viewer_uploads", "an intake needs manage_dev_deployment",
       U, "await access.require(actor.user_id, actor.provider_org_id, C.manage_dev_deployment)",
       "await access.require(actor.user_id, actor.provider_org_id, C.read_aggregate_health)",
       DOORS),
    _m("fake_get_unscoped", "a row of another provider is absent",
       S, "row is not None and row.provider_org_id == provider_org_id else None",
       "row is not None else None", DOORS),
    _m("cursor_repeats_a_row", "a page starts strictly after its cursor",
       P, "or (r.created_at.isoformat(), r.key) > (after[0], after[1])]",
       "or (r.created_at.isoformat(), r.key) >= (after[0], after[1])]", PAGES),
    _m("bad_cursor_is_a_500", "a cursor the list did not issue is invalid_cursor",
       P, "except (ValueError, binascii.Error, TypeError):", "except (ZeroDivisionError,):",
       PAGES),
    _m("fastapi_422_not_enveloped", "a refused body is R270's envelope, not FastAPI's",
       RA, "except RequestValidationError as refused:",
       "except ZeroDivisionError as refused:", ENVELOPE),
    _m("refusal_echoes_input", "a refusal names the field and never echoes the input",
       RA, 'message=str(e["msg"])[:300]', 'message=str(e.get("input"))[:300]', ENVELOPE),
    _m("artifact_routes_mount_unwired", "default OFF: no artifact route without the surface",
       RA, "    if a is None:\n        return\n", "    if False:\n        return\n", UNWIRED),
    _m("project_routes_mount_unwired", "default OFF: no project route without the surface",
       RP, "    if a is None:\n        return\n", "    if False:\n        return\n", UNWIRED),
    # --- 04b: uploads -----------------------------------------------------------------------
    _m("unsafe_path_accepted", "no path escapes its prefix or hides a segment",
       M, "    if len(path) > 512 or not all(SEGMENT.match(s) for s in segments):\n",
       "    if len(path) > 512:\n", UNSAFE),
    _m("code_file_accepted", "no code-bearing file is an artifact (remote code never on)",
       M, "    if name not in SAFE_NAMES and not name.endswith(SAFE_SUFFIXES):\n",
       "    if False:\n", UNSAFE),
    _m("duplicate_paths_accepted", "a manifest names each path once",
       M, "        if len(set(paths)) != len(paths):\n", "        if False:\n", UNSAFE),
    _m("missing_file_unnoticed", "a declared file that was never stored fails verification",
       V, "for p in sorted(declared.keys() - found)]", "for p in []]", UNVERIFIED),
    _m("extra_path_unnoticed", "a stored object nobody declared fails verification",
       V, "for p in sorted(found - declared.keys())]", "for p in []]", UNVERIFIED),
    _m("declaration_trusted", "the stored bytes are rehashed, never the declaration trusted",
       V, "        if await objects.digest(prefix + path) != (declared[path].bytes, "
          "declared[path].sha256):\n",
       "        if False:\n", UNVERIFIED),
    _m("failed_bytes_kept", "unverified bytes are deleted",
       U, "        await objects.delete(key)\n    await store.update(",
       "        pass\n    await store.update(", UNVERIFIED, SOURCE),
    _m("undeclared_part_granted", "a PUT URL is issued for a declared path only",
       U, "        if body.relative_path not in Manifest(files=upload.files).by_path():\n",
       "        if False:\n", PARTS),
    _m("session_never_expires", "a session past its expiry takes no bytes and no completion",
       U, 'upload.state == "open" and await self.store.db_now() >= upload.expires_at)',
       'upload.state == "open" and False)', EXPIRES),
    _m("expired_bytes_kept", "an expired session's bytes are swept",
       U, "                    await self.objects.delete(key)\n",
       "                    pass\n", EXPIRES),
    _m("bytes_after_completion", "a completed session takes no more bytes",
       U, '        if upload.state != "open":\n', "        if False:\n", DUPLICATE),
    _m("completion_ignores_manifest", "completion confirms the session's own manifest",
       U, "        if body.manifest_sha256 != upload.manifest_sha256:\n", "        if False:\n",
       DUPLICATE),
    _m("artifact_id_random", "a rerun writes the same artifact, once",
       U, 'artifact_id=stable_id("artifact", row.key)',
       'artifact_id=str(__import__("uuid").uuid4())', RETRY, PARTS),
    _m("fake_update_not_cas", "an intake row moves only from the state it was read in",
       S, 'if stored is None or getattr(stored, "state") != expected_state:',
       "if stored is None:", CAS),
    # --- WR-AP04-2: AP-04's operations over 0060 (`compose.DurableOps`) -------------------
    _m("live_lease_refusal_raised", "a lease another worker holds is skipped, not a crash",
       CO, "        except errors.Conflict:\n            return None\n",
       "        except ZeroDivisionError:\n            return None\n", FENCED),
    _m("stale_advance_untyped", "a superseded worker's phase write is stale_lease",
       CO, "return _op(await self.ops.advance(operation_id, fence, phase))\n"
           "        except errors.Conflict:",
       "return _op(await self.ops.advance(operation_id, fence, phase))\n"
       "        except ZeroDivisionError:", FENCED),
    _m("stale_finish_untyped", "a superseded worker's finish is stale_lease",
       CO, "return _op(await self.ops.finish(operation_id, fence, state, error))\n"
           "        except errors.Conflict:",
       "return _op(await self.ops.finish(operation_id, fence, state, error))\n"
       "        except ZeroDivisionError:", FENCED),
    _m("no_heartbeat", "every phase renews the worker's own lease",
       CO, "            if (operation_id, fence) in self.held:\n", "            if False:\n",
       HEARTBEAT),
    _m("system_read_scoped", "the system reads any operation; the route scopes it",
       CO, "_op(await self.ops.get(operation_id, SYSTEM))",
       '_op(await self.ops.get(operation_id, api.Actor(audience="session", user_id="x")))',
       OP_SCOPE),
    _m("operation_read_across_workspaces", "an operation is read only in its workspace",
       RA, "if op is None or op.actor.provider_org_id != actor.provider_org_id:",
       "if op is None:", OP_SCOPE),
    _m("ops_replay_ignores_body", "same scope+key and another input is 409",
       S, "            if op.input_hash != input_hash:\n", "            if False:\n", OPS),
    _m("ops_cancel_never_terminal", "cancelling a queued operation is terminal at once",
       S, 'state="cancelled" if op.doc.state == "queued"',
       'state="cancel_requested" if op.doc.state == "queued"', OPS),
    # --- WR-AP04-2: the Lab unit's composition and the worker role --------------------------
    _m("workspace_never_named", "a web session's workspace is the request's provider_org_id",
       CO, '"provider_org_id": request.query_params.get("provider_org_id") or None})',
       '"provider_org_id": None})', WORKSPACE),
    _m("query_overrides_a_credential", "a credential that carries its provider keeps it",
       CO, '        if actor.audience != "session" or actor.provider_org_id is not None:\n',
       "        if False:\n", WORKSPACE),
    _m("unwired_actors_crash", "without session actors the families are a 503, not a 500",
       CO, "        if self.actors is None:\n            raise", "        if False:\n            raise",
       WORKSPACE),
    _m("any_secret_ref", "only the references the role lists are resolved",
       CO, "        if ref not in allowed:\n", "        if False:\n", SECRETS),
    _m("ssm_not_decrypted", "an ssm: reference is read decrypted",
       CO, "WithDecryption=True)", "WithDecryption=False)", SECRETS),
    _m("manifest_keeps_hidden_files", "a hidden or code-bearing file is never declared",
       CO, 'for e in doc["files"] if refuse_path(e["path"]) is None]',
       'for e in doc["files"]]', BOX),
    _m("import_at_a_branch", "the import is pinned to the manifest's commit",
       CO, '"commit": doc["model"]["commit"]}}', '"commit": "main"}}', BOX),
    # --- 04c: imports -----------------------------------------------------------------------
    _m("mutable_ref_accepted", "an import is pinned to a 40-hex commit",
       I, "        if not re.fullmatch(COMMIT, commit):\n", "        if False:\n", REFS),
    _m("any_source_host", "only an allowlisted source host is fetched",
       I, "        if host not in SOURCES:\n", "        if False:\n", REFS),
    _m("raw_token_accepted", "a secret reference, never a token, is accepted",
       I, "        if ref is not None and not re.fullmatch(SECRET_REF, ref):\n",
       "        if False:\n", REFS),
    _m("credential_not_sent", "the resolved credential is presented to the source",
       I, 'headers = {"Authorization": f"Bearer {token}"} if token else {}', "headers = {}",
       IMPORT),
    _m("token_stored", "the import row stores the reference, never the credential",
       I, "secret_ref=body.secret_ref,",
       "secret_ref=self.secret(body.secret_ref) if body.secret_ref else None,", IMPORT),
    _m("fetch_at_main", "files are fetched at the pinned commit, never a branch",
       I, "/resolve/{commit}/{quote(path)}", "/resolve/main/{quote(path)}", IMPORT),
    _m("unauthorized_is_unavailable", "a refused credential is named as such",
       I, "if response.status_code in (401, 403):", "if response.status_code in (403,):",
       SOURCE),
    _m("redirect_anywhere", "a source never redirects a fetch off its own hosts",
       I, "        if not self._allowed(request.url.host):\n", "        if False:\n", SOURCE),
    # --- 04d: compatibility and revisions -------------------------------------------------
    _m("any_architecture", "an architecture outside the profile is refused by name",
       V, "or architectures[0] not in ARCHITECTURES):", "or False):", REASONS),
    _m("any_runtime", "the pinned vLLM image is the supported runtime",
       V, "    if request.runtime_image_ref != RUNTIME_IMAGE:\n", "    if False:\n", REASONS),
    _m("any_precision", "bfloat16 is the supported precision",
       V, "    if request.precision != PRECISION:\n", "    if False:\n", REASONS),
    _m("any_hardware", "L40S is the supported hardware",
       V, "    if request.hardware != HARDWARE:\n", "    if False:\n", REASONS),
    _m("any_input_format", "the gateway's chat schemas are the supported input format",
       V, "SUPPORTED_SCHEMAS:\n        reasons.append", "():\n        reasons.append", REASONS),
    _m("artifact_reasons_ignored", "an artifact the profile does not fit makes no revision",
       P, "reasons = [*artifact.compatibility.reasons, *request_reasons(body.profile)]",
       "reasons = [*request_reasons(body.profile)]", REASONS),
    _m("another_projects_artifact", "a revision is built from its own project's artifact",
       P, "if artifact is None or artifact.project_id != project.project_id:",
       "if artifact is None:", REASONS),
    _m("label_by_count", "a revision's label is its own id (no count-based race)",
       P, "        revision_label=link.serving_version_id,\n        # an upload",
       '        revision_label="lab-1",\n        # an upload', LABELS, REVISION),
    _m("revision_id_random", "a repeated revision create is the same serving revision",
       P, 'serving_version_id=stable_id("serving_revision", provider, key)',
       'serving_version_id=str(__import__("uuid").uuid4())', REVISION),
    _m("profile_digest_drift", "the profile is the measured serving version",
       V, 'ENGINE_OPTIONS_DIGEST = "sha256:3c4bbface',
       'ENGINE_OPTIONS_DIGEST = "sha256:3c4bbfacf', PROFILE),
    # --- 04e: adoption ----------------------------------------------------------------------
    _m("anyone_adopts", "only an operator adopts",
       P, "if not actor.operator or actor.user_id is None:", "if actor.user_id is None:",
       ADOPT_NO),
    _m("foreign_adoption", "a serving version is adopted into its own provider only",
       P, "if serving is None or serving.provider_org_id != body.provider_org_id:",
       "if serving is None:", ADOPT_NO),
    _m("unmeasured_shards", "the measured shards must equal the serving pins",
       P, "!= list(serving.weight_shard_digests):",
       "!= list(serving.weight_shard_digests) and False:", ADOPT_NO),
    _m("adoption_new_identity", "adoption keeps the production serving identity",
       P, "            serving_version_id=serving.serving_version_id, project_id=",
       '            serving_version_id=stable_id("x", serving.serving_version_id), '
       "project_id=", ADOPT),
    _m("adoption_not_idempotent", "a repeated adoption is the same artifact",
       P, 'artifact_id=stable_id("adopted_artifact", serving.serving_version_id)',
       'artifact_id=str(__import__("uuid").uuid4())', ADOPT),
)

#: `PgArtifactStore`'s own decisions, killed on PostgreSQL + MinIO by the same cases.
PG_MUTANTS: tuple[Mutant, ...] = (
    _m("pg_get_unscoped", "a row of another provider is absent (PostgreSQL)",
       S, '"and provider_org_id = %s", (key, provider_org_id))',
       '"and %s is not null", (key, provider_org_id))', DOORS),
    _m("pg_update_not_cas", "an intake row moves only from its read state (PostgreSQL)",
       S, "where {kind.KEY} = %s and state = %s returning",
       "where {kind.KEY} = %s and %s is not null returning", CAS),
    _m("pg_insert_not_idempotent", "inserting a stored row again returns it (PostgreSQL)",
       S, "on conflict ({kind.KEY}) do nothing ", "", NO_CLAIM),
    _m("pg_slug_conflict_untyped", "a taken slug is a 409 conflict (PostgreSQL)",
       S, "    if isinstance(failed, pg.UniqueViolation):\n",
       "    if isinstance(failed, ZeroDivisionError):\n", SLUG),
    _m("surface_ops_in_memory", "the composed operations are 0060's durable store",
       CO, "DurableOps(control_ops.PgControlOps(connect))",
       "DurableOps(control_ops.FakeControlOps())", UNIT, KILLED),
)
FAKE_ONLY = (OPS, PROFILE, UNWIRED, WORKSPACE, SECRETS, BOX)


def case_names() -> set[str]:
    return set(re.findall(r"^def (test_\w+)\(", (API_DIR / SUITE_FILE).read_text(), re.M))


def _layout(root: pathlib.Path) -> pathlib.Path:
    """The default copy one level down as `apps/infrx-api`, beside copies of what the suite
    reads from the repository: the migrations (`infrx.state.migrations`), the measured Marlin
    files, `models/marlin2b/serving-version.json` (the fixture of truth) and
    `infra/runbooks/artifacts.py` (the box manifest the real import is built from)."""
    api = root / "apps" / "infrx-api"
    api.mkdir(parents=True)
    shared._copy(api, _PLAIN)
    repo = API_DIR.parents[1]
    for part in (pathlib.Path("apps", "app", "supabase", "migrations"),
                 pathlib.Path("research", "models", "marlin2b")):
        shutil.copytree(repo / part, root / part)
    (root / "models" / "marlin2b").mkdir(parents=True)
    shutil.copy(repo / "models" / "marlin2b" / "serving-version.json",
                root / "models" / "marlin2b" / "serving-version.json")
    (root / "infra" / "runbooks").mkdir(parents=True)     # the box manifest tool (WR-AP04-2)
    shutil.copy(repo / "infra" / "runbooks" / "artifacts.py",
                root / "infra" / "runbooks" / "artifacts.py")
    return api


_PLAIN = Runner(name="ap04-plain")
RUNNER = Runner(name="ap04", targets=(SUITE_FILE,), extra_args=("-m", "not pg"), layout=_layout)
PG_RUNNER = Runner(name="ap04-pg", targets=(SUITE_FILE,), extra_args=("-m", "pg"),
                   env=("INFRX_D_TASK",), layout=_layout)


def run_mutant(mutant) -> Result:
    """The PostgreSQL list's cases run unmutated first, once per process (R83 (b))."""
    if mutant not in PG_MUTANTS:
        return shared.run_mutant(mutant, RUNNER)
    cases = tuple(sorted({case for m in PG_MUTANTS for case in m.cases}))
    return shared.pristine(cases, PG_RUNNER) or shared.run_mutant(mutant, PG_RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run AP-04's mutation list"))
