"""KNOWN-GOOD-PROOF: a rollback target on a schema newer than its tree.

Failure oracles: a schema_proof counts only as far as its `through` - a target proven through
0022 is KNOWN-GOOD at --applied 0022 and NOT at 0023; the record carries a proof for both
known-good targets (none before this lane: the rehearsal at e607b705 found no target once the
window applies 0019+), bound to the sha256 of the migration bytes it ran on (a revised 0022/0023
in this checkout is not proven); the proof driver refuses a short or unknown target, counts a
suite that skipped or passed nothing as FAIL (pytest exits 0 on skips), and its migrated-DSN
check refuses a history that differs from the candidate's files by a version or by a byte, or a
CLI-split history that omits, reorders or fragments the file's statements. KNOWN-GOOD-REPROOF
(R151 condition 1): both proofs reach 0051 and bind this checkout's 0027-0051 bytes;
KNOWN-GOOD-REPROOF-2 (the next window's condition 1): they reach 0052, and the through-0051
proof is kept word for word in `superseded` (R224); KNOWN-GOOD-REPROOF-3 (the next window's
condition 1 again): they reach 0056, and the through-0052 proof is kept word for word in front;
KNOWN-GOOD-REPROOF-4 (the second hosted window's condition 1): they reach 0059, and the
through-0056 proof is kept word for word in front.
"""
from __future__ import annotations

import hashlib
import json
import re
import runpy
import sys

import pytest

from . import support
from .test_rollback_drill import KNOWN_GOOD, MIG, PREFLIGHT, PREP, MAIN, _commit, _git

PROOF = runpy.run_path(str(support.REPO / "infra" / "runbooks" / "schema_proof.py"))
RECORD = support.REPO / "infra" / "rollout" / "known-good.json"


def test_ops_recover__a_schema_proof_reaches_exactly_its_through(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "trunk")
    old = _commit(repo, {MAIN: "PreparationRunner(jobs)\n", PREP: "class PreparationRunner: ...\n",
                         f"{MIG}/0018_x.sql": "", PREFLIGHT: "TUNABLE = (\n)\n", "ev/proof.md": "x"})
    entry = {"sha": old, "known_good": True, "evidence": ["ev/proof.md"]}
    judge = KNOWN_GOOD["judge"]

    def verdict(applied, proof=None):
        registry = {"releases": [{**entry, **({"schema_proof": proof} if proof else {})}]}
        return judge(old, applied, [], None, registry, repo)["verdict"]

    mig = repo / MIG                                     # this checkout: the window's 0019-0023
    for n in range(19, 24):
        (mig / f"{n:04d}_m.sql").write_text(f"select {n};\n")
    files = {f"{n:04d}": hashlib.sha256(f"select {n};\n".encode()).hexdigest() for n in range(19, 24)}

    assert verdict("0022") == "NOT-KNOWN-GOOD"                       # fails before: no proof
    proof = {"through": "0022", "files": files, "evidence": ["ev/proof.md"]}
    assert verdict("0018", proof) == "KNOWN-GOOD"
    assert verdict("0022", proof) == "KNOWN-GOOD"
    assert verdict("0023", proof) == "NOT-KNOWN-GOOD"                # 0023 is not proven
    assert verdict("0023", {**proof, "through": "0023"}) == "KNOWN-GOOD"
    # the proof is about the bytes it ran on: a revised 0023 here, or one it never hashed, is not
    (mig / "0023_m.sql").write_text("select 0;\n")
    assert verdict("0023", {**proof, "through": "0023"}) == "NOT-KNOWN-GOOD"
    assert verdict("0022", proof) == "KNOWN-GOOD"                    # 0019-0022 are unchanged
    (mig / "0023_m.sql").write_text("select 23;\n")
    del files["0023"]
    assert verdict("0023", {**proof, "through": "0023"}) == "NOT-KNOWN-GOOD"


