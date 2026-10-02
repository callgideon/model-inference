"""Layer 1: the guards that make the harness safe to run, and the test-id table.

Nothing here needs docker. These are the properties that a reviewer would otherwise have
to take on trust: that every image is pinned by digest, that every port is inside E's
range, that a destructive helper cannot reach outside the namespace, that the movable
database clock cannot exist in production, and that the legacy-to-namespaced test-id table
matches the documents it claims to map.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import harness                                          # noqa: E402
import pgstate                                          # noqa: E402
import testids                                          # noqa: E402

harness.api_on_path()

from infrx.contracts import tasklocal                   # noqa: E402

COMPOSE_TEXT = harness.COMPOSE_FILE.read_text()
OWNED_FILES = sorted(p for p in harness.HERE.rglob("*")
                     if p.is_file() and "__pycache__" not in p.parts)


# ------------------------------------------------------------------ ports and names

def _rendered(namespace: str) -> str:
    """compose.yaml as compose sees it for `namespace`: every `${VAR}` / `${VAR:?why}` replaced
    from `harness.compose_env(namespace)`, so a literal left behind in one derived place shows
    up as the wrong value in the other namespace."""
    env = harness.compose_env(namespace)
    return re.sub(r"\$\{(\w+)(?::\?[^}]*)?\}", lambda match: env[match.group(1)], COMPOSE_TEXT)


@pytest.mark.parametrize("namespace", sorted(harness.NAMESPACES))
def test_every_port_is_inside_the_range_tasklocal_grants_this_task(namespace):
    """08 §8 / R48: each namespace's compose range is its tasklocal block and nothing else (E2:
    55500-55599). A port outside it is a collision with another session's worktree waiting to
    happen."""
    granted = tasklocal.local_services(namespace)["compose"]
    allowed = {granted.host_port, *granted.extra_ports}
    assert allowed == set(harness.range_for(namespace)), "the harness copy drifted from tasklocal"
    ports = harness.ports_for(namespace)
    for service, port in ports.items():
        assert port in allowed, f"{service} on {port} is outside {namespace}'s range"
    assert len(set(ports.values())) == len(ports), "two services, one port"


@pytest.mark.parametrize("namespace", sorted(harness.NAMESPACES))
def test_a_namespace_block_overlaps_no_other_reservation(namespace):
    """Whatever tasklocal can express today: no port another task or track reserved lies inside
    a namespace's block, and every port the namespace publishes lies inside it."""
    owner = "e" if namespace == "e2" else namespace
    block = harness.range_for(namespace)
    intruders = {port: who for port, who in tasklocal.all_host_ports().items()
                 if port in block and who.split("/")[0] != owner}
    assert intruders == {}, intruders
    assert set(harness.ports_for(namespace).values()) <= set(block)
    assert (harness.PORTS, harness.PORT_RANGE) == (harness.ports_for(harness.NAMESPACE),
                                                   harness.range_for(harness.NAMESPACE))


@pytest.mark.parametrize("namespace", sorted(harness.NAMESPACES))
def test_the_compose_file_publishes_exactly_those_ports_on_loopback(namespace):
    text = _rendered(namespace)
    published = re.findall(r'"127\.0\.0\.1:(\d+):(\d+)"', text)
    assert published, "no published ports found; the regex or the file changed"
    host_ports = sorted(int(host) for host, _ in published)
    assert host_ports == sorted(port for name, port in harness.ports_for(namespace).items()
                                if name != "fake_vllm"), host_ports
    assert re.search(r"ports:\s*\n\s*-\s*\"\d+:", text) is None, \
        "every published port must be bound to 127.0.0.1, never to every interface"


def test_every_image_is_pinned_by_digest():
    """A tag moves. "Pinned versions" that move are not evidence (04 layer 2)."""
    images = harness.compose_images()
    assert set(images) == set(harness.SERVICES), images
    for service, reference in images.items():
        assert "@sha256:" in reference, f"{service} is not pinned by digest: {reference}"
        digest = reference.split("@sha256:", 1)[1]
        assert re.fullmatch(r"[0-9a-f]{64}", digest), f"{service}: {digest!r} is not a sha256"
        assert ":" not in reference.split("@", 1)[0], f"{service} carries a tag as well"


