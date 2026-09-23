"""E4B: the certification runner's own rules, with no stack, engine or network.

Each case pins one decision `certify.py` makes - what a report must carry, when a check may
say PASS, what the dataset drill and the ledger reconciliation count as a defect - so that a
single edit to that decision fails here (tests/integration/backend/e4b_mutants.py).
"""
from __future__ import annotations

import itertools
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
CLEAN = {"sha": "c" * 40, "dirty": False}


@pytest.fixture
def clean_tree(monkeypatch):
    """Both git samples a clean, known tree - whatever the checkout running the case is."""
    monkeypatch.setattr(certify.run, "git_head", lambda: dict(CLEAN))


def first(problems: list[str]) -> str:
    """The first problem reported; none at all is the assertion that fails."""
    assert problems, "no problem was reported"
    return problems[0]


# ------------------------------------------------------------------------------ report

def test_e4b_the_report_carries_both_heads_the_target_the_hashes_and_the_exit_rule(clean_tree):
    """A report is evidence for one tree and one target: both `git_head` samples, the
    target, the hashes, every check's owners and label. Exit 0 only when every entry is
    PASS: a typed PENDING or SKIP is 3 (never a pass), any FAIL is 1."""
    report = certify.Report(TARGET)
    report.hashes = {"serve_sh": "ab" * 32}
    report.check("e4b.x", certify.PASS, "fine", measured={"n": 1}, label=certify.FAKE)
    assert report.exit_code == 0
    report.check("e4b.y", certify.SKIP, "box only", owners=("BOX",))
    assert report.exit_code == 3
    report.check("e4b.z", certify.PENDING, "dev host", owners=("STACK", "BOX"))
    assert report.exit_code == 3
    doc = json.loads(report.as_json())
    assert {"git_head", "git_head_end", "target", "hashes", "stages", "backend_ready"} <= set(doc)
    assert doc["target"] == TARGET and doc["hashes"] == report.hashes
    assert "not decided" in doc["backend_ready"]
    assert [(s["stage"], s["owners"], s["label"]) for s in doc["stages"]] == [
        ("e4b.x", None, certify.FAKE), ("e4b.y", ["BOX"], None), ("e4b.z", ["BOX", "STACK"], None),
        ("release-identity", None, None)]
    assert doc["git_head"] == doc["git_head_end"] == CLEAN and doc["exit_code"] == 3
    report.check("e4b.w", certify.FAIL, "broken")
    assert report.exit_code == json.loads(report.as_json())["exit_code"] == 1


def test_e4b_a_report_counts_for_one_clean_known_tree_or_it_fails(monkeypatch, tmp_path):
    """Review F1, protocol §6.3 as code: a SHA at both ends, both clean (an unknown state is
    not clean) and the same - otherwise a report whose every check passed still exits 1."""
    dirty, unknown = {**CLEAN, "dirty": True}, {"sha": None, "dirty": None}
    moved = {**CLEAN, "sha": "d" * 40}
    assert certify.identity_problems(CLEAN, CLEAN) == []
    assert certify.identity_problems(dirty, CLEAN) == ["the tree at the start is dirty"]
    assert certify.identity_problems(CLEAN, {**CLEAN, "dirty": None}) == [
        "the tree at the end is of unknown state"]
    assert certify.identity_problems(CLEAN, unknown) == [
        "no git SHA at the end of the run", "the tree at the end is of unknown state"]
    assert certify.identity_problems(CLEAN, moved) == [
        f"the tree moved during the run: {'c' * 40} -> {'d' * 40}"]
    heads = itertools.chain([dirty], itertools.repeat(CLEAN))
    monkeypatch.setattr(certify.run, "git_head", lambda: dict(next(heads)))
    report = certify.Report(TARGET)
    report.check("e4b.x", certify.PASS, "fine")
    doc = json.loads(report.as_json())
    assert (doc["stages"][-1]["stage"], doc["stages"][-1]["status"]) == ("release-identity",
                                                                         certify.FAIL)
    assert doc["exit_code"] == report.exit_code == 1
    assert doc["git_head"] == dirty and doc["git_head_end"] == CLEAN
    # git that cannot answer (not a tree) is an unknown state, never a clean one
    monkeypatch.undo()
    monkeypatch.setattr(certify.harness, "REPO_ROOT", tmp_path)
    assert certify.run.git_head() == {"sha": None, "dirty": None}


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
    assert {"BOX", "STACK"} | set(certify.recoverykit.PENDING) == set(certify.OWNERS)


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
            "pending": {"BOX": [f"{drills}::test_backend_journey", f"{rec}::test_i3b_rc03"],
                        "STACK": [f"{rec}::test_i3b_rc08b"]}}