def test_ops_recover__the_record_proves_both_targets_on_the_candidate_schema():
    record = json.loads(RECORD.read_text())
    tree = {p.name[:4]: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (support.REPO / MIG).glob("[0-9][0-9][0-9][0-9]_*.sql")}
    proven = {r["sha"][:7]: r["schema_proof"] for r in record["releases"]
              if r.get("known_good") and r.get("schema_proof")}
    assert set(proven) == {"4226315", "bda1586"}
    for proof in proven.values():
        assert proof["through"] >= "0026" and proof["through"] in tree       # KNOWN-GOOD-PROOF-3
        assert any("KNOWN-GOOD-PROOF-" in p for p in proof["evidence"])
        # the bytes it ran on, beyond both targets' 0001-0018: a revised 0022/0023 fails here
        assert proof["files"] == {v: h for v, h in tree.items() if "0018" < v <= proof["through"]}
        # the committed driver deselects the SHAPE cases the record counts (0-KGP2-RV-1: it named 10)
        assert f"{len(PROOF['SHAPE'])} SHAPE cases" in proof["result"], len(PROOF["SHAPE"])


def test_ops_recover__the_record_proves_both_targets_through_the_lab_migrations_0059():
    """KNOWN-GOOD-REPROOF (R151/R201 condition 1, the 2026-09-29 window: hosted 0026 -> 0051),
    KNOWN-GOOD-REPROOF-2 (0051 -> 0052, lab-control-2's reject), KNOWN-GOOD-REPROOF-3 (the first
    Lab window: 0051 -> 0056, 0053-0056 the Lab's composition reads, release live, variants/requeue
    and control grants), KNOWN-GOOD-REPROOF-4 (the second window: 0056 -> 0059, 0057 the trace
    consent read, 0058 the variant identities, 0059 the control grants 2).
    Failure oracle: a record whose proofs stop short of 0059, or whose 0027-0059 hashes are not
    this checkout's bytes (a migration revised after the proof ran), leaves the window with
    no rollback target; the re-proof's evidence is named first; the through-0056 proof it
    replaces is kept, not dropped, in front of the through-0052 and through-0051 ones (R224)."""
    record = json.loads(RECORD.read_text())
    lab = {p.name[:4]: hashlib.sha256(p.read_bytes()).hexdigest()
           for p in (support.REPO / MIG).glob("[0-9][0-9][0-9][0-9]_*.sql") if "0026" < p.name[:4] <= "0059"}
    assert sorted(lab) == [f"{n:04d}" for n in range(27, 60)]
    proven = {r["sha"][:7]: r["schema_proof"] for r in record["releases"]
              if r.get("known_good") and r.get("schema_proof")}
    assert set(proven) == {"4226315", "bda1586"}
    for sha, proof in proven.items():
        assert proof["through"] >= "0059", sha
        assert {v: proof["files"].get(v) for v in lab} == lab, sha
        assert "KNOWN-GOOD-REPROOF-4-" in proof["evidence"][0], sha
        assert [s["through"] for s in proof["superseded"]] == ["0056", "0052", "0051", "0026"], sha
        assert "KNOWN-GOOD-REPROOF-3-8f0e3c9" in proof["superseded"][0]["evidence"][0], sha
        assert "KNOWN-GOOD-REPROOF-2-68ba65f" in proof["superseded"][1]["evidence"][0], sha
        assert "KNOWN-GOOD-REPROOF-fca3ea3" in proof["superseded"][2]["evidence"][0], sha


