#!/usr/bin/env python3
"""AP-10 10d: `infrx.lab.improve.sop` - the SOP benchmark's report over a scripted endpoint
(httpx.MockTransport; the real fake-engine process is `test_sop_fake_engine.py`).

    uv run --frozen pytest -q tests/ap10/test_sop_benchmark.py
"""
from __future__ import annotations

import asyncio
import json
import pathlib

import httpx
import pytest

from infrx.lab.improve import sop

MODEL = "marlin2b"
EVENTS = "Scene: a work cell.\nEvents: <0.0 - 2.0> pick part\n<2.5 - 4.0> place part"


def manifest(tmp: pathlib.Path, n: int = 3, media: bool = True) -> pathlib.Path:
    (tmp / "clip.mp4").write_bytes(b"\x00not-a-real-video")
    lines = [{"id": f"item-{k}", "video": "clip.mp4" if media or k else None,
              "prompt": f"p{k}", "max_tokens": 64, "start_s": 0, "end_s": 8}
             for k in range(n)]
    path = tmp / "items.jsonl"
    path.write_text("".join(json.dumps(x) + "\n" for x in lines))
    return path


def endpoint(answers: dict[str, object], *, fake: bool = False, model: str = MODEL):
    """`answers[prompt]`: the content text, an int status, or a whole reply dict."""
    seen: list[dict] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/_control":
            return httpx.Response(200 if fake else 404, json={})
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": model}]})
        body = json.loads(request.content)
        seen.append(body)
        answer = answers[body["messages"][0]["content"][1]["text"]]
        if isinstance(answer, int):
            return httpx.Response(answer, json={"error": {"code": "x"}})
        if isinstance(answer, dict):
            return httpx.Response(200, json=answer)
        return httpx.Response(200, json={
            "model": model, "choices": [{"message": {"content": answer}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handle),
                               base_url="http://candidate/v1/")
    return client, seen


def bench(client, path, *, target="candidate", **kw) -> dict:
    return asyncio.run(sop.benchmark(client, path, dataset_version="ds-1", model=MODEL,
                                     serving_revision="rev-1", seed=7, target=target, **kw))


def definition(**kw) -> sop.Definition:
    return sop.Definition.model_validate({"sop_id": "pick-place", "version": 1,
                                          "review_ref": "sop-review-1", "tolerance_s": 0.5,
                                          "steps": ["pick", "place"], **kw})


def gold(path: pathlib.Path, labels: dict, **kw) -> sop.GoldSet:
    return sop.GoldSet.model_validate({
        "dataset_version": "ds-1", "manifest_sha256": sop.sha256(path.read_bytes()),
        "provenance": "human_reviewed", "reviewed_by": "reviewer-1", "review_ref": "gold-1",
        "labels": labels, **kw})


def test_ap10_sop_the_report_pins_identity_and_lists_every_failure_and_abstention(tmp_path):
    """Oracle: one request per sent item with temperature 0 and the run's seed; the report
    pins the dataset (version, manifest digest, case digest), the model/serving revision,
    harness, parser and seed; a 5xx, a malformed answer and an answer from another model are
    failures by class, an item without media is never sent (`no_media`) and an answer with
    no timed event is `insufficient_evidence` - each listed by id. A run of the same inputs
    is the same report but for its timings."""
    path = manifest(tmp_path, 7, media=False)
    other = {"model": "someone-else", "choices": [{"message": {"content": EVENTS}}],
             "usage": {}}
    answers = {"p1": EVENTS, "p2": 503, "p3": {"choices": []}, "p4": "no times here",
               "p5": other, "p6": "From 1.0 to 2.5."}
    client, seen = endpoint(answers)
    got = bench(client, path)
    assert [b["messages"][0]["content"][1]["text"] for b in seen] == \
        ["p1", "p2", "p3", "p4", "p5", "p6"]
    assert {(b["temperature"], b["seed"], b["model"]) for b in seen} == {(0, 7, MODEL)}
    assert (got["harness"], got["parser"], got["seed"]) == (sop.HARNESS, sop.PARSER, 7)
    assert got["dataset"] == {"version": "ds-1", "items": 7,
                              "manifest_sha256": sop.sha256(path.read_bytes()),
                              "cases_sha256": sop.sha256("\n".join(
                                  f"item-{k}" for k in range(7)).encode())}
    assert got["model"] == {"requested": MODEL, "serving_revision": "rev-1",
                            "listed": [MODEL], "target": "candidate"}
    assert got["failures"] == [{"id": "item-2", "reason": "http_503"},
                               {"id": "item-3", "reason": "malformed_answer"},
                               {"id": "item-5", "reason": "identity_mismatch"}]
    assert got["abstentions"] == [{"id": "item-0", "reason": "no_media"},
                                  {"id": "item-4", "reason": "insufficient_evidence"}]
    rows = {r["id"]: r for r in got["items"]}
    assert rows["item-1"]["events"] == [(0.0, 2.0), (2.5, 4.0)]
    assert rows["item-6"]["events"] == [(1.0, 2.5)]
    again = bench(endpoint(answers)[0], path)
    strip = lambda r: {**r, "items": [{k: v for k, v in i.items() if k != "latency_s"}
                                      for i in r["items"]], "performance": None}
    assert strip(again) == strip(got)


def test_ap10_sop_quality_is_blocked_on_p07_and_validity_is_counted_apart(tmp_path):
    """Oracle: without the SOP definition or the gold set the quality verdict is
    BLOCKED[P-07] carrying no agreement number; teacher labels, a gold set of another
    manifest or one naming unknown items stay BLOCKED; output validity (answers with timed
    events) is counted in every case, apart from task agreement."""
    path = manifest(tmp_path, 3)
    client, _ = endpoint({"p0": EVENTS, "p1": "nothing timed", "p2": 500})
    spans = {"item-0": [{"start_s": 0, "end_s": 2}]}
    cases = [(None, None, "P-07"), (definition(), None, "P-07"),
             (None, gold(path, spans), "P-07"),
             (definition(), gold(path, spans, provenance="teacher"), "teacher labels"),
             (definition(), gold(path, spans, manifest_sha256="0" * 64), "another dataset"),
             (definition(), gold(path, {"item-9": [{"start_s": 0, "end_s": 1}]}),
              "does not have")]
    for d, g, reason in cases:
        q = bench(client, path, definition=d, gold=g)["quality"]
        assert q["verdict"] == "BLOCKED" and reason in q["blocked"], (reason, q)
        assert set(q) == {"verdict", "blocked", "output_validity"}, q
        assert q["output_validity"] == {"answers": 2, "with_timed_events": 1}


def test_ap10_sop_agreement_is_computed_only_from_reviewed_gold_within_tolerance(tmp_path):
    """Oracle: with a definition and a human-reviewed gold set of this manifest, each gold
    span takes one prediction, in order, whose start AND end are within the tolerance;
    unmatched gold is missed, unmatched predictions hallucinated, and a gold item without an
    answer counts all its spans missed and is listed."""
    path = manifest(tmp_path, 3)
    client, _ = endpoint({"p0": EVENTS, "p1": "Events: <0.0 - 9.0> x", "p2": 500})
    labels = {"item-0": [{"start_s": 0.4, "end_s": 2.4}, {"start_s": 0.2, "end_s": 2.2},
                         {"start_s": 2.5, "end_s": 4.6}, {"start_s": 7, "end_s": 8}],
              "item-1": [{"start_s": 0.0, "end_s": 1.0}],
              "item-2": [{"start_s": 1, "end_s": 2}]}
    q = bench(client, path, definition=definition(), gold=gold(path, labels))["quality"]
    assert q["verdict"] == "COMPUTED"
    # item-0: the first span matches; the second would match only the prediction the first
    # took (one-to-one: missed); the third's end is off by 0.6 > 0.5 (missed, its prediction
    # hallucinated); the fourth has none. item-1: end off: missed + hallucinated. item-2
    # failed: its one span missed.
    assert (q["matched"], q["missed"], q["hallucinated"]) == (1, 5, 2), q
    assert (q["precision"], q["recall"]) == (round(1 / 3, 6), round(1 / 6, 6))
    assert q["unanswered_gold_items"] == ["item-2"]
    assert (q["sop"]["version"], q["gold"]["items"]) == (1, 3)


@pytest.mark.parametrize("fake_surface,target,label", [
    (False, "candidate", "meas."), (True, "candidate", "fake"), (False, "fake", "fake")])
def test_ap10_sop_performance_is_meas_only_for_a_declared_candidate_without_the_fake_surface(
        tmp_path, fake_surface, target, label):
    """Oracle: the fake engine (its control surface answers) or a declared fake target is
    labelled `fake`, never `meas.`; latency percentiles are refused below 6 (p50) / 60 (p95)
    samples rather than guessed."""
    path = manifest(tmp_path, 6)
    client, _ = endpoint({f"p{k}": EVENTS for k in range(6)}, fake=fake_surface)
    perf = bench(client, path, target=target)["performance"]
    assert perf["label"] == label
    assert (perf["answered"], perf["failed"], perf["abstained"]) == (6, 0, 0)
    assert perf["latency_s"]["n"] == 6 and perf["latency_s"]["p50"] is not None
    assert perf["latency_s"]["p95"] is None
    assert (perf["prompt_tokens"], perf["completion_tokens"], perf["video_s"]) == (60, 30, 48)
    short = manifest(tmp_path, 5)
    client, _ = endpoint({f"p{k}": EVENTS for k in range(5)})
    assert bench(client, short)["performance"]["latency_s"]["p50"] is None
