#!/usr/bin/env python3
"""H1: versioned prompt harnesses and bounded replay (HARNESS-SAFE, EVAL-REPRO).

Driven by the F3 fixtures (`packages/shared/contracts/lab/fixtures.json`): the harness
revision and evaluation run below are the contract's accepted examples. L2's rights port
is a fake here (integration dependency L2; the real check is its RPC).

    uv run --frozen pytest -q tests/h/test_harness.py
"""
from __future__ import annotations

import copy
import json
import pathlib
from datetime import datetime, timezone

import pytest
from infrx.contracts import errors
from infrx.contracts.lab import records as lab
from infrx.contracts.v2 import records as v2
from infrx.harnesses import replay as h

REPO = pathlib.Path(__file__).resolve().parents[4]
FIXTURES = json.loads((REPO / "packages/shared/contracts/lab/fixtures.json").read_text())
HARNESS, RUN = FIXTURES["accepted"]["harness_revision"], FIXTURES["accepted"]["eval_run"]
A, B = HARNESS["provider_org_id"], "22222222-2222-4222-8222-222222222222"
UID = "33333333-3333-4333-8333-333333333333"
NOW = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)
ROOMY = h.Bounds(max_requests=50, max_bytes=100_000, max_seconds=60.0)
CASE = {"sample": {"text": "two blue mugs"}}


def harness(**changes):
    return {**copy.deepcopy(HARNESS), **changes}


def deployment(environment=v2.Environment.dev):
    return v2.DeploymentRevision(deployment_revision_id=UID, endpoint_id=UID, provider_org_id=A,
                                 serving_version_id=UID, environment=environment,
                                 visibility=v2.Visibility.private,
                                 state=v2.DeploymentState.ready_private, max_input_tokens=4096,
                                 max_output_tokens=512, created_at=NOW)


class Model:
    """A scripted dev endpoint: answers in order and records every call it receives."""

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, prompt, media, tool_results):
        self.calls.append((prompt, media, tool_results))
        return self.answers.pop(0)


class Rights:
    """The L2 rights port as a fake: one provider holds a grant for one dataset."""

    def __init__(self, provider=A, dataset=RUN["dataset_ref"]):
        self.allowed, self.asked = (provider, dataset), []

    def authorize(self, gate, *, provider_org_id, dataset_ref):
        self.asked.append((gate, provider_org_id, dataset_ref))
        if (provider_org_id, dataset_ref) != self.allowed:
            raise errors.Forbidden("no current grant")


def replayer(model, payload=None, recordings=None, bounds=ROOMY, clock=None, env=v2.Environment.dev):
    return h.Replayer(payload or harness(), deployment(env), model, recordings or {}, bounds,
                      **({"clock": clock} if clock else {}))


CALL = {"name": "lookup_sku", "arguments": {"q": "mug"}}
DONE = {"text": '{"sku": "MUG-2", "qty": 2}', "tool_calls": []}


# --- H1.a: identity ---------------------------------------------------------------------------
def test_h1_a_prompt_or_processor_change_is_a_new_identity():
    base = h.revision_ref(harness())
    assert h.revision_ref(harness()) == base
    assert h.revision_ref(harness(prompt_template="Extract JSON: {{input}}")) != base
    assert h.revision_ref(harness(processor_profile="marlin-sop-2")) != base
    assert h.revision_ref(harness(tools=[])) != base
    assert base.startswith(f"lab:harness:{A}:{HARNESS['harness_id']}@sha256:")


def test_h1_arbitrary_code_is_never_a_harness():
    for bad in (harness(adapter="python"), {**harness(), "processor_code": "import os"}):
        with pytest.raises(lab.LabRejected) as refused:
            h.revision_ref(bad)
        assert refused.value.reason == "invalid"
    with pytest.raises(lab.LabRejected):                     # another record is not a harness
        h.revision_ref(RUN)