def test_e4b_the_backend_suite_splits_into_protocol_and_recovery_by_the_gates_rule():
    """`recovery/` is I3B's drills (E4B.b); everything else is the protocol suite (E4B.a).
    Each half gets the gate's own verdict, with the ids it pends on as its owners."""
    halves = certify.split_backend(_cases())
    assert halves["protocol"]["passed"] == ["tests.integration.backend.test_drills::test_e3b_dr01"]
    assert halves["recovery"]["pending"] == {
        "BOX": ["tests.integration.backend.recovery.test_recovery::test_i3b_rc03"],
        "STACK": ["tests.integration.backend.recovery.test_recovery::test_i3b_rc08b"]}
    gate = [{"stage": stage, "status": certify.PASS} for stage in certify.GATE_STAGES]
    report = certify.Report(TARGET)
    certify.suite_check(report, "e4b.a.protocol", halves["protocol"], gate)
    certify.suite_check(report, "e4b.b.recovery", halves["recovery"], gate)
    assert [(s["status"], s["owners"]) for s in report.stages] == [
        (certify.PENDING, ["BOX"]), (certify.PENDING, ["BOX", "STACK"])]
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
    assert (entry["status"], entry["owners"], entry["label"]) == (certify.PENDING, ["BOX"],
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
    certify.dataset_check(report, metered, tmp_path, ledger=lambda: next(views),
                          settle_wait_s=0)
    assert report.stages[-1]["status"] == certify.FAIL
    # a debit that lands after the answer is waited for (bounded), not failed on sight
    monkeypatch.setattr(certify.time, "sleep", lambda s: None)
    views = iter([(BEFORE, [], []), (BEFORE, [], HOLDS), (AFTER, USAGE, HOLDS)])
    certify.dataset_check(report, metered, tmp_path, ledger=lambda: next(views))
    assert report.stages[-1]["status"] == certify.PASS, report.stages[-1]["detail"]
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



# ------------------------------------------------------------------------------ E4B.b

BOX_INVENTORY = certify.harness.REPO_ROOT / "research/plan/evidence/w/box/inventory-20260923T0319Z.txt"


def test_e4b_the_config_pin_names_every_setting_that_moved_past_its_evidence(monkeypatch):
    """"Reject any optimization that invalidates earlier evidence": every declared setting is
    read from its own source, and one that moved fails the check naming the evidence."""
    from infrx.contracts.limits import DEFAULTS
    current, record = certify.current_config(), certify.serving_record()
    assert set(current) == set(certify.declared())
    assert current["engine_options_digest"] == certify.options_digest(certify.served_flags(record))
    assert current["published_engine_options_digest"] == \
        certify.published_release()["engine_options_digest"]
    assert current["published_runtime_image"] == certify.published_release()["runtime_image_ref"]
    assert (current["preparation_concurrency"], current["max_video_seconds"]) == (
        DEFAULTS.preparation_concurrency, DEFAULTS.max_video_seconds)
    declared = {name: value for name, (value, _) in certify.declared().items()}
    assert certify.config_problems(declared) == []
    moved = certify.config_problems({**declared, "encoder_budget_tokens": 32768})
    assert len(moved) == 1 and "encoder_budget_tokens: 32768" in moved[0] and "W4" in moved[0]
    report = certify.Report(TARGET)
    monkeypatch.setattr(certify, "current_config", lambda: dict(declared))
    certify.config_pin_check(report, None)
    assert (report.stages[-1]["status"], report.stages[-1]["owners"]) == (certify.PENDING,
                                                                          ["BOX"])
    monkeypatch.setattr(certify, "current_config", lambda: {**declared, "profile_version": "v2"})
    certify.config_pin_check(report, None)
    assert report.stages[-1]["status"] == certify.FAIL


def test_e4b_the_declared_settings_are_the_serving_record_read_never_typed(tmp_path, monkeypatch):
    """Review F6: the pin's W3/W4 values are serving-version.json's, read from it - so W4
    phase B's re-declaration moves them with the record - and the published release must be
    that record. The tree's second source is serve.sh: a launch script that drifted from the
    record fails the pin by name."""
    record = certify.serving_record()
    pinned = {name: value for name, (value, _) in certify.declared().items()}
    assert (pinned["engine_options_digest"], pinned["runtime_image"],
            pinned["engine_max_num_seqs"], pinned["contract_engine_max_num_seqs"]) == (
        record["engine_options_digest"], record["runtime_image"]["ref"],
        record["settings"]["ENGINE_MAX_NUM_SEQS"], int(record["settings"]["ENGINE_MAX_NUM_SEQS"]))
    assert pinned["encoder_budget_tokens"] == certify.encoder_budget(certify.served_flags(record))
    assert (pinned["published_engine_options_digest"], pinned["published_runtime_image"]) == (
        record["engine_options_digest"], record["runtime_image"]["ref"])
    tonight = {**record, "engine_options_digest": "sha256:" + "5" * 64,
               "flags": [*record["flags"], "--max-num-batched-tokens", "32768"]}
    monkeypatch.setattr(certify, "serving_record", lambda: tonight)
    moved = {name: value for name, (value, _) in certify.declared().items()}
    assert (moved["engine_options_digest"], moved["published_engine_options_digest"],
            moved["encoder_budget_tokens"]) == ("sha256:" + "5" * 64,) * 2 + (32768,)
    monkeypatch.undo()
    pins = certify.serve_sh_pins()
    assert (pins["image"], pins["seqs"]) == (record["runtime_image"]["ref"],
                                             record["settings"]["ENGINE_MAX_NUM_SEQS"])
    marlin = tmp_path / "marlin2b"
    marlin.mkdir()
    (marlin / "serve.sh").write_text(
        (certify.MARLIN / "serve.sh").read_text()
        .replace(record["runtime_image"]["ref"], "vllm/vllm-openai:nightly")
        .replace("ENGINE_MAX_NUM_SEQS=${ENGINE_MAX_NUM_SEQS:-8}", "ENGINE_MAX_NUM_SEQS=${ENGINE_MAX_NUM_SEQS:-32}"))
    monkeypatch.setattr(certify, "MARLIN", marlin)
    monkeypatch.setattr(certify, "serving_record", lambda: record)
    drifted = certify.serve_sh_pins()
    assert (drifted["image"], drifted["seqs"]) == ("vllm/vllm-openai:nightly", "32")
    names = [problem.split(":")[0] for problem in certify.config_problems(certify.current_config())]
    assert {"runtime_image", "engine_max_num_seqs"} <= set(names), names


def test_e4b_the_deployed_engine_is_judged_from_the_box_inventory(tmp_path, monkeypatch):
    """The committed W3 inventory is the box as it ran (the pre-pin serve.sh): its engine
    does not run the pinned launch, which is exactly what the check reports. A deployed
    engine with the pinned image and exactly the served flags passes."""
    record = certify.serving_record()
    assert certify.inventory_problems(BOX_INVENTORY.read_text(), record) == [
        "pinned flags the engine does not run: [('--allowed-local-media-path', "
        "'/opt/dlami/nvme/processing'), ('--max-num-seqs', '8')]",
        "flags the engine runs beyond the pin: [('--max-num-seqs', '32')]"]
    good = ("### engine\nimage_equals_pin=yes\nargs="
            + json.dumps(["serve", "/model", *certify.served_flags(record)]) + "\n")
    assert certify.inventory_problems(good, record) == []
    assert certify.inventory_problems(good.replace("=yes", "=no"), record) == [
        "image_equals_pin=no"]
    assert certify.inventory_problems("image_equals_pin=yes\n", record) == [
        "no readable args= line"]
    declared = {name: value for name, (value, _) in certify.declared().items()}
    monkeypatch.setattr(certify, "current_config", lambda: dict(declared))
    inventory = tmp_path / "inventory.txt"
    inventory.write_text(good)
    report = certify.Report(TARGET)
    certify.config_pin_check(report, inventory)
    assert report.stages[-1]["status"] == certify.PASS
    certify.config_pin_check(report, BOX_INVENTORY)
    assert report.stages[-1]["status"] == certify.FAIL
    assert report.stages[-1]["detail"][0].startswith("deployed: pinned flags")


def _proc(root: Path, pid: int, argv: list[str], cwd: Path | None) -> None:
    entry = root / str(pid)
    entry.mkdir()
    (entry / "cmdline").write_bytes(b"\0".join(part.encode() for part in argv) + b"\0")
    if cwd is not None:
        (entry / "cwd").symlink_to(cwd)


def test_e4b_app_and_lab_servers_of_this_repository_fail_the_preconditions(tmp_path,
                                                                           monkeypatch):
    """Protocol §2 as review F7 corrected it: a Next.js server in an `apps/app` or `apps/lab`
    package - of any checkout, found by its working directory or its command line - is a
    running App or Lab, and one whose working directory cannot be read counts too (unknown
    is not stopped). A shell that merely names one, a Node server that is not Next.js, and
    a Next.js server of another project, are not."""
    repo, other, proc = tmp_path / "repo", tmp_path / "elsewhere", tmp_path / "proc"
    clone = tmp_path / "srv" / "clone"
    for path in (repo / "apps/app", repo / ".claude/worktrees/x/apps/lab", other, proc,
                 clone / "apps/app"):
        path.mkdir(parents=True)
    _proc(proc, 11, ["next-server (v16.3.5)"], repo / "apps/app")
    _proc(proc, 12, ["node", "/r/node_modules/.bin/../next/dist/bin/next", "dev"],
          repo / ".claude/worktrees/x/apps/lab")
    _proc(proc, 13, ["next-server (v16.3.5)"], other)
    _proc(proc, 14, ["bash", "-c", "pgrep -af next-server"], repo / "apps/app")
    _proc(proc, 15, ["node", "server.js", "start"], repo / "apps/app")
    _proc(proc, 16, ["next-server (v16.3.5)"], None)
    _proc(proc, 17, ["node", "/opt/checkout/apps/lab/node_modules/.bin/next", "start"], other)
    _proc(proc, 18, ["next-server (v16.3.5)"], clone / "apps/app")
    found = certify.next_servers(proc)
    assert [server["pid"] for server in found] == [11, 12, 16, 17, 18]
    assert found[2]["cwd"] is None
    report = certify.Report(TARGET)
    monkeypatch.setattr(certify, "next_servers", lambda: found)
    certify.preconditions_check(report, TARGET, box=False)
    assert report.stages[-1]["status"] == certify.FAIL
    assert "pid 11" in report.stages[-1]["detail"][0]
    assert "unknown is not stopped" in report.stages[-1]["detail"][2]
    monkeypatch.setattr(certify, "next_servers", lambda: [])
    certify.preconditions_check(report, TARGET, box=False)
    assert report.stages[-1]["status"] == certify.PASS
    # the box half: the window, an idle engine, the parity clips
    box = {**TARGET, "engine_url": "http://engine"}
    monkeypatch.setattr(certify, "client", lambda argv, env=None: {"exit": 0, "tail": ""})
    monkeypatch.setattr(certify, "scrape", lambda url: {"running": 0.0, "waiting": 0.0})
    monkeypatch.setenv("E4B_WINDOW_OK", "1")
    certify.preconditions_check(report, box, box=True)
    assert report.stages[-1]["status"] == certify.PASS
    monkeypatch.setattr(certify, "scrape", lambda url: {"running": 1.0, "waiting": 0.0})
    monkeypatch.delenv("E4B_WINDOW_OK")
    certify.preconditions_check(report, box, box=True)
    assert report.stages[-1]["detail"] == [
        "E4B_WINDOW_OK=1 (a logged maintenance window) is not set",
        "the engine is not idle (running+waiting = 1.0)"]


def _clips():
    return {"short": {"duration_s": 10.0, "width": 1280, "height": 720},
            "short1080": {"duration_s": 10.0, "width": 1920, "height": 1080},
            "long": {"duration_s": 60.0, "width": 1280, "height": 720},
            "band": {"duration_s": 78.0, "width": 640, "height": 360},
            "over": {"duration_s": 112.0, "width": 640, "height": 360}}


def _attempt(clip, outcome="accepted", *, status=200, ttft=1.0, latency=5.0, code=None,
             retry=None, error=None):
    return {"clip_id": clip, "outcome": outcome, "http_status": status, "ttft_s": ttft,
            "latency_s": latency, "error_code": code, "retry_after": retry,
            "error_class": error}


def _verdict(verdicts, name):
    (row,) = [row for row in verdicts if row[0] == name]
    return row[1]


def test_e4b_an_envelope_rung_judges_the_duration_cap_apart_from_its_failures():
    """Protocol §4: attempts beyond the engine ceiling are the cap's (refused at admission,
    never admitted and failed); a clip within the applied cap is never refused; platform
    failures are judged over the rest; each tail needs 60 samples and meets its limit."""
    clips = _clips()
    ok = [_attempt("short") for _ in range(60)] + [_attempt("long", latency=20.0)] * 3
    capped = [*ok, _attempt("over", "rejected", status=400, code="invalid_request")]
    verdicts = certify.rung_verdicts(capped, clips, gateway=True)
    assert {row[0]: row[1] for row in verdicts} == {
        "duration_cap": "pass", "failure_rate": "pass", "answered": "pass", "rejections": "pass",
        "ttft_p95_short": "pass", "e2e_p95_per_clip_minute": "pass"}
    engine_failed = [*ok, _attempt("over", "failed", error="stream_error_event")]
    assert _verdict(certify.rung_verdicts(engine_failed, clips, gateway=True),
                    "duration_cap") == "fail"
    assert _verdict(certify.rung_verdicts(engine_failed, clips, gateway=True),
                    "failure_rate") == "pass"
    refused = [*capped, _attempt("long", "rejected", status=400)]
    assert _verdict(certify.rung_verdicts(refused, clips, gateway=True), "duration_cap") == "fail"
    band = [*capped, _attempt("band", "rejected", status=400)]
    assert {row[1] for row in certify.rung_verdicts(band, clips, gateway=True)} == {"pass"}
    assert _verdict(certify.rung_verdicts(ok, clips, gateway=True), "duration_cap") == "unknown"
    assert certify.rung_verdicts(capped, clips, gateway=False)[0] == (
        "duration_cap", "unknown", "an engine target has no admission", "BOX")
    failing = [*ok[2:], *[_attempt("short", "failed", status=502, error="http_502")] * 2]
    assert _verdict(certify.rung_verdicts(failing, clips, gateway=True), "failure_rate") == "fail"
    busy = [*ok, _attempt("short", "rejected", status=429, code="capacity_exhausted", retry=2)]
    assert _verdict(certify.rung_verdicts(busy, clips, gateway=True), "rejections") == "fail"
    # the TTFT row's class is clips <= 30 s at <= 720p: long or 1080p clips are not in it
    for other in ("long", "short1080"):
        mixed = [*ok, *[_attempt(other, ttft=20.0)] * 5]
        assert _verdict(certify.rung_verdicts(mixed, clips, gateway=True),
                        "ttft_p95_short") == "pass", other
    slow = [_attempt("short", ttft=7.0) for _ in range(60)]
    assert _verdict(certify.rung_verdicts(slow, clips, gateway=True), "ttft_p95_short") == "fail"
    dragging = [_attempt("short", latency=8.0) for _ in range(60)]
    assert _verdict(certify.rung_verdicts(dragging, clips, gateway=True),
                    "e2e_p95_per_clip_minute") == "fail"
    few = certify.rung_verdicts(ok[:10], clips, gateway=True)
    assert _verdict(few, "ttft_p95_short") == _verdict(few, "e2e_p95_per_clip_minute") == \
        "unknown"


def test_e4b_an_unanswered_attempt_is_a_failure_whatever_its_cause():
    """Review F4 (PERF-ENVELOPE: raw denominators include every error): a timeout or a reset
    is a failed attempt like a 5xx, and a cell that accepted nothing supports no rate - an
    endpoint that answered nothing is never certified at a rate."""
    clips = _clips()
    ok = [_attempt("short") for _ in range(84)]
    timeouts = [_attempt("short", "failed", status=None, error="ReadTimeout")] * 36
    verdicts = certify.rung_verdicts([*ok, *timeouts], clips, gateway=True)
    assert _verdict(verdicts, "failure_rate") == "fail"
    assert ("failure_rate", "fail", "36/120 (0 platform-caused)", "BOX") in verdicts
    dead = [_attempt("short", "failed", status=None, error="ConnectError")] * 120
    nothing = certify.rung_verdicts(dead, clips, gateway=True)
    assert _verdict(nothing, "answered") == _verdict(nothing, "failure_rate") == "fail"
    assert certify.envelope_summary([(0.5, nothing), (1.0, nothing)]) == (certify.FAIL, (), None)
    only_capped = [_attempt("over", "rejected", status=400)] * 5
    unanswered = certify.rung_verdicts(only_capped, clips, gateway=True)
    assert _verdict(unanswered, "answered") == "fail"
    assert certify.envelope_summary([(0.5, unanswered)])[2] is None
    gave_up = certify.rung_verdicts([_attempt("short", "cancelled")] * 20, clips, gateway=True)
    assert _verdict(gave_up, "failure_rate") == "pass" and _verdict(gave_up, "answered") == "fail"
    assert certify.envelope_summary([(0.5, gave_up)]) == (certify.FAIL, (), None)
    flat = [{"rss_mib": 900.0, "gpu_used_mib": 40000.0, "drift": 0, "unsettleable": 0}] * 8
    soak = [dict(row, send_s=i) for i, row in enumerate([*ok[:30], *timeouts[:36]])]
    assert certify.summarise(certify.soak_verdicts(soak, flat, clips))[0] == certify.FAIL
    reset = _attempt("short", "failed", status=None, error="RemoteProtocolError")
    honest = [_attempt("short")] * 8 + [_attempt("short", "rejected", status=429,
                                                 code="capacity_exhausted", retry=2.0)] * 8
    assert "5xx or platform" in first(certify.overload_problems([*honest, reset], clips))


def test_e4b_the_supported_rate_is_the_highest_rung_climbing_from_the_lowest():
    """The envelope is contiguous from the bottom: a failing rung ends the climb, whatever
    passes above it; latency unknowns pend, and a cap failure on any rung fails the cell."""
    good = [("failure_rate", "pass", "", "BOX"), ("rejections", "pass", "", "BOX"),
            ("ttft_p95_short", "pass", "", "BOX"), ("duration_cap", "pass", "", "BOX")]
    bad = [("failure_rate", "fail", "", "BOX"), *good[1:]]
    assert certify.envelope_summary([(0.5, good), (1.0, bad), (2.0, good)]) == (
        certify.PASS, (), 0.5)
    assert certify.envelope_summary([(0.5, bad), (1.0, good)]) == (certify.FAIL, (), None)
    tail = [*good[:2], ("ttft_p95_short", "unknown", "", "BOX"), good[3]]
    assert certify.envelope_summary([(0.5, good), (1.0, tail)]) == (certify.PENDING, ("BOX",),
                                                                   1.0)
    crashed = [*good, ("client_exit", "fail", "exit 2", "BOX")]
    assert certify.envelope_summary([(0.5, good), (1.0, crashed), (2.0, good)])[2] == 0.5
    capped = [*good[:3], ("duration_cap", "fail", "", "BOX")]
    assert certify.envelope_summary([(0.5, good), (1.0, bad), (2.0, capped)])[0] == certify.FAIL


def test_e4b_the_soak_judges_memory_the_reconciler_and_latency_from_its_samples():
    """Protocol §4 soak: growth (second half over first, `decide.growth`) within W4's
    memory limits, a reconciled store at the end, the last third's p50 within 1.5x the
    first's; no samples is unknown, never a pass."""
    clips = _clips()
    rows = [dict(_attempt("short", latency=2.0), send_s=i) for i in range(18)]
    flat = [{"rss_mib": 900.0, "gpu_used_mib": 40000.0, "drift": 0, "unsettleable": 0}] * 8
    assert {row[0]: row[1] for row in certify.soak_verdicts(rows, flat, clips)} == {
        "failure_rate": "pass", "answered": "pass", "host_growth_mib": "pass",
        "gpu_growth_mib": "pass", "reconciled_at_end": "pass", "latency_drift": "pass"}
    leak = flat[:4] + [{**flat[0], "rss_mib": 1500.0}] * 4
    assert _verdict(certify.soak_verdicts(rows, leak, clips), "host_growth_mib") == "fail"
    vram = flat[:4] + [{**flat[0], "gpu_used_mib": 40300.0}] * 4
    assert _verdict(certify.soak_verdicts(rows, vram, clips), "gpu_growth_mib") == "fail"
    drifted = [*flat[:-1], {**flat[0], "drift": 2}]
    assert _verdict(certify.soak_verdicts(rows, drifted, clips), "reconciled_at_end") == "fail"
    slower = [dict(row, latency_s=2.0 if row["send_s"] < 12 else 3.5) for row in rows]
    assert _verdict(certify.soak_verdicts(slower, flat, clips), "latency_drift") == "fail"
    blind = certify.soak_verdicts(rows, [], clips)
    assert [_verdict(blind, name) for name in ("host_growth_mib", "gpu_growth_mib",
                                                 "reconciled_at_end")] == ["unknown"] * 3
    assert certify.summarise(blind) == (certify.PENDING, ("BOX",))
    assert _verdict(certify.soak_verdicts(rows[:17], flat, clips), "latency_drift") == "unknown"


def test_e4b_overload_refusals_are_429_with_retry_guidance_and_never_5xx():
    """Protocol §4 overload: something accepted, something refused, every refusal a 429 with
    a numeric Retry-After and an overload code; no 5xx, no broken stream. Clips over the
    applied cap answer for the duration cap, not for overload."""
    clips = _clips()
    honest = [_attempt("short")] * 8 + [_attempt("short", "rejected", status=429,
                                                 code="capacity_exhausted", retry=2.0)] * 24
    capped = [*honest, _attempt("over", "rejected", status=400, code="invalid_request")]
    assert certify.overload_problems(capped, clips) == []
    assert certify.overload_problems(honest[:8], clips) == [
        "nothing was refused: admission never reached its limit"]
    assert certify.overload_problems(honest[8:], clips) == ["nothing was accepted under the burst"]
    for bad in (dict(honest[-1], retry_after=None), dict(honest[-1], http_status=503),
                dict(honest[-1], error_code="dependency_unavailable")):
        assert "without 429" in first(certify.overload_problems([*honest, bad], clips))
    stream = _attempt("short", "failed", error="stream_error_event")
    assert "5xx or platform" in first(certify.overload_problems([*honest, stream], clips))


def test_e4b_scrape_reads_the_series_the_soak_judges(tmp_path):
    """One read of the gateway's /metrics (and the engine's): memory in MiB, the GPU's used
    memory only, the reconciler's drift and unsettleable jobs, vLLM's running and waiting."""
    page = tmp_path / "metrics"
    page.write_text("# TYPE infrx_process_resident_bytes gauge\n"
                    "infrx_process_resident_bytes 1073741824\n"
                    'infrx_gpu_memory_bytes{gpu="0",state="used"} 2097152\n'
                    'infrx_gpu_memory_bytes{gpu="0",state="total"} 48318382080\n'
                    "infrx_reconciliation_drift 0\ninfrx_unsettleable_jobs 1\n"
                    'vllm:num_requests_running{model_name="marlin2b"} 2\n'
                    'infrx_build_info{process="gateway",revision="0123abc"} 1\n')
    assert certify.scrape(page.as_uri()) == {"rss_mib": 1024.0, "gpu_used_mib": 2.0,
                                             "drift": 0.0, "unsettleable": 1.0,
                                             "running": 2.0, "waiting": None,
                                             "revision": "0123abc"}
    assert certify.scrape((tmp_path / "absent").as_uri()) is None


def test_e4b_the_load_cells_run_the_declared_shapes_and_pend_where_they_cannot_judge(
        tmp_path, monkeypatch):
    """The cells as the protocol shapes them: one envelope run per rate, the soak at its
    rate, and overload only against a gateway - an engine target pends it on the cutover."""
    seen = []

    def fake_client(argv, env=None):
        name = Path(argv[argv.index("--raw") + 1]).name
        seen.append((name, float(argv[argv.index("--rate") + 1]),
                     int(argv[argv.index("--requests") + 1])))
        rows = [dict(_attempt("c039-bbb1080p30-1080-square"), send_s=i) for i in range(20)]
        if name.startswith("overload"):
            rows = [_attempt("c039-bbb1080p30-1080-square")] + [
                _attempt("c039-bbb1080p30-1080-square", "rejected", status=429,
                         code="rate_limited", retry=1.0)]
        Path(argv[argv.index("--raw") + 1]).write_text(
            "".join(json.dumps({**row, "item_key": f"k{i}"}) + "\n" for i, row in enumerate(rows)))
        return {"exit": 0, "tail": ""}
    monkeypatch.setattr(certify, "client", fake_client)
    engine = {**TARGET, "bench_target": "direct", "base_url": "http://e/v1", "model": "m"}
    report = certify.Report(engine)
    certify.load_cells(report, engine, tmp_path, None)
    tiny = certify.MATRIX["tiny"]
    assert seen == [(f"envelope-r{tiny['envelope']['rates'][0]}-raw.jsonl",
                     tiny["envelope"]["rates"][0], tiny["envelope"]["requests"]),
                    ("soak-raw.jsonl", tiny["soak"]["rate"],
                     round(tiny["soak"]["rate"] * tiny["soak"]["seconds"]))]
    assert [(e["stage"], e["status"], e["owners"]) for e in report.stages] == [
        ("e4b.b.envelope", certify.PENDING, ["BOX"]),
        ("e4b.b.soak", certify.PENDING, ["BOX"]),
        ("e4b.b.overload", certify.PENDING, ["BOX"])]
    monkeypatch.setattr(certify, "client", lambda argv, env=None: {
        **fake_client(argv, env), "exit": 2 if "soak-raw.jsonl" in argv[argv.index("--raw") + 1]
        else 0})
    report = certify.Report(engine)
    certify.load_cells(report, engine, tmp_path, None)
    assert report.stages[1]["stage"] == "e4b.b.soak" and report.stages[1]["status"] == certify.FAIL
    monkeypatch.setattr(certify, "client", fake_client)
    gateway = {**engine, "bench_target": "gateway", "scale": "box", "label": certify.MEAS}
    seen.clear()
    certify.load_cells(report, gateway, tmp_path, None)
    box = certify.MATRIX["box"]
    assert [name for name, *_ in seen] == [
        *(f"envelope-r{rate}-raw.jsonl" for rate in box["envelope"]["rates"]), "soak-raw.jsonl",
        "overload-raw.jsonl"]
    assert seen[-2][1] == box["envelope"]["rates"][-1] * box["soak"]["rate_fraction"]
    assert seen[-1][2] == box["overload"]["burst"]
    assert report.stages[-1]["status"] == certify.PASS


def test_e4b_a_runner_error_is_a_recorded_failure_and_the_report_is_still_written(
        tmp_path, monkeypatch, clean_tree):
    """A run that dies before writing its JSON is no evidence (E3B phase 2's lost report):
    an unexpected error becomes a FAIL entry, the report is written, the exit is 1."""
    def broken():
        raise RuntimeError("the serving record is unreadable")
    monkeypatch.setattr(certify, "release_hashes", broken)
    out = tmp_path / "report.json"
    try:
        code = certify.main(["--no-stack", "--workdir", str(tmp_path), "--report", str(out)])
    except RuntimeError:
        code = None
    assert code == 1
    doc = json.loads(out.read_text())
    assert doc["exit_code"] == 1
    assert [(s["stage"], s["status"]) for s in doc["stages"]] == [
        ("runner-error", certify.FAIL), ("release-identity", certify.PASS)]
    assert "unreadable" in doc["stages"][0]["detail"]


def test_e4b_a_host_without_git_writes_a_report_that_fails_its_identity(tmp_path, monkeypatch):
    """Review F2: the runtime image has no git. The runner must still write its report, with
    the unknown tree recorded as unknown - which fails the release identity - and a box run
    must name the release it certifies, which the checkout's own SHA is compared with."""
    nogit = tmp_path / "bin"
    nogit.mkdir()
    monkeypatch.setenv("PATH", str(nogit))
    try:
        head = certify.run.git_head()
    except OSError as crashed:
        pytest.fail(f"no git crashed git_head: {crashed!r}")
    assert head == {"sha": None, "dirty": None}
    monkeypatch.setattr(certify, "release_hashes", lambda: {})
    monkeypatch.setattr(certify, "preconditions_check",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("stop before the engine")))
    out = tmp_path / "report.json"
    try:
        code = certify.main(["--no-stack", "--workdir", str(tmp_path), "--report", str(out)])
    except OSError as crashed:
        pytest.fail(f"no git crashed the runner: {crashed!r}")
    doc = json.loads(out.read_text())
    assert code == doc["exit_code"] == 1 and doc["git_head"] == {"sha": None, "dirty": None}
    assert doc["stages"][-1] == {**doc["stages"][-1], "stage": "release-identity",
                                 "status": certify.FAIL}
    assert "no git SHA at the start of the run" in doc["stages"][-1]["detail"]
    assert certify.identity_problems(CLEAN, CLEAN, release_sha="e" * 40) == [
        f"the tree is {'c' * 40}, not the release {'e' * 40}"]
    assert certify.identity_problems(CLEAN, CLEAN, release_sha="c" * 40) == []
    with pytest.raises(SystemExit):
        certify.main(["--box", "--target", "http://gw/v1", "--engine-url", "http://engine",
                      "--report", str(out)])