@pytest.mark.parametrize("namespace", sorted(harness.NAMESPACES))
def test_every_container_is_named_in_the_namespace_and_volumes_are_project_scoped(namespace):
    text = _rendered(namespace)
    names = re.findall(r"container_name:\s*(\S+)", text)
    assert len(names) == len(harness.SERVICES)
    assert all(name.startswith(f"infrx-{namespace}-") for name in names), names
    assert re.search(rf"^name:\s*infrx-{namespace}\s*$", text, re.MULTILINE), \
        f"the compose project must be infrx-{namespace}, which is what scopes the teardown"
    binds = [line for line in text.splitlines()
             if re.match(r"\s+- (\.|/|\$)", line) and ":" in line]
    assert binds == [], f"no host bind mount: a disposable volume cannot be a host path: {binds}"


def test_every_container_helper_is_namespace_checked():
    """r1 B2 (H5): `pause` was the one fault helper with no test behind its `assert_ours`, so
    removing the check survived. Each helper is driven with a stubbed `run`/`compose`, which
    must never be reached, because `assert_ours` refuses first."""
    reached = []
    original_run, original_compose = harness.run, harness.compose
    harness.run = lambda argv, **kw: reached.append(argv)
    harness.compose = lambda *a, **kw: reached.append(a)
    original_labels = harness._labels
    harness._labels = lambda kind, name: {}          # nothing carries our labels
    try:
        for helper in (harness.pause_container, harness.disconnect_container,
                       harness.signal_container):
            with pytest.raises(harness.HarnessError, match="not created by project"):
                helper("valkey")
    finally:
        harness.run, harness.compose, harness._labels = original_run, original_compose, original_labels
    assert reached == [], f"a helper reached docker before its namespace check: {reached}"


def test_a_destructive_helper_refuses_anything_outside_the_namespace():
    """Other sessions run containers on this host. The prefix check happens before docker
    is consulted, so this holds with no daemon at all."""
    for foreign in ("postgres", "gideon-migration-order-test-caae059890", "infrx-d1-postgres"):
        with pytest.raises(harness.HarnessError, match="namespace"):
            harness.assert_ours(foreign)
    with pytest.raises(harness.HarnessError, match="unknown service"):
        harness.container_of("not-a-service")
    assert harness.container_of("postgres") == f"infrx-{harness.NAMESPACE}-postgres"


# Assembled from parts so this guard does not trip over its own needles, and so it still
# scans itself: a leak added to this very file would be caught.
PRODUCTION_NEEDLES = ("supabase" ".co", "amazonaws" ".com", "callbill" ".ai", "llm-" "bootcamp",
                      "SUPABASE_SERVICE" "_ROLE_KEY", "AWS_SECRET" "_ACCESS_KEY",
                      "GATEWAY" "_API_KEY")
#: (file relative to this directory, needle) pairs that are legitimate - one needle in one
#: file each, so the same file naming any other needle (or a new file naming this one) is
#: still caught (WR-LL2-4).
PRODUCTION_ALLOWED = {
    # certify's public-edge external check targets the real edge host by name (a URL
    # string asserted on, never called from this suite).
    ("backend/test_certify.py", "callbill" ".ai"),
    # the create-test-user operator script reads the service-role key by this env NAME;
    # the test feeds it a fake value and checks the refusals - no secret is present.
    ("ops/test_create_test_user.py", "SUPABASE_SERVICE" "_ROLE_KEY"),
}


def production_offenders(root: Path, files) -> set[tuple[str, str]]:
    return {(path.relative_to(root).as_posix(), needle) for path in files
            for needle in PRODUCTION_NEEDLES
            if needle in path.read_text(errors="ignore")} - PRODUCTION_ALLOWED


def test_nothing_in_this_directory_points_at_production():
    """No production credential is needed, and none may be reachable by accident."""
    assert production_offenders(harness.HERE, OWNED_FILES) == set()