# WR-KGR2-RV2 (R224, word for word): the through-0051 proof as the record held it before the
# re-proof, copied from the git blob infra/rollout/known-good.json at ac8bc06d (merge #40).
# A literal, not `git show`, because the mutation copies are not git checkouts.
THROUGH_0051 = {sha: {
    "through": "0051",
    "result": f"{sha}: its own tests/d (26 suites, 383 passed, 14 SHAPE cases skipped by name, "
              "5 xfailed) + the result-read probe PASS on 0001-0051, on plain PostgreSQL + shim AND on "
              "the Supabase image; migrate.py history check PASS on both (the committed driver at "
              "fca3ea3, candidate 72dc76ad)",
    "candidate": "72dc76ad (0001-0026 as proven at 0026 by KNOWN-GOOD-PROOF-3, 0027-0051 the Lab "
                 "migrations the R151/R201 window of 2026-09-29 applies); supersedes the through-0026 "
                 "proof, rerun whole",
    "evidence": ["research/plan/evidence/i/KNOWN-GOOD-REPROOF-fca3ea3.md",
                 "research/plan/evidence/i/KNOWN-GOOD-PROOF-3-af552ed.md",
                 "research/plan/evidence/i/KNOWN-GOOD-PROOF-2-3f7df77.md",
                 "research/plan/evidence/i/KNOWN-GOOD-PROOF-aab4b41.md"],
} for sha in ("4226315", "bda1586")}


# KNOWN-GOOD-REPROOF-3 (R224, word for word): the through-0052 proof as the record held it
# before this re-proof, copied from the git blob infra/rollout/known-good.json at 33547abd.
THROUGH_0052 = {sha: {
    "through": "0052",
    "result": f"{sha}: its own tests/d (26 suites, 383 passed, 14 SHAPE cases skipped by name, "
              "5 xfailed) + the result-read probe PASS on 0001-0052, on plain PostgreSQL + shim AND on "
              "the Supabase image; migrate.py history check PASS on both (the committed driver at "
              "fca3ea3, unchanged; candidate e9e32e0e)",
    "candidate": "e9e32e0e (0001-0051 as proven at 0051 by KNOWN-GOOD-REPROOF, 0052 lab-control-2's "
                 "reject, LOCAL-ONLY until the next R151 window); supersedes the through-0051 proof, "
                 "rerun whole",
    "evidence": ["research/plan/evidence/i/KNOWN-GOOD-REPROOF-2-68ba65f.md",
                 "research/plan/evidence/i/KNOWN-GOOD-REPROOF-fca3ea3.md",
                 "research/plan/evidence/i/KNOWN-GOOD-PROOF-3-af552ed.md",
                 "research/plan/evidence/i/KNOWN-GOOD-PROOF-2-3f7df77.md",
                 "research/plan/evidence/i/KNOWN-GOOD-PROOF-aab4b41.md"],
} for sha in ("4226315", "bda1586")}


# KNOWN-GOOD-REPROOF-4 (R224, word for word): the through-0056 proof as the record held it
# before this re-proof, copied from the git blob infra/rollout/known-good.json at 6ca7879f.
THROUGH_0056 = {sha: {
    "through": "0056",
    "result": f"{sha}: its own tests/d (26 suites, 383 passed, 14 SHAPE cases skipped by name, "
              "5 xfailed) + the result-read probe PASS on 0001-0056, on plain PostgreSQL + shim AND on "
              "the Supabase image; migrate.py history check PASS on both (the committed driver at "
              "fca3ea3, unchanged; candidate 33547abd)",
    "candidate": "33547abd (0001-0052 as proven at 0052 by KNOWN-GOOD-REPROOF-2, 0053-0056 the Lab's "
                 "composition reads, release live, variants + requeue and control grants, LOCAL-ONLY "
                 "until the next R151 window); supersedes the through-0052 proof, rerun whole",
    "evidence": ["research/plan/evidence/i/KNOWN-GOOD-REPROOF-3-8f0e3c9.md",
                 "research/plan/evidence/i/KNOWN-GOOD-REPROOF-2-68ba65f.md",
                 "research/plan/evidence/i/KNOWN-GOOD-REPROOF-fca3ea3.md",
                 "research/plan/evidence/i/KNOWN-GOOD-PROOF-3-af552ed.md",
                 "research/plan/evidence/i/KNOWN-GOOD-PROOF-2-3f7df77.md",
                 "research/plan/evidence/i/KNOWN-GOOD-PROOF-aab4b41.md"],
} for sha in ("4226315", "bda1586")}