def test_e4b_the_box_report_is_tied_to_the_build_the_gateway_serves(monkeypatch):
    """Review F3: on the box the report's hashes are the release the endpoint serves - the
    gateway's own `infrx_build_info` revision is the report's tree, and it runs the image
    install.sh built for that release. Anything unknown fails; nothing is typed in."""
    sha, image = "0123abc" + "0" * 33, "sha256:" + "1" * 64
    served = {"revision": "0123abc"}
    assert certify.served_build_problems(served, sha, image, image) == []
    assert certify.served_build_problems({"revision": "9999999"}, sha, image, image) == [
        f"the gateway serves 9999999, the report's tree is {sha}"]
    assert certify.served_build_problems(served, None, image, image) == [
        "the gateway serves 0123abc, the report's tree is None"]
    assert "publishes no infrx_build_info" in first(
        certify.served_build_problems({"revision": None}, sha, image, image))
    assert "unreadable" in first(certify.served_build_problems(None, sha, image, image))
    assert certify.served_build_problems(served, sha, None, image) == [
        "INFRX_CERTIFY_GATEWAY_IMAGE is unset: the serving image is unrecorded"]
    assert certify.served_build_problems(served, sha, image, None) == [
        "INFRX_CERTIFY_RELEASE_IMAGE is unset: the release image is unrecorded"]
    other = "sha256:" + "2" * 64
    assert certify.served_build_problems(served, sha, other, image) == [
        f"the gateway runs {other}, not the release image {image}"]
    monkeypatch.setattr(certify.run, "git_head", lambda: {"sha": sha, "dirty": False})
    monkeypatch.setattr(certify, "scrape", lambda url: served)
    monkeypatch.setenv("INFRX_CERTIFY_GATEWAY_IMAGE", image)
    monkeypatch.setenv("INFRX_CERTIFY_RELEASE_IMAGE", image)
    report = certify.Report(TARGET)
    certify.served_build_check(report, "http://gw/metrics")
    assert (report.stages[-1]["stage"], report.stages[-1]["status"]) == ("e4b.b.served-build",
                                                                         certify.PASS)
    monkeypatch.delenv("INFRX_CERTIFY_GATEWAY_IMAGE")
    certify.served_build_check(report, "http://gw/metrics")
    assert report.stages[-1]["status"] == certify.FAIL
    with pytest.raises(SystemExit):
        certify.main(["--box", "--release-sha", sha, "--target", "http://gw/v1",
                      "--engine-url", "http://engine"])


