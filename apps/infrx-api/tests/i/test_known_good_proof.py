"""KNOWN-GOOD-PROOF: a rollback target on a schema newer than its tree.

Failure oracles: a schema_proof counts only as far as its `through` - a target proven through
0022 is KNOWN-GOOD at --applied 0022 and NOT at 0023; the record carries a proof for both
known-good targets (none before this lane: the rehearsal at e607b705 found no target once the
window applies 0019+), bound to the sha256 of the migration bytes it ran on (a revised 0022/0023
in this checkout is not proven); the proof driver refuses a short or unknown target, counts a
suite that skipped or passed nothing as FAIL (pytest exits 0 on skips), and its migrated-DSN
check refuses a history that differs from the candidate's files by a version or by a byte, or a
CLI-split history that omits, reorders or fragments the file's statements.
"""
from __future__ import annotations

import hashlib
import json
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
        assert proof["through"] >= "0022" and proof["through"] in tree
        assert any("KNOWN-GOOD-PROOF-" in p for p in proof["evidence"])
        # the bytes it ran on, beyond both targets' 0001-0018: a revised 0022/0023 fails here
        assert proof["files"] == {v: h for v, h in tree.items() if "0018" < v <= proof["through"]}


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
    env = {"VIRTUAL_ENV": sys.prefix}                   # this interpreter's pytest, outside a project
    verdict = {name: PROOF["run_suite"](api, f"tests/d/test_{name}.py", env) for name in bodies}
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
