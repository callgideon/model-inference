"""E4B: the certification runner's own rules, with no stack, engine or network.

Each case pins one decision `certify.py` makes - what a report must carry, when a check may
say PASS, what the dataset drill and the ledger reconciliation count as a defect - so that a
single edit to that decision fails here (tests/integration/backend/e4b_mutants.py).
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import certify                                          # noqa: E402

TARGET = {"kind": "local", "scale": "tiny", "label": certify.FAKE}


def first(problems: list[str]) -> str:
    """The first problem reported; none at all is the assertion that fails."""
    assert problems, "no problem was reported"
    return problems[0]


# ------------------------------------------------------------------------------ report

def test_e4b_the_report_carries_both_heads_the_target_the_hashes_and_the_exit_rule():
    """A report is evidence for one tree and one target: both `git_head` samples, the
    target, the hashes, every check's owners and label. Exit 0 only when every entry is
    PASS: a typed PENDING or SKIP is 3 (never a pass), any FAIL is 1."""
    report = certify.Report(TARGET)
    report.hashes = {"serve_sh": "ab" * 32}
    report.check("e4b.x", certify.PASS, "fine", measured={"n": 1}, label=certify.FAKE)
    assert report.exit_code == 0
    report.check("e4b.y", certify.SKIP, "box only", owners=("BOX",))
    assert report.exit_code == 3
    report.check("e4b.z", certify.PENDING, "cutover", owners=("G2-R1", "D5"))
    assert report.exit_code == 3
    doc = json.loads(report.as_json())
    assert {"git_head", "git_head_end", "target", "hashes", "stages", "backend_ready"} <= set(doc)
    assert doc["target"] == TARGET and doc["hashes"] == report.hashes
    assert "not decided" in doc["backend_ready"]
    assert [(s["stage"], s["owners"], s["label"]) for s in doc["stages"]] == [
        ("e4b.x", None, certify.FAKE), ("e4b.y", ["BOX"], None), ("e4b.z", ["D5", "G2-R1"], None)]
    report.check("e4b.w", certify.FAIL, "broken")
    assert report.exit_code == json.loads(report.as_json())["exit_code"] == 1


def test_e4b_a_skip_or_pending_without_a_known_owner_is_a_failure():
    """Every skip is typed with an owner from the closed vocabulary; anything else is a
    certification that silently did not run, and is recorded as the FAIL it is."""
    report = certify.Report(TARGET)
    for status, owners in ((certify.PENDING, ()), (certify.SKIP, ()),
                           (certify.PENDING, ("G9Z",)), (certify.SKIP, ("BOX", "nobody"))):
        entry = report.check("e4b.untyped", status, "why", owners=owners)
        assert entry["status"] == certify.FAIL, (status, owners)
        assert entry["detail"]["untyped"] == status
    assert report.check("e4b.typed", certify.PENDING, "why", owners=("BOX",))["status"] \
        == certify.PENDING
    assert {"BOX", "STACK", "D5", "G2-R1", "I2B-R4", "M1-L2"} <= set(certify.OWNERS)


# ------------------------------------------------------------------------------ hashes

def test_e4b_the_release_hashes_recompute_the_pinned_engine_options():
    """The report's hashes come from the tree: W3's engine-options digest is recomputed from
    the recorded flags, and the model/image pins are the serving record's."""
    hashes = certify.release_hashes()
    record = certify.serving_record()
    assert hashes["engine_options_digest"] == record["engine_options_digest"] \
        == hashes["engine_options_digest_recomputed"]
    assert hashes["runtime_image"] == record["runtime_image"]["ref"]
    assert hashes["model_digests"]["weight_shards"] == record["model"]["weight_shard_digests"]
    for name in ("serving_version_json", "serve_sh", "contract_limits", "migrations",
                 "deploy_tree", "rollout_tree", "alert_rules", "uv_lock", "infrx_package"):
        assert re.fullmatch(r"[0-9a-f]{64}", hashes[name]), name
    flags = certify.served_flags(record)
    assert "${" not in json.dumps(flags) and record["settings"]["ENGINE_MAX_NUM_SEQS"] in flags
    assert certify.options_digest(certify.served_flags(record, ENGINE_MAX_NUM_SEQS="32")) \
        != record["engine_options_digest"]