# --- H1.c: binding into the F3 manifest ---------------------------------------------------------
def test_h1_binding_pins_the_revision_and_checks_the_purpose():
    rights = Rights()
    bound = h.bind(RUN, h.revision_ref(harness()), rights=rights)
    assert bound["harness_ref"] == h.revision_ref(harness()) and lab.validate(bound) is None
    assert rights.asked == [(lab.Gate.schedule, A, RUN["dataset_ref"])]
    assert RUN["harness_ref"] != bound["harness_ref"]                  # the input is not edited


def test_h1_a_mutable_harness_ref_is_rejected():
    label = h.revision_ref(harness()).split("@")[0] + "@latest"
    rights = Rights()
    with pytest.raises(lab.LabRejected) as refused:
        h.bind(RUN, label, rights=rights)
    assert refused.value.reason == "mutable_ref" and rights.asked == []


def test_h1_a_cross_provider_harness_fails_the_purpose_check():
    foreign = h.revision_ref(harness(provider_org_id=B))
    rights = Rights()
    with pytest.raises(lab.LabRejected) as refused:
        h.bind(RUN, foreign, rights=rights)
    assert refused.value.reason == "cross_provider_ref" and rights.asked == []
    with pytest.raises(errors.Forbidden):                               # no grant: L2 says no
        h.bind(RUN, h.revision_ref(harness()), rights=Rights(provider=B))


# --- H1.b: replay -------------------------------------------------------------------------------
def test_h1_replay_answers_tools_only_from_recordings():
    model = Model({"text": "", "tool_calls": [CALL]}, DONE)
    recorded = {h.recording_key(CALL["name"], CALL["arguments"]): {"sku": "MUG-2"}}
    outcome = replayer(model, recordings=recorded).replay(CASE)
    assert (outcome.status, outcome.output, outcome.reasons) == ("complete", {"sku": "MUG-2", "qty": 2}, ())
    assert model.calls[0][0] == "Extract the order as JSON: two blue mugs"
    assert model.calls[1][2] == [{"name": "lookup_sku", "result": {"sku": "MUG-2"}}]
    assert outcome.comparable
    other = {h.recording_key(CALL["name"], {"q": "cup"}): {"sku": "CUP-1"}}   # other arguments
    model = Model({"text": "", "tool_calls": [CALL]}, DONE)
    assert replayer(model, recordings=other).replay(CASE).reasons == ("missing_recording:lookup_sku",)


def test_h1_a_missing_recording_is_unsupported_not_executed():
    model = Model({"text": "", "tool_calls": [CALL]}, DONE)
    outcome = replayer(model).replay(CASE)
    assert outcome.status == "unsupported" and outcome.reasons == ("missing_recording:lookup_sku",)
    assert len(model.calls) == 1 and not outcome.comparable


@pytest.mark.parametrize("effect", ["network_mutation", "actuator"])
def test_h1_a_mutating_tool_or_actuator_is_blocked(effect):
    tools = [{"name": "lookup_sku", "effect": effect, "input_schema": {"type": "object"}}]
    model = Model({"text": "", "tool_calls": [CALL]}, DONE)
    recorded = {h.recording_key(CALL["name"], CALL["arguments"]): {"ok": True}}
    outcome = replayer(model, harness(tools=tools), recorded).replay(CASE)
    assert outcome.status == "blocked" and outcome.reasons == (f"{effect}:lookup_sku",)
    assert len(model.calls) == 1 and not outcome.comparable


def test_h1_an_undeclared_tool_is_unsupported():
    model = Model({"text": "", "tool_calls": [{"name": "send_email", "arguments": {}}]}, DONE)
    outcome = replayer(model).replay(CASE)
    assert outcome.status == "unsupported" and outcome.reasons == ("undeclared_tool:send_email",)