def test_ops_recover__the_superseded_0051_proof_is_the_recorded_one_word_for_word():
    """R224: a re-proof moves the proof it replaces into `superseded` word for word.
    Failure oracle: the through-0051 entry (superseded[2] since KNOWN-GOOD-REPROOF-4) reworded,
    or a field dropped, differs from the through-0051 record at ac8bc06d."""
    record = json.loads(RECORD.read_text())
    proven = {r["sha"][:7]: r["schema_proof"] for r in record["releases"]
              if r.get("known_good") and r.get("schema_proof")}
    assert set(proven) == set(THROUGH_0051)
    for sha, proof in proven.items():
        assert proof["superseded"][2] == THROUGH_0051[sha], sha


def test_ops_recover__the_superseded_0052_proof_is_the_recorded_one_word_for_word():
    """R224 (KNOWN-GOOD-REPROOF-3): the through-0052 proof word for word (superseded[1] since
    KNOWN-GOOD-REPROOF-4).
    Failure oracle: it reworded, a field dropped, or not moved at all differs from the
    through-0052 record at 33547abd."""
    record = json.loads(RECORD.read_text())
    proven = {r["sha"][:7]: r["schema_proof"] for r in record["releases"]
              if r.get("known_good") and r.get("schema_proof")}
    assert set(proven) == set(THROUGH_0052)
    for sha, proof in proven.items():
        assert proof["superseded"][1] == THROUGH_0052[sha], sha


def test_ops_recover__the_superseded_0056_proof_is_the_recorded_one_word_for_word():
    """R224 (KNOWN-GOOD-REPROOF-4): superseded[0] is the through-0056 proof word for word.
    Failure oracle: it reworded, a field dropped, or not moved at all differs from the
    through-0056 record at 6ca7879f."""
    record = json.loads(RECORD.read_text())
    proven = {r["sha"][:7]: r["schema_proof"] for r in record["releases"]
              if r.get("known_good") and r.get("schema_proof")}
    assert set(proven) == set(THROUGH_0056)
    for sha, proof in proven.items():
        assert proof["superseded"][0] == THROUGH_0056[sha], sha

# The 14 SHAPE cases the record's runs deselected (KNOWN-GOOD-REPROOF-fca3ea3; KGR-RV-1): the
# count alone lets a real SQL case replace one of them unnoticed.
RECORDED_SHAPE = {
    "tests/d/test_credit_schema.py::test_d1r_leaves_the_0001_0005_schema_unchanged",
    "tests/d/test_credit_schema.py::test_credit_units__no_conversion_and_explicit_regimes",
    "tests/d/test_credit_schema.py::test_credit_privileges__service_reads_money_and_writes_through_seams",
    "tests/d/test_credit_schema.py::test_rerun__applying_d1r_twice_is_a_no_op",
    "tests/d/test_schema_postgres.py::test_dur_rls__the_mutation_boundary_is_narrow_and_fails_closed",
    "tests/d/test_schema_postgres.py::test_dur_rls__the_execute_surface_is_enumerated",
    "tests/d/test_admission.py::test_media__uploads_finalize_once_and_objects_delete_only_when_idle",
    "tests/d/test_settle.py::test_credit_spend__sql_settle_equals_v2_settle_on_the_grid",
    "tests/d/test_admission.py::test_results__write_once_owner_read_and_the_prepared_prompt_count",
    "tests/d/test_store_requests.py::test_put_result__write_once_reference_and_owner_read",
    "tests/d/test_schema_postgres.py::test_dur_rls__browser_roles_cannot_reach_protected_state",
    "tests/d/test_credit_schema.py::test_operator_seams__audit_keys_suspension_usage_holds",
    "tests/d/test_schema_postgres.py::test_dur_rls__the_browser_privilege_surface_is_enumerated",
    "tests/d/test_pgharness.py::test_the_decoy_is_the_tasks_own_and_d1s_is_unchanged",
}


def test_ops_recover__the_driver_deselects_exactly_the_recorded_shape_cases():
    """KGR-RV-1: the driver's SHAPE keys are the recorded set, not only its count."""
    assert set(PROOF["SHAPE"]) == RECORDED_SHAPE