def test_e4b_the_recomputed_digest_comes_from_the_flags_not_from_the_record(monkeypatch):
    """A record whose digest no longer matches its flags is visible in the report: the
    recomputed value is derived, never copied from the field it is checked against."""
    record = certify.serving_record()
    monkeypatch.setattr(certify, "serving_record",
                        lambda: {**record, "engine_options_digest": "sha256:" + "0" * 64})
    hashes = certify.release_hashes()
    assert hashes["engine_options_digest"] == "sha256:" + "0" * 64
    assert hashes["engine_options_digest_recomputed"] == record["engine_options_digest"]


# ------------------------------------------------------------------------------ backend

def _cases():
    rec = "tests.integration.backend.recovery.test_recovery"
    drills = "tests.integration.backend.test_drills"
    return {"passed": [f"{drills}::test_e3b_dr01", f"{rec}::test_i3b_rc01"],
            "failed": [], "skipped": [],
            "pending": {"G2-R1": [f"{drills}::test_backend_journey", f"{rec}::test_i3b_rc03"],
                        "I2B-R4": [f"{rec}::test_i3b_rc08b"]}}


def test_e4b_the_backend_suite_splits_into_protocol_and_recovery_by_the_gates_rule():
    """`recovery/` is I3B's drills (E4B.b); everything else is the protocol suite (E4B.a).
    Each half gets the gate's own verdict, with the ids it pends on as its owners."""
    halves = certify.split_backend(_cases())
    assert halves["protocol"]["passed"] == ["tests.integration.backend.test_drills::test_e3b_dr01"]
    assert halves["recovery"]["pending"] == {
        "G2-R1": ["tests.integration.backend.recovery.test_recovery::test_i3b_rc03"],
        "I2B-R4": ["tests.integration.backend.recovery.test_recovery::test_i3b_rc08b"]}
    gate = [{"stage": stage, "status": certify.PASS} for stage in certify.GATE_STAGES]
    report = certify.Report(TARGET)
    certify.suite_check(report, "e4b.a.protocol", halves["protocol"], gate)
    certify.suite_check(report, "e4b.b.recovery", halves["recovery"], gate)
    assert [(s["status"], s["owners"]) for s in report.stages] == [
        (certify.PENDING, ["G2-R1"]), (certify.PENDING, ["G2-R1", "I2B-R4"])]
    broken = {**halves["recovery"], "failed": ["x.recovery.test_restore::test_i3b_bk01"]}
    certify.suite_check(report, "e4b.b.recovery", broken, gate)
    assert report.stages[-1]["status"] == certify.FAIL
    down = [*gate[:-1], {"stage": "rls", "status": certify.FAIL}]
    clean = {"passed": ["a.test_drills::test_x"], "failed": [], "skipped": [], "pending": {}}
    certify.suite_check(report, "e4b.a.protocol", clean, down)
    assert report.stages[-1]["status"] == certify.FAIL
    assert report.stages[-1]["detail"]["stages_not_passed"] == ["rls=FAIL"]
    certify.suite_check(report, "e4b.a.protocol", clean, gate)
    assert report.stages[-1]["status"] == certify.PASS


# ------------------------------------------------------------------------------ parity

def _parity_row(clip, content="d67be709"):
    return {"clip_id": clip, "sha256": "aa" + clip, "outcome": "accepted", "prompt_tokens": 1200,
            "duration_s": 2.0, "width": 640, "height": 360, "content_sha256": content,
            "events": []}