def test_the_production_allow_list_excuses_only_its_own_file_and_needle(tmp_path):
    """A new file naming a production host or the service-role secret is caught, and an
    allow-listed file naming any other needle is too."""
    (tmp_path / "backend").mkdir()
    (tmp_path / "ops").mkdir()
    (tmp_path / "new_host.py").write_text("URL = 'https://x." + "callbill" ".ai'\n")
    (tmp_path / "new_key.py").write_text("NAME = '" + "SUPABASE_SERVICE" "_ROLE_KEY'\n")
    (tmp_path / "backend" / "test_certify.py").write_text("callbill" ".ai " + "supabase" ".co")
    (tmp_path / "ops" / "test_create_test_user.py").write_text("SUPABASE_SERVICE" "_ROLE_KEY")
    assert production_offenders(tmp_path, sorted(tmp_path.rglob("*.py"))) == {
        ("new_host.py", "callbill" ".ai"), ("new_key.py", "SUPABASE_SERVICE" "_ROLE_KEY"),
        ("backend/test_certify.py", "supabase" ".co")}


# ------------------------------------------------------------------ the test-only clock

def test_the_movable_clock_cannot_exist_in_a_deployed_database():
    """08 §10 "D1: four things", item 3: a clock a deployed process can move is a way to
    release an unknown-usage hold early.

    E2R item 2 swapped E2's private clock for D1's shared `infrx_test` one, so the old check
    ("the migrations never mention it") is no longer available: `infrx.now()` READS
    `infrx_test.clock` and says so in 0003. The two barriers that actually hold are checked
    instead - nothing in the migrations INSTALLS the offset, and the only installer is a
    fixture outside the migrations directory - plus the second, independent gate: the offset
    is consulted only in a task-local `infrx_<task>` database, which production's is not.
    """
    installs = (f"create schema if not exists {pgstate.CLOCK_SCHEMA}",
                f"create schema {pgstate.CLOCK_SCHEMA}",
                f"create table if not exists {pgstate.CLOCK_SCHEMA}.clock",
                f"function {pgstate.CLOCK_SCHEMA}.advance",
                f"function {pgstate.CLOCK_SCHEMA}.set_offset")
    for path in pgstate.migration_files():
        lowered = path.read_text().lower()
        for needle in installs:
            assert needle not in lowered, f"{path.name} installs the test clock: {needle!r}"

    fixture = pgstate.CLOCK_FIXTURE
    assert fixture.is_file(), f"the clock fixture is missing: {fixture}"
    assert fixture.parent != harness.MIGRATIONS_DIR, \
        "the only installer of a movable clock must not be a migration"
    body = fixture.read_text().lower()
    assert f"create schema if not exists {pgstate.CLOCK_SCHEMA}" in body
    assert f"create table if not exists {pgstate.CLOCK_SCHEMA}.clock" in body

    # The second barrier, in the migration that defines the clock: a database name gate.
    gate = "current_database() like 'infrx@_%' escape '@'"
    assert any(gate in path.read_text() for path in pgstate.migration_files()), \
        f"{pgstate.CLOCK_FUNCTION} must gate the offset on a task-local database name"
    assert harness.PG_DATABASE.startswith("infrx_"), "our database must pass that gate"
    assert not harness.PG_TEMPLATE_SOURCE.startswith("infrx_"), \
        "and production's database name must not: that is what makes the gate work"