@pytest.mark.parametrize("bound,limits", [
    ("requests", h.Bounds(max_requests=1, max_bytes=100_000, max_seconds=60.0)),
    ("bytes", h.Bounds(max_requests=50, max_bytes=60, max_seconds=60.0)),
    ("seconds", h.Bounds(max_requests=50, max_bytes=100_000, max_seconds=1.0)),
])
def test_h1_a_bound_exceeded_stops_the_run(bound, limits):
    ticks = iter([0.0, 0.5, 2.0, 2.0, 2.0])
    model = Model({"text": "", "tool_calls": [CALL]}, DONE)
    recorded = {h.recording_key(CALL["name"], CALL["arguments"]): {"sku": "MUG-2"}}
    run = replayer(model, recordings=recorded, bounds=limits, clock=lambda: next(ticks))
    with pytest.raises(h.ReplayBoundExceeded) as stopped:
        run.replay(CASE)
    assert stopped.value.bound == bound and len(model.calls) == 1
    with pytest.raises(h.ReplayBoundExceeded):                     # and stays stopped
        run.replay(CASE)
    assert len(model.calls) == 1


def test_h1_replay_only_targets_dev_endpoints():
    model = Model(DONE)
    with pytest.raises(errors.Forbidden):
        replayer(model, env=v2.Environment.prod)
    assert model.calls == []


def test_h1_a_harness_changed_mid_run_is_refused():
    payload = harness()
    model = Model(DONE, DONE)
    run = replayer(model, payload)
    assert run.replay(CASE).status == "complete"
    payload["prompt_template"] = "Now say something else: {{input}}"
    with pytest.raises(errors.StateConflict):
        run.replay(CASE)
    assert len(model.calls) == 1


def test_h1_adapters_are_built_in_and_bounded():
    text = replayer(Model({"text": "plain", "tool_calls": []}), harness(adapter="text", tools=[]))
    assert text.replay(CASE).output == "plain"
    structured = replayer(Model({"text": "not json", "tool_calls": []}))
    assert structured.replay(CASE).status == "failed"            # a scored failure, comparable
    video = harness(adapter="finite_video", tools=[], input_mapping={"input": "sample.caption"})
    model = Model({"text": "a clip", "tool_calls": []})
    clip = {"sample": {"caption": "c", "media_ref": "upl_x", "duration_ms": 82_000}}
    assert replayer(model, video).replay(clip).status == "complete" and model.calls[0][1] == ["upl_x"]
    long = replayer(Model(DONE), video).replay({"sample": {**clip["sample"], "duration_ms": 82_001}})
    assert long.status == "unsupported" and long.reasons == ("video_over_cap",)
    missing = replayer(Model(DONE)).replay({"sample": {}})
    assert missing.status == "unsupported" and missing.reasons == ("missing_input:sample.text",)


def test_h1_coverage_reports_every_unsupported_case():
    outcomes = [h.Outcome("complete", 1, ()), h.Outcome("failed", None, ()),
                h.Outcome("unsupported", None, ("missing_recording:a",)),
                h.Outcome("unsupported", None, ("missing_recording:a",)),
                h.Outcome("blocked", None, ("actuator:arm",))]
    assert h.coverage(outcomes) == {"cases": 5, "comparable": 2,
                                    "not_comparable": {"missing_recording:a": 2, "actuator:arm": 1}}


# --- H1.c: comparisons --------------------------------------------------------------------------
def run_with(**changes):
    return {**copy.deepcopy(RUN), **changes}


def test_h1_a_prompt_only_variant_is_a_single_factor_comparison():
    variant = run_with(harness_ref=h.revision_ref(harness(prompt_template="Extract: {{input}}")))
    assert h.compare(RUN, variant) == ("single_factor", ("harness_ref",))
    assert h.compare(RUN, run_with(seed=8)) == ("single_factor", ("seed",))
    assert h.compare(RUN, run_with()) == ("rerun", ())


def test_h1_multifactor_needs_an_explicit_tag_and_the_same_universe():
    both = run_with(seed=8, serving_ref=RUN["serving_ref"][:-1] + "0")
    with pytest.raises(errors.InvalidRequest):
        h.compare(RUN, both)
    assert h.compare(RUN, both, multifactor_tag="quant+prompt") == ("multifactor", ("serving_ref", "seed"))
    for field in ("dataset_ref", "evaluator_ref"):
        with pytest.raises(errors.InvalidRequest):
            h.compare(RUN, run_with(**{field: RUN[field][:-1] + "0"}), multifactor_tag="x")
