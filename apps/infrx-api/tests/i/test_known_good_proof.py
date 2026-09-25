"""KNOWN-GOOD-PROOF: a rollback target on a schema newer than its tree.

Failure oracles: a schema_proof counts only as far as its `through` - a target proven through
0022 is KNOWN-GOOD at --applied 0022 and NOT at 0023; the record carries a proof for both
known-good targets (none before this lane: the rehearsal at e607b705 found no target once the
window applies 0019+); the proof driver refuses a short or unknown target, and its migrated-DSN
check refuses a history that differs from the candidate's files by a version or by a byte.
"""
from __future__ import annotations

import json
import runpy

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

    assert verdict("0022") == "NOT-KNOWN-GOOD"                       # fails before: no proof
    proof = {"through": "0022", "evidence": ["ev/proof.md"]}
    assert verdict("0018", proof) == "KNOWN-GOOD"
    assert verdict("0022", proof) == "KNOWN-GOOD"
    assert verdict("0023", proof) == "NOT-KNOWN-GOOD"                # 0023 is not proven
    assert verdict("0023", {**proof, "through": "0023"}) == "KNOWN-GOOD"


def test_ops_recover__the_record_proves_both_targets_on_the_candidate_schema():
    record = json.loads(RECORD.read_text())
    versions = {p.name[:4] for p in (support.REPO / MIG).glob("[0-9][0-9][0-9][0-9]_*.sql")}
    proven = {r["sha"][:7]: r["schema_proof"] for r in record["releases"]
              if r.get("known_good") and r.get("schema_proof")}
    assert set(proven) == {"4226315", "bda1586"}
    for proof in proven.values():
        assert proof["through"] >= "0022" and proof["through"] in versions
        assert any("KNOWN-GOOD-PROOF-" in p for p in proof["evidence"])


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