def test_the_migration_set_is_the_console_one_and_is_read_in_filename_order():
    files = pgstate.migration_files()
    assert [path.name for path in files] == [
        "0001_init.sql", "0002_seed_models.sql", "0003_pilot_durable_schema.sql",
        "0004_pilot_roles_and_rpcs.sql", "0005_console_read_surface.sql",
        # D1R (additive CREDIT accounting, provider registry, read surface, operator seams)
        "0006_credit_accounting.sql", "0007_provider_registry.sql",
        "0008_credit_read_surface.sql", "0009_operator_seams.sql",
        # D2 (media objects, admission, dispatch outbox, outbox gc, job results)
        "0010_media_uploads.sql", "0011_admission.sql", "0012_dispatch_outbox.sql",
        "0013_outbox_gc.sql", "0014_job_results.sql",
        # A1 (verified individual signup eligibility, retention, retirement)
        "0015_signup_eligibility.sql",
        # D3 (fenced leases, cancellation, reaper)
        "0016_fenced_leases.sql",
        "0017_stream_journal.sql",
        # D5 (terminal settlement, cancel cause, grants, reconcile, the R91 lookup)
        "0018_terminal_settlement.sql",
        # D10 (upload readiness, content lifecycle, read authority + the dedicated logins)
        "0019_upload_readiness.sql", "0020_content_lifecycle.sql", "0021_read_authority.sql",
        # D10 follow-up (fail_preparation, set_feature_flag, WR-7 refetch, F2 guard revoke)
        "0022_preparation_refusal_and_flag_writer.sql",
        # D10 door revoke (the runtime login loses admit/claim_preparation, R123)
        "0023_runtime_unmarked_door_revoke.sql",
        # D10-APP-SQL: the console read port
        "0024_console_read_port.sql",
        # D10-0025: the operator console's audited RPCs and reads (U3 WR-U3-1, R143)
        "0025_operator_console.sql",
        # D10-0026: the worker's result write fenced like append (R147)
        "0026_fenced_result.sql",
    # Lab (local-only, R151): L2-SQL, D6F
    "0027_lab_access.sql", "0028_feedback_durable.sql", "0029_lab_data.sql",
    # Lab (local-only, R151): LW1 integration (R156 session door)
    "0030_lab_access_self.sql",
    # Lab (local-only, R151): lab-sql LW2 (D6J, L3-SQL, D9, D7/D6F follow-ups, the LW2 schema requests)
    "0031_lab_consent.sql", "0032_lab_control.sql", "0033_lab_rollout.sql",
    "0034_lab_eval_followup.sql", "0035_feedback_scrub.sql", "0036_lab_judge_ledger.sql",
    "0037_lab_judge_doors.sql", "0038_feedback_doors.sql", "0039_lab_release.sql",
    "0040_lab_variant_comparisons.sql",
    # Lab (local-only, R151): lab-sql LW3 (C2-RPC, D8, the LW3 requests)
    "0041_lab_content_refs.sql", "0042_lab_d8_ledgers.sql", "0043_lab_reads_and_proposals.sql",
    # Lab (local-only, R151): lab-sql LW4 (WR-LSQ-9)
    "0044_lab_control_reads.sql",
    # Lab (local-only, R151/R201): lab-sql LW5 (E8L-F1, WR-E8L-2)
    "0045_lab_serving_ref_identity.sql",
    # Lab (local-only, R151/R201): lab-sql LW5 (WR-E8L-4)
    "0046_lab_shadow_limit_operator.sql",
    # Lab (local-only, R151/R201): lab-sql LW6 (E3L-F2/R205)
    "0047_lab_control_propose_idempotent.sql",
    # Lab (local-only, R151/R201): lab-sql LW6 (WR-R4-1's lab-sql half)
    "0048_lab_release_listing.sql",
    # Lab (local-only, R151/R201): lab-sql LW6 (WR-LSQ-C2A)
    "0049_lab_judge_runs_in.sql",
    # Lab (local-only, R151/R201): lab-sql LW6 (WR-LSQ-C2B)
    "0050_lab_outbox_pending_kinds.sql",
    # Lab (local-only, R151/R201): lab-sql LW6 (WR-N4-3)
    "0051_lab_import_jobs.sql",
    # Lab (local-only, R151/R201): lab-control-2 (E3L-F4, the operator's rejection)
    "0052_lab_control_reject.sql",
    # Lab (local-only, R151/R201): composition-6 (WR-R4-2, WR-C5-RECEIPT, WR-C5-PROVIDERS)
    "0053_lab_composition_reads.sql",
    # Lab (local-only, R151/R201): lab-live (WR-C6-LIVE, R244)
    "0054_lab_release_live.sql",
    "0055_lab_variants_requeue.sql",
    "0056_lab_control_grants.sql",
    "0057_trace_consent_read.sql",
    "0058_lab_variant_identities.sql",
    "0059_lab_control_grants_2.sql",
    # Wave 7 (LOCAL-ONLY, R271): api-schema 0060, api-artifacts 0061 (merge #89), api-judge 0064
    "0060_control_operations.sql",
    "0061_model_projects_artifacts.sql",
    "0064_judge_api.sql",
    ]
    assert files[0].parent == harness.MIGRATIONS_DIR
    digests = pgstate.migration_digests()
    assert [name for name, _ in digests] == [path.name for path in files]
    assert all(re.fullmatch(r"[0-9a-f]{64}", digest) for _, digest in digests)