def test_ops_recover__the_proof_driver_refuses_a_bad_target_and_a_moved_history():
    for bad in ("bda1586", "f" * 40):                                # short; not a commit here
        with pytest.raises(SystemExit) as refused:
            PROOF["main"]([bad])
        assert refused.value.code == 2
    compare = PROOF["compare"]
    files = {"0001": "create table a (x int);\ncreate table b (y int);\n", "0002": "select 1;\n"}
    whole = [("0001", [files["0001"]]), ("0002", [files["0002"]])]          # migrate.py's rows
    assert compare(whole, files) is None
    split = [("0001", ["create table a (x int)", "create table b (y int)"]), whole[1]]  # the CLI's
    assert compare(split, files) is None
    assert "only in candidate ['0002']" in compare(whole[:1], files)
    assert "only in history ['0003']" in compare(whole + [("0003", ["x"])], files)
    moved = [whole[0], ("0002", ["select 2;\n"])]
    assert compare(moved, files) == "statements differ from the candidate's files: ['0002']"
    assert compare([("0001", ["create table a (x int)", "drop table b"]), whole[1]], files) == \
        "statements differ from the candidate's files: ['0001']"


def test_ops_recover__the_proof_driver_counts_a_skipped_suite_as_a_fail(tmp_path):
    """The old suites skip themselves without Docker, psycopg or Valkey and pytest then exits 0:
    a suite that skipped anything, or passed nothing, ran none of the old SQL."""
    api = tmp_path / "apps" / "infrx-api"
    (api / "tests" / "d").mkdir(parents=True)
    bodies = {"skips": "def test_a():\n    pytest.skip('no docker')\n",
              "partly": "def test_a():\n    pass\n\n\ndef test_b():\n    pytest.skip('no valkey')\n",
              "xfails": "def test_a():\n    pytest.xfail('shape')\n",
              "runs": "def test_a():\n    pass\n"}
    for name, body in bodies.items():
        (api / "tests" / "d" / f"test_{name}.py").write_text(f"import pytest\n\n\n{body}")
    pytest = (sys.executable, "-m", "pytest")           # the scratch tree's `uv run` is not the point
    verdict = {name: PROOF["run_suite"](api, f"tests/d/test_{name}.py", {}, pytest) for name in bodies}
    assert {name: ok for name, (ok, _) in verdict.items()} == \
        {"skips": False, "partly": False, "xfails": False, "runs": True}, verdict
    assert "1 skipped" in verdict["skips"][1]


def test_ops_recover__a_cli_split_history_must_be_the_whole_file_in_order():
    compare = PROOF["compare"]
    files = {"0001": "-- header\ncreate table a (x int);\ncreate table b (y int); /* note */\n"
                     "revoke all on a from anon;\n", "0002": "select 1; -- drop table b\n"}
    stmts = ["create table a (x int)", "create table b (y int)", "revoke all on a from anon"]
    for good in (stmts, [s + ";" for s in stmts], ["-- header\n" + stmts[0], *stmts[1:]]):
        assert compare([("0001", good), ("0002", ["select 1"])], files) is None, good
    refused = "statements differ from the candidate's files: ['0001']"
    for bad in (stmts[:2], stmts[1:], [stmts[0], stmts[2]], stmts[::-1], stmts + stmts[-1:],
                ["a", "b"], ["create table a", "(x int)", *stmts[1:]], [*stmts[:2], ""]):
        assert compare([("0001", bad), ("0002", ["select 1"])], files) == refused, bad
    # a "statement" that is text inside the file's comment is not one of its statements
    assert compare([("0001", stmts), ("0002", ["select 1", "drop table b"])], files) == \
        "statements differ from the candidate's files: ['0002']"