def test_e4b_only_a_box_run_with_its_preconditions_met_is_a_measurement(tmp_path, monkeypatch,
                                                                          clean_tree):
    """Review F5: the local target's numbers are the fake engine's; a `--target` run is
    unverified unless it is the box run with every precondition met - never `meas.` by
    default."""
    import argparse
    assert certify.local_target("tiny", 1)["label"] == certify.FAKE
    remote = certify.remote_target(argparse.Namespace(
        target="http://gw/v1", engine_url="http://engine", scale=None, box=True))
    assert remote["label"] == certify.UNVERIFIED
    assert certify.target_label(remote, True, certify.PASS) == certify.MEAS
    assert certify.target_label(remote, True, certify.FAIL) == certify.UNVERIFIED
    assert certify.target_label(remote, False, certify.PASS) == certify.UNVERIFIED
    assert certify.target_label({"kind": "local"}, True, certify.PASS) == certify.FAKE
    seen = {}
    monkeypatch.setattr(certify, "release_hashes", lambda: {})
    monkeypatch.setattr(certify, "published_release", lambda: {"requested_model": "m"})
    monkeypatch.setattr(certify, "config_pin_check", lambda *a: None)
    monkeypatch.setattr(certify, "served_build_check", lambda *a: None)
    monkeypatch.setattr(certify, "engine_checks",
                        lambda report, target, workdir, args: seen.update(
                            label=target["label"], reported=report.target["label"]))
    box = ["--no-stack", "--box", "--release-sha", CLEAN["sha"], "--target", "http://gw/v1",
           "--engine-url", "http://engine", "--metrics-url", "http://gw/metrics",
           "--workdir", str(tmp_path)]
    for ready, label in ((certify.PASS, certify.MEAS), (certify.FAIL, certify.UNVERIFIED)):
        monkeypatch.setattr(certify, "preconditions_check",
                            lambda report, target, box_run, ready=ready: report.check(
                                "e4b.b.preconditions", ready, "stub"))
        certify.main(box)
        assert seen == {"label": label, "reported": label}, (ready, seen)