def test_the_role_matrix_covers_every_role_and_every_expectation_kind():
    """A matrix that only ever asserts "zero rows" cannot tell a working policy from a
    missing grant, so every expectation kind must be represented, and both directions of
    each rule (denied for a stranger, allowed for the owner)."""
    fixtures = pgstate.Fixtures(seed=1)
    fixtures.orgs.update({"alpha": _fake_uuid(1), "beta": _fake_uuid(2)})
    fixtures.keys.update({"alpha": _fake_uuid(3), "beta": _fake_uuid(4)})
    for index, handle in enumerate(("owner_alpha", "member_alpha", "owner_beta", "operator")):
        fixtures.principals[handle] = pgstate.Principal(handle, _fake_uuid(10 + index),
                                                        f"{handle}@x.invalid")
    checks = pgstate.role_matrix(fixtures)
    assert {check.role for check in checks} == {"anon", "authenticated", "service_role",
                                                "postgres"}
    assert {check.expect[0] for check in checks} == {"value", "rowcount", "error", "rows"}
    assert len({check.case for check in checks}) == len(checks), "duplicate case id"
    assert all(check.why for check in checks), "every case states the invariant it pins"
    assert sum(1 for check in checks if check.expect == ("error", pgstate.PERMISSION_DENIED)) >= 8
    assert sum(1 for check in checks if check.expect[0] == "rowcount"
               and check.expect[1] == 1) >= 2, "an allowed write must be proved too"
    # r1 review: every 42501 case must say WHICH 42501 it means - a missing grant, an RLS
    # policy refusal and an RPC's own guard are three different facts behind one SQLSTATE.
    denials = [check for check in checks if check.expect == ("error", pgstate.PERMISSION_DENIED)]
    unqualified = [check.case for check in denials if not check.message_contains]
    assert unqualified == [], f"these 42501 cases do not distinguish the cause: {unqualified}"
    # E2R item 2: with the merged 0004 there is a FOURTH cause - a revoked function EXECUTE -
    # and the four `anon` cases name the relation they were refused, so a grant reappearing on
    # one table cannot hide behind a generic fragment.
    causes = {check.message_contains for check in denials}
    assert {"permission denied for table", "violates row-level security policy",
            "not a member of organization",
            "permission denied for function org_balance"} <= causes, causes
    assert {check.message_contains for check in denials if check.case.startswith("E2-")
            and check.message_contains.startswith("permission denied for table ")} == {
        "permission denied for table organizations", "permission denied for table api_keys",
        "permission denied for table credit_ledger",
        "permission denied for table models"}, causes
    # E3B phase 2 item 7: every relation and SECURITY DEFINER function has a row per API role
    # (`test_services.py` holds the list itself to the migrated catalog); D4's 0017 adds the
    # invoker `chunk_doc`, the watermark columns per role, their CHECK and the trigger.
    generated = {check.case for check in checks if check.case.startswith("E3B-RLS-")}
    assert generated == {f"E3B-RLS-{name}-{role}" for name in (
        *pgstate.RELATIONS, *pgstate.FUNCTIONS, *pgstate.INVOKER_FUNCTIONS,
        "0017-watermark-columns", "0018-settlement-columns") for role in pgstate.API_ROLES} | {
        f"E3B-RLS-W-{name}-{role}" for name in pgstate.RELATIONS for role in pgstate.API_ROLES
    } | {"E3B-RLS-0017-watermark-check", "E3B-RLS-0017-terminal-trigger",
         "E3B-RLS-0018-settled-usage-check", "E3B-RLS-0018-settlement-guard"}
    # Every statement must be renderable: an unbound placeholder is a case that never runs.
    for check in checks:
        statement, _ = pgstate._sql(fixtures, check.sql)
        assert "{" not in statement, check.case