def test_e4b_sop_parity_pairs_the_engine_with_its_baseline_and_fails_on_drift(tmp_path,
                                                                               monkeypatch):
    """MARLIN-SOP through W4's own verdict: the same outputs pass, drifted content fails, a
    remote engine with no baseline is PENDING on the box - never a pass."""
    outputs = []

    def fake_client(argv, env=None):
        assert outputs, "parity.py ran with no baseline to pair it with"
        out = Path(argv[argv.index("--out") + 1])
        out.write_text("".join(json.dumps(row) + "\n" for row in outputs.pop(0)))
        return {"exit": 0, "tail": ""}
    monkeypatch.setattr(certify, "client", fake_client)
    same = [_parity_row("c039"), _parity_row("c024")]
    report = certify.Report(TARGET)
    outputs[:] = [same, same]
    certify.parity_check(report, engine_url="http://e", workdir=tmp_path, baseline=None,
                         label=certify.FAKE, local=True)
    assert (report.stages[-1]["status"], report.stages[-1]["label"]) == (certify.PASS,
                                                                        certify.FAKE)
    outputs[:] = [same, [_parity_row("c039"), _parity_row("c024", content="0ff")]]
    certify.parity_check(report, engine_url="http://e", workdir=tmp_path, baseline=None,
                         label=certify.FAKE, local=True)
    assert report.stages[-1]["status"] == certify.FAIL
    assert "c024" in report.stages[-1]["detail"]["why"]
    outputs[:] = [[_parity_row("c039")]]
    base = tmp_path / "e0.jsonl"
    base.write_text("".join(json.dumps(r) + "\n" for r in same))
    certify.parity_check(report, engine_url="http://e", workdir=tmp_path, baseline=base,
                         label=certify.MEAS, local=False)
    assert (report.stages[-1]["status"], report.stages[-1]["owners"]) == (certify.PENDING,
                                                                          ["BOX"])
    certify.parity_check(report, engine_url="http://e", workdir=tmp_path, baseline=None,
                         label=certify.MEAS, local=False)
    assert (report.stages[-1]["status"], report.stages[-1]["owners"]) == (certify.PENDING,
                                                                          ["BOX"])
    assert outputs == [], "a remote engine with no baseline must not be run and passed"


# ------------------------------------------------------------------------------ dataset

def _row(item, outcome="accepted", *, status=200, job=None, key=None):
    return {"item_key": item, "idempotency_key": key or f"sop1.{item}", "outcome": outcome,
            "http_status": status, "inference_id": job if job is not None else f"job-{item}"}


FIRST = [_row("i1"), _row("i2"), _row("i3", "failed", status=200), _row("i4", "rejected",
                                                                         status=429)]
SECOND = [_row("i3"), _row("i4"), _row("i5"), _row("i6", "rejected", status=400)]


def test_e4b_the_resume_drill_counts_a_run_that_was_not_interrupted_as_proving_nothing():
    """The client half of MARLIN-SOP's resume: a real interruption, one key per item,
    nothing terminal re-sent, and every item terminal at the end."""
    assert certify.resume_problems(FIRST, SECOND, items=6, first_interrupted=True) == []
    assert "not interrupted" in first(certify.resume_problems(
        FIRST, SECOND, items=6, first_interrupted=False))
    everything = [*FIRST[:2], *SECOND[:3], _row("i6")]
    assert certify.resume_problems(everything, [], items=6, first_interrupted=True) == [
        "not interrupted mid-run (interrupted=True, 6 of 6 items accepted): the drill proved "
        "nothing"]
    resent = certify.resume_problems(FIRST, [*SECOND, _row("i1")], items=6,
                                     first_interrupted=True)
    assert resent == ["terminal items re-sent by the resume: ['i1']"]
    rekeyed = certify.resume_problems(FIRST, [_row("i3", key="sop1.other"), *SECOND[1:]],
                                      items=6, first_interrupted=True)
    assert rekeyed == ["items sent under more than one key: ['i3']"]
    open_item = certify.resume_problems(FIRST, [*SECOND[:3], _row("i6", "failed")],
                                        items=6, first_interrupted=True)
    assert open_item == ["items not terminal after the resume: ['i6']"]
    short = certify.resume_problems(FIRST, SECOND[:3], items=6, first_interrupted=True)
    assert short == ["5 distinct items across both runs, 6 scheduled"]


@dataclass
class Usage:
    request_id: str
    charged_amount: str
    unit: str = "CREDIT"


@dataclass
class Hold:
    request_id: str
    state: str = "released"
    amount: str = "1.00000000"


@dataclass
class Balance:
    ledger_total: Decimal
    reserved_total: Decimal