def test_ops_recover__both_targets_are_known_good_through_0059_and_not_beyond(tmp_path):
    """KNOWN-GOOD-PROOF-2/-3 (RR:51), KNOWN-GOOD-REPROOF(-2/-3/-4) (R151): each target's REAL record
    entry, judged against this checkout's real 0019-0059 bytes, is KNOWN-GOOD with hosted at
    0024-0059 (0026 fences put_result; its lease-less call, the targets' write, is 0014's;
    0027-0059 are the Lab's and the trace consent read) and NOT at 0060, which no proof reaches. The target tree is a stand-in commit (0001-0018 and the
    preparation loop, as both targets carry) because mutation copies are not git checkouts;
    the real-sha verdicts are the evidence's `known-good.py <sha> --applied 0059|0060` runs."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "trunk")
    target = _commit(repo, {MAIN: "PreparationRunner(jobs)\n", PREP: "class PreparationRunner: ...\n",
                            f"{MIG}/0018_x.sql": "", PREFLIGHT: "TUNABLE = (\n)\n"})
    for real in sorted((support.REPO / MIG).glob("[0-9][0-9][0-9][0-9]_*.sql")):
        if real.name[:4] > "0018":
            (repo / MIG / real.name).write_bytes(real.read_bytes())
    judge = KNOWN_GOOD["judge"]
    for real in (r for r in json.loads(RECORD.read_text())["releases"] if r.get("schema_proof")):
        entry = {**real, "sha": target}
        for path in entry["evidence"] + entry["schema_proof"]["evidence"]:
            (repo / path).parent.mkdir(parents=True, exist_ok=True)
            (repo / path).touch()
        at = {applied: judge(target, applied, [], None, {"releases": [entry]}, repo)
              for applied in ("0024", "0026", "0027", "0051", "0052", "0056", "0057", "0059", "0060")}
        assert {at[a]["verdict"] for a in ("0024", "0026", "0027", "0051", "0052", "0056", "0057", "0059")} == \
            {"KNOWN-GOOD"}, (real["sha"], at)
        assert at["0060"]["verdict"] == "NOT-KNOWN-GOOD"
        assert [c["check"] for c in at["0060"]["checks"] if not c["ok"]] == ["migrations"]


def test_ops_recover__every_evidence_path_the_record_names_exists():
    """KGR3-RV-1: every evidence path of the real record (each proven target's `evidence` and
    its `schema_proof.evidence`) exists in this checkout; known-good.py refuses a target whose
    evidence is missing, so a renamed or unmerged evidence file must fail here first."""
    for real in (r for r in json.loads(RECORD.read_text())["releases"] if r.get("schema_proof")):
        for path in real["evidence"] + real["schema_proof"]["evidence"]:
            assert (support.REPO / path).exists(), (real["sha"], path)


def test_ops_recover__hosted_migrate_expects_exactly_the_migrations_after_its_anchor():
    """INFRA-03/DT-12 (supersedes KGR4-RV-1's patch-order case; the spent window patches are
    research/plan/evidence/i/hosted-migrate-*.patch): hosted-migrate.sh's EXPECTED_PENDING is this
    tree's migrations after its HOSTED_APPLIED anchor (computed as lab-migrate.sh computes it) and its
    W7 post-check names the newest - so the next window is one reviewed edit of those three lines."""
    text = (support.REPO / "infra" / "rollout" / "hosted-migrate.sh").read_text()
    at = re.search(r'case "\$HOSTED_APPLIED" in \*"(\d{4}) ', text)[1]
    files = sorted(p.name for p in (support.REPO / MIG).glob("[0-9][0-9][0-9][0-9]_*.sql"))
    expected = re.search(r'^EXPECTED_PENDING="([^"]*)"', text, re.M)[1].split(", ")
    after = [f for f in files if f[:4] > at]
    # R269/R271: a wave-7 file (0060-0068, migrations/README.md) stays LOCAL-ONLY after the
    # window's set until its own window edits these lines
    assert [f[:4] for f in after[:len(expected)]] == expected, (expected, after)
    assert all("0060" <= f[:4] <= "0068" for f in after[len(expected):]), after[len(expected):]
    newest = after[len(expected) - 1][:-4]
    assert f'case "$POST" in *"{newest[:4]} {newest[5:]}"$\'\\n\'"nothing pending") ;;' in text