def _fake_uuid(n: int):
    import uuid
    return uuid.UUID(int=n, version=4)


# ------------------------------------------------------------------ test-id mapping

def test_every_legacy_test_id_occurs_in_the_document_it_is_attributed_to():
    """The table's only value is that it is true. A mapping nobody checks rots into a
    mapping that is wrong, and then a report cites `Q3` again."""
    text = {key: (harness.REPO_ROOT / path).read_text()
            for key, path in testids.REPO_DOCS.items()}
    missing = []
    for series in testids.SERIES:
        for legacy in series.legacy_ids():
            if not re.search(rf"\b{re.escape(legacy)}\b", text[series.source]):
                missing.append(f"{legacy} claimed in {testids.REPO_DOCS[series.source]}")
    assert missing == [], missing


def test_namespacing_actually_resolves_the_collisions_it_exists_for():
    """Two kinds of collision, both measured: research-document reuse, and a legacy id
    spelled exactly like one of this plan's task ids."""
    collisions = testids.collisions()
    for legacy in ("Q1", "F1"):
        assert legacy in collisions, f"{legacy} is reused across documents and must be listed"
        assert len(collisions[legacy]) >= 2, collisions[legacy]
    assert testids.resolve("SERV-Q1").source == "serv"
    assert testids.resolve("JUDGE-Q1").source == "judge"
    assert testids.resolve("CONSOLE-E1").scope.endswith("(NOT plan task E1)"), \
        "E1 is both a console checklist and a task in this plan; the table must say so"

    clashes = testids.task_id_clashes(harness.REPO_ROOT)
    tasks = set(testids.plan_task_ids(harness.REPO_ROOT))
    # `D1`-`D4` in production-api §1 are design *decisions*, not test cases, so they are
    # deliberately absent from the table and absent here.
    assert {"E1", "Q1", "F1", "I1", "M1", "T1", "J1", "C1", "U1", "G1"} <= set(clashes), \
        f"these legacy ids are also task ids and must be listed: {sorted(clashes)}"
    assert "D1" not in clashes, "D1-D4 are decisions in production-api §1, not test ids"
    assert len(clashes) >= 25, f"only {len(clashes)} clashes found"
    assert all(legacy in tasks for legacy in clashes)
    assert "E1" not in collisions, \
        "E1 is unique among the research documents; its clash is with the task id"


def test_no_namespaced_id_is_issued_twice_and_all_four_namespaces_are_used():
    ids = testids.namespaced_ids()
    assert len(ids) == len(set(ids)), "a namespaced id was issued twice"
    assert {name.split("-", 1)[0] for name in ids} == {"SERV", "TRACE", "JUDGE", "CONSOLE"}
    assert len(ids) >= 120, f"the research set is larger than this: {len(ids)}"
    table = testids.markdown_table()
    assert table.count("|") > 100 and "Ambiguous legacy id" in table
    with pytest.raises(KeyError):
        testids.resolve("SERV-NOPE9")


# ------------------------------------------------------------------ canary

def test_canary_intentional_failure_is_detected_in_the_guard_suite():
    """The second half of the canary: a failure in *this* file must also be seen, so the
    proof is not limited to one test module."""
    if os.environ.get("INFRX_E2_CANARY") == "fail":
        raise AssertionError("E2 canary: this failure is intentional (INFRX_E2_CANARY=fail)")
    assert os.environ.get("INFRX_E2_CANARY") in (None, "", "off")