ROWS = [*FIRST, *SECOND]
JOBS = ("job-i1", "job-i2", "job-i3", "job-i4", "job-i5")
USAGE = [Usage(job, "0.50000000") for job in JOBS] + [Usage("job-other", "9.00000000")]
HOLDS = [Hold(job) for job in JOBS]
BEFORE = Balance(Decimal("10000"), Decimal("0"))
AFTER = Balance(Decimal("9997.5"), Decimal("0"))


def test_e4b_the_ledger_reconciles_item_by_item_with_no_duplicate_accepted_item():
    """The server half: one job per accepted item across both runs (a replay answers the
    same Inference-Id), one CREDIT usage record and one released hold per job, Σ charged =
    the ledger's fall, reserved restored. Another request's record is not this run's."""
    assert certify.reconcile_problems(ROWS, USAGE, HOLDS, BEFORE, AFTER) == []
    twice = [*ROWS, _row("i1", job="job-i1-again")]
    assert "more than one job" in first(certify.reconcile_problems(twice, USAGE, HOLDS, BEFORE,
                                                                   AFTER))
    assert first(certify.reconcile_problems(ROWS, USAGE[1:], HOLDS, BEFORE, AFTER)).startswith(
        "jobs without exactly one usage record")
    doubled = [*USAGE, Usage("job-i2", "0.50000000")]
    assert "job-i2" in first(certify.reconcile_problems(ROWS, doubled, HOLDS, BEFORE,
                                                        Balance(Decimal("9997"), Decimal("0"))))
    usd = [Usage("job-i1", "0.50000000", unit="USD"), *USAGE[1:]]
    assert certify.reconcile_problems(ROWS, usd, HOLDS, BEFORE, AFTER) == [
        "usage recorded outside CREDIT: ['job-i1']"]
    held = [Hold("job-i1", state="held"), *HOLDS[1:]]
    assert certify.reconcile_problems(ROWS, USAGE, held, BEFORE, AFTER) == [
        "holds still held after every item is terminal: ['job-i1']"]
    assert "exactly one hold" in first(certify.reconcile_problems(ROWS, USAGE, HOLDS[1:], BEFORE,
                                                                  AFTER))
    assert certify.reconcile_problems(ROWS, USAGE, HOLDS, BEFORE,
                                      Balance(Decimal("9997"), Decimal("0"))) == [
        "Σ charged 2.50000000 != ledger fall 3"]
    assert certify.reconcile_problems(ROWS, USAGE, HOLDS, BEFORE,
                                      Balance(Decimal("9997.5"), Decimal("1"))) == [
        "reserved 0 -> 1"]