def test_e4b_each_stated_client_rule_holds_one_assertion_each(tmp_path, monkeypatch):
    """Review F8, one assertion per stated rule: no retries (a retry may not hide a refusal)
    and the full corpus at the box scale; a quarantined 4xx item is terminal and never
    re-sent; an accepted item without an Inference-Id is unreconcilable; a signal the client
    answered with exit 0 was no interruption; an engine whose metrics cannot be read is not
    idle."""
    import argparse
    local = certify.local_target("tiny", 1)
    box = certify.remote_target(argparse.Namespace(target="http://gw/v1", engine_url="http://e",
                                                   scale=None, box=True))
    monkeypatch.setattr(certify, "published_release", lambda: {"requested_model": "m"})
    for target, subset in ((local, "fast"), (box, "full")):
        argv = certify.bench_argv(target, tmp_path, "cell", rate=1.0, requests=4,
                                  dataset_version="v")
        assert argv[argv.index("--retries") + 1] == "0"
        assert argv[argv.index("--subset") + 1] == subset, target["scale"]
    quarantined = _row("i7", "rejected", status=400)
    assert certify.resume_problems([*FIRST, quarantined], [*SECOND, _row("i7")], items=7,
                                   first_interrupted=True) == [
        "terminal items re-sent by the resume: ['i7']"]
    blank = [dict(row, inference_id=None) if row["item_key"] == "i5" else row for row in ROWS]
    assert first(certify.reconcile_problems(blank, USAGE, HOLDS, BEFORE, AFTER)) == (
        "items accepted as more than one job (or with no Inference-Id): ['i5']")
    monkeypatch.setattr(certify, "interrupted_run", lambda argv, raw, **_: (
        raw.write_text("".join(json.dumps(r) + "\n" for r in FIRST)),
        {"exit": 0, "signalled": True})[1])
    monkeypatch.setattr(certify, "client", lambda argv, env=None: (
        Path(argv[argv.index("--raw") + 1]).write_text(
            "".join(json.dumps(r) + "\n" for r in SECOND)), {"exit": 0, "tail": ""})[1])
    monkeypatch.setitem(certify.MATRIX["tiny"], "dataset",
                        {"items": 6, "interrupt_after": 2, "rate": 4.0})
    report = certify.Report(TARGET)
    certify.dataset_check(report, {**local, "base_url": "http://e/v1"}, tmp_path)
    assert report.stages[-1]["status"] == certify.FAIL
    assert "not interrupted" in first(report.stages[-1]["detail"])
    monkeypatch.setenv("E4B_WINDOW_OK", "1")
    monkeypatch.setattr(certify, "next_servers", lambda: [])
    monkeypatch.setattr(certify, "client", lambda argv, env=None: {"exit": 0, "tail": ""})
    for unreadable in (None, {"running": None, "waiting": None}):
        monkeypatch.setattr(certify, "scrape", lambda url, answer=unreadable: answer)
        certify.preconditions_check(report, {**TARGET, "engine_url": "http://e"}, box=True)
        assert report.stages[-1]["detail"][0] == "the engine is not idle (running+waiting = None)"

if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