def test_e4b_the_dataset_drill_pends_on_the_owner_it_needs_and_passes_only_reconciled(
        tmp_path, monkeypatch):
    """The drill's orchestration: interrupted run, resume, then the ledger. The engine
    target stops at the client half (PENDING on the held cutover), a metered target with no
    ledger adapter is PENDING on D5, and only a reconciled ledger passes."""
    runs = []

    def interrupted(argv, raw, *, after, env, timeout_s):
        raw.write_text("".join(json.dumps(r) + "\n" for r in FIRST))
        runs.append(("first", after))
        return {"exit": 130, "signalled": True}

    def fake_client(argv, env=None):
        assert "--resume" in argv and argv[argv.index("--resume") + 1].endswith("dataset-raw.jsonl")
        out = Path(argv[argv.index("--raw") + 1])
        out.write_text("".join(json.dumps(r) + "\n" for r in SECOND))
        runs.append(("resume",))
        return {"exit": 0, "tail": ""}
    monkeypatch.setattr(certify, "interrupted_run", interrupted)
    monkeypatch.setattr(certify, "client", fake_client)
    monkeypatch.setitem(certify.MATRIX["tiny"], "dataset",
                        {"items": 6, "interrupt_after": 2, "rate": 4.0})
    local = {**TARGET, "metered": False, "base_url": "http://e/v1", "bench_target": "direct",
             "model": "marlin2b"}
    report = certify.Report(local)
    certify.dataset_check(report, local, tmp_path)
    entry = report.stages[-1]
    assert (entry["status"], entry["owners"], entry["label"]) == (certify.PENDING, ["G2-R1"],
                                                                  certify.FAKE)
    assert entry["measured"]["first_run"]["accepted"] == 2 and runs == [("first", 2), ("resume",)]
    metered = {**local, "metered": True, "label": certify.MEAS, "bench_target": "gateway"}
    monkeypatch.setattr(certify, "tenant_ledger", lambda: None)
    certify.dataset_check(report, metered, tmp_path)
    assert (report.stages[-1]["status"], report.stages[-1]["owners"]) == (certify.PENDING,
                                                                          ["D5"])
    views = iter([(BEFORE, [], []), (AFTER, USAGE, HOLDS)])
    certify.dataset_check(report, metered, tmp_path, ledger=lambda: next(views))
    assert report.stages[-1]["status"] == certify.PASS, report.stages[-1]["detail"]
    views = iter([(BEFORE, [], []), (AFTER, USAGE[1:], HOLDS)])
    certify.dataset_check(report, metered, tmp_path, ledger=lambda: next(views))
    assert report.stages[-1]["status"] == certify.FAIL
    # the client half fails on its own: a run the signal never interrupted, a resume that
    # did not finish - on any target, before a ledger is read
    monkeypatch.setattr(certify, "interrupted_run",
                        lambda argv, raw, **_: interrupted(argv, raw, after=2, env=None,
                                                           timeout_s=1) and
                        {"exit": 0, "signalled": False})
    certify.dataset_check(report, local, tmp_path)
    assert report.stages[-1]["status"] == certify.FAIL
    assert "not interrupted" in first(report.stages[-1]["detail"])
    monkeypatch.setattr(certify, "interrupted_run", interrupted)
    monkeypatch.setattr(certify, "client", lambda argv, env=None: {
        **fake_client(argv, env), "exit": 1, "tail": "bench failed"})
    certify.dataset_check(report, local, tmp_path)
    assert report.stages[-1]["status"] == certify.FAIL
    assert report.stages[-1]["detail"] == ["the resumed run exited 1: bench failed"]


def test_e4b_the_protocol_file_states_the_numbers_the_runner_applies():
    """E4B-protocol.md §5 is the predeclared source; `CRITERIA` and `MATRIX` must say the
    same, row by row, so neither can move without the other."""
    text = certify.PROTOCOL.read_text()
    table = dict(re.findall(r"^\| `(\w+)` \| ([^|]+?) \|", text, re.M))
    for name, value in certify.CRITERIA.items():
        assert name in table, name
        assert float(table[name]) == float(value), (name, table[name], value)
    assert table["engine_ceiling_s"] == str(certify.engine_ceiling_s(certify.serving_record()))
    shapes = {row[0]: row[1:] for row in re.findall(
        r"^\| `(tiny|box)`[^|]*\| ([^|]+) \| ([^|]+) \| (\d+)[^|]* \| ([^|]+) \|", text, re.M)}
    tiny = certify.MATRIX["tiny"]
    assert shapes["tiny"] == (
        f"{tiny['envelope']['rates'][0]} × {tiny['envelope']['requests']}",
        f"{tiny['soak']['rate']} × {tiny['soak']['seconds']}, {tiny['soak']['sample_s']} s",
        str(tiny["overload"]["burst"]),
        f"{tiny['dataset']['items']} / {tiny['dataset']['interrupt_after']} / "
        f"{tiny['dataset']['rate']}")
    box = certify.MATRIX["box"]
    assert shapes["box"][0].startswith(
        ", ".join(str(r) for r in box["envelope"]["rates"]) + f" × {box['envelope']['requests']}")
    assert f"× {box['soak']['seconds']}" in shapes["box"][1] and \
        f"{box['soak']['sample_s']} s" in shapes["box"][1]
    assert shapes["box"][2] == str(box["overload"]["burst"])
    assert shapes["box"][3] == (f"{box['dataset']['items']} / "
                                f"{box['dataset']['interrupt_after']} / {box['dataset']['rate']}")
    assert set(re.findall(r"`(\w+_codes)` \| ([^|]+) \|", text)[0][1].replace(" ", "")
               .split(",")) == set(certify.OVERLOAD_CODES)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
