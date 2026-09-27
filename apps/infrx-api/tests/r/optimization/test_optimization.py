#!/usr/bin/env python3
"""R3: register and compare optimized serving variants (OPT-PARITY). Fake-only.

The engine is W3's real `VllmEngine` over an `httpx.MockTransport` that answers `/version`
and `/v1/models`; the run pairing is H1's real `compare`. A B2 report is built in B2's
`infrx.eval_report.1` shape (codex/w5-eval-runner d87408fc, not on this base). The load
records are test data in the experiment-results shape; no GPU measurement is claimed.

    uv run --frozen pytest -q tests/r/optimization/test_optimization.py
"""
from __future__ import annotations

import asyncio
import hashlib

import httpx
import pytest
from infrx.contracts import errors
from infrx.contracts.lab import fakes
from infrx.contracts.lab import records as lab
from infrx.rollouts import optimization as r3
from infrx.worker.engine import EngineUnsupported, VllmEngine

P = "11111111-1111-4111-8111-111111111111"
VARIANT_ID = "0000008c-0000-4000-8000-00000000008c"
BASE = r3.Identity.model_validate({
    "checkpoint_digest": "sha256:" + "1" * 64, "tokenizer_digest": "sha256:" + "2" * 64,
    "served_model": "marlin-2b", "engine": "vllm", "engine_version": "0.11.0",
    "quantization": "bf16", "hardware": "L40S", "preprocessor": "marlin-sop-2fps",
    "capabilities": ["text", "finite_video"]})


def ident(**changes):
    return r3.Identity.model_validate({**BASE.model_dump(), **changes})


NVFP4 = ident(checkpoint_digest="sha256:" + "3" * 64, quantization="nvfp4", hardware="B300")


def engine(version="0.11.0", models=("marlin-2b",), require_version=None, served="marlin-2b"):
    """W3's adapter over a fake engine server."""
    def answer(request):
        if request.url.path == "/version":
            return httpx.Response(200, json={"version": version})
        return httpx.Response(200, json={"data": [{"id": m} for m in models]})
    client = httpx.AsyncClient(transport=httpx.MockTransport(answer), base_url="http://engine")
    return VllmEngine(client, served_model=served, clock=lambda: 0.0,
                      require_version=require_version)


def register(base=BASE, variant=NVFP4, eng=None):
    return asyncio.run(r3.register(P, VARIANT_ID, base, variant, eng or engine()))


VARIANT = register()
RUN = {
    "schema": "lab.eval_run.1", "provider_org_id": P,
    "run_id": "0000001e-0000-4000-8000-00000000001e", "created_at": "2026-09-27T10:00:00Z",
    "dataset_ref": f"lab:dataset:{P}:0000000a-0000-4000-8000-00000000000a@sha256:{'d' * 64}",
    "harness_ref": f"lab:harness:{P}:00000014-0000-4000-8000-000000000014@sha256:{'c' * 64}",
    "serving_ref": VARIANT["base_serving_ref"],
    "evaluator_ref": f"lab:evaluator:{P}:00000032-0000-4000-8000-000000000032@sha256:{'a' * 64}",
    "seed": 7, "environment": "dev", "max_cases": 100, "state": "succeeded",
    "idempotency_key": "run:0000001e-0000-4000-8000-00000000001e",
    "budgets": [{"limit": {"unit": "CREDIT", "value": "500.00000000"},
                 "reserved": {"unit": "CREDIT", "value": "0.00000000"}}]}


def run_of(serving_ref, run_id="0000001f-0000-4000-8000-00000000001f", **changes):
    return {**RUN, "run_id": run_id, "idempotency_key": f"run:{run_id}", "serving_ref": serving_ref,
            **changes}


VRUN = run_of(VARIANT["variant_serving_ref"])


def digest(value) -> str:
    return "sha256:" + hashlib.sha256(lab.canonical(value)).hexdigest()


def report(outcome="accept", runs=(RUN, VRUN), reasons=()):
    body = {"schema": "infrx.eval_report.1", "baseline_run": lab.ref_of(runs[0]),
            "candidate_run": lab.ref_of(runs[1]), "universe_digest": digest([]),
            "protocol": {}, "protocol_digest": digest({}),
            "pairing": {"kind": "single_factor", "factors": ["serving_ref"], "tag": None},
            "observed": {}, "estimates": {}, "decision": {"outcome": outcome, "reasons": list(reasons)}}
    return {**body, "report_digest": digest(body)}


def load(which="base", **changes):
    ident_, ref = (BASE, VARIANT["base_serving_ref"]) if which == "base" else \
        (NVFP4, VARIANT["variant_serving_ref"])
    row = {"serving_ref": ref, "profile_digest": "sha256:" + "9" * 64,
           "engine_version": ident_.engine_version, "hardware": ident_.hardware,
           "source": "marlin2b/results/fixture.jsonl@0000000",   # test data, not a measurement
           "throughput_tok_s": 1_000.0 if which == "base" else 2_500.0,
           "p99_ms": 900 if which == "base" else 700,
           "memory_gib": 20.0 if which == "base" else 12.5}
    return {**row, **changes}


LOADS = (load("base"), load("variant"))


def compare(variant=VARIANT, base=BASE, cand=NVFP4, rep=None, runs=(RUN, VRUN), loads=LOADS):
    return r3.compare(variant, base, cand, report=rep or report(runs=runs), runs=runs, loads=loads)


# --- R3.a registration ---------------------------------------------------------------------
def test_r3_a_variant_is_an_immutable_distinct_serving_identity():
    assert lab.validate(VARIANT) is None
    assert VARIANT["changes"] == ["checkpoint_digest:sha256:" + "3" * 64, "quantization:nvfp4",
                                  "hardware:B300"]
    assert VARIANT["base_serving_ref"] == r3.serving_ref(P, BASE)
    catalog = fakes.FakeLabCatalog()
    assert catalog.publish(VARIANT) == catalog.publish(register())      # same bytes, one ref
    seen = {VARIANT["variant_serving_ref"]}
    for change in ({"engine_version": "0.11.1"}, {"hardware": "H100"},
                   {"preprocessor": "marlin-sop-1fps"}, {"engine": "sglang"},
                   {"served_model": "marlin-2b-fp8"}):
        other = ident(**{**NVFP4.model_dump(), **change})
        eng = engine(version=other.engine_version, models=(other.served_model,),
                     served=other.served_model)
        ref = asyncio.run(r3.register(P, VARIANT_ID, BASE, other, eng))["variant_serving_ref"]
        assert ref not in seen, change
        seen.add(ref)
    with pytest.raises(errors.InvalidRequest):                          # not a variant
        register(variant=BASE)
    # capabilities are a set: their order is not an identity
    assert r3.serving_ref(P, ident(capabilities=["finite_video", "text"])) == r3.serving_ref(P, BASE)
    assert r3.serving_ref(P, ident(capabilities=["text"])) != r3.serving_ref(P, BASE)


def test_r3_registration_checks_the_engine_through_w3():
    with pytest.raises(errors.InvalidRequest):                          # claims 0.11.0, runs 0.10
        register(eng=engine(version="0.10.0"))
    with pytest.raises(EngineUnsupported):                              # W3's own pin refuses too
        register(eng=engine(version="0.10.0", require_version="0.11.0"))
    with pytest.raises(EngineUnsupported):                              # the model is not served
        register(eng=engine(models=("other",)))
    other = ident(**{**NVFP4.model_dump(), "served_model": "marlin-2b-fp8"})
    with pytest.raises(errors.InvalidRequest):                          # serves a different model
        asyncio.run(r3.register(P, VARIANT_ID, BASE, other, engine()))
    for bad in ({"capabilities": []}, {"capabilities": ["telepathy"]}, {"quantization": ""},
                {"extra": "x"}, {"tokenizer_digest": "2" * 64}):
        with pytest.raises(Exception):
            ident(**bad)


# --- R3.b the workload is pinned -------------------------------------------------------------
def test_r3_mismatched_workloads_are_refused():
    other_dataset = f"lab:dataset:{P}:0000000b-0000-4000-8000-00000000000b@sha256:{'d' * 64}"
    other_harness = f"lab:harness:{P}:00000015-0000-4000-8000-000000000015@sha256:{'c' * 64}"
    for runs in ((RUN, run_of(VARIANT["variant_serving_ref"], dataset_ref=other_dataset)),
                 (RUN, run_of(VARIANT["variant_serving_ref"], harness_ref=other_harness)),
                 (RUN, run_of(VARIANT["variant_serving_ref"], seed=8)),
                 (RUN, run_of(RUN["serving_ref"])),
                 (VRUN, run_of(RUN["serving_ref"]))):
        with pytest.raises(errors.InvalidRequest):
            compare(runs=runs)
    for loads in ((load("base"), load("variant", profile_digest="sha256:" + "8" * 64)),
                  (load("base"), load("variant", hardware="L40S")),
                  (load("base", engine_version="0.9.0"), load("variant")),
                  (load("base", serving_ref=VARIANT["variant_serving_ref"]), load("variant")),
                  (load("base"), load("variant", serving_ref=VARIANT["base_serving_ref"]))):
        with pytest.raises(errors.InvalidRequest):
            compare(loads=loads)


def test_r3_gpu_evidence_is_only_an_experiment_branch_result():
    for source in ("bench.jsonl", "marlin2b/results/bench.jsonl", "/tmp/marlin2b/results/x@abcdef1",
                   "apps/marlin2b/results/x@abcdef1", "llama/results/x@abcdef1",
                   "marlin2b/results/x@main"):
        with pytest.raises(errors.InvalidRequest):
            compare(loads=(load("base"), load("variant", source=source)))
    assert compare(loads=(load("base", source="models/kimik3/results/r.json@0123456789abcdef"),
                          load("variant")))["optimization_claimed"]


def test_r3_the_report_and_identities_are_bound():
    tampered = {**report("reject"), "decision": {"outcome": "accept", "reasons": []}}
    with pytest.raises(errors.InvalidRequest):
        compare(rep=tampered)
    with pytest.raises(errors.InvalidRequest):                          # a report about other runs
        compare(rep=report(runs=(RUN, run_of(VARIANT["variant_serving_ref"], seed=7,
                                                   run_id="00000020-0000-4000-8000-000000000020"))))
    with pytest.raises(errors.InvalidRequest):                          # a lie about the identity
        compare(cand=ident(**{**NVFP4.model_dump(), "hardware": "H100"}))
    with pytest.raises(errors.InvalidRequest):
        compare(base=ident(quantization="fp16"))


# --- R3.c the verdict --------------------------------------------------------------------------
def test_r3_an_equivalent_measured_variant_claims_its_optimization():
    out = compare()
    assert out["outcome"] == "equivalent" and out["reasons"] == [] and out["optimization_claimed"]
    assert out["performance"] == {"throughput_ratio": 2.5, "p99_ms_delta": -200,
                                  "memory_gib_delta": -7.5,
                                  "sources": [LOADS[0]["source"], LOADS[1]["source"]]}
    assert out["variant_ref"] == lab.ref_of(VARIANT)
    assert out["report_digest"] == report()["report_digest"]
    assert out["capabilities"] == {"base": ["finite_video", "text"],
                                   "variant": ["finite_video", "text"]}


def test_r3_better_throughput_cannot_override_a_failed_slice():
    out = compare(rep=report("reject", reasons=["slice sop inferior"]))
    assert out["outcome"] == "rejected" and not out["optimization_claimed"]
    assert out["reasons"] == ["slice sop inferior"]
    assert out["performance"]["throughput_ratio"] == 2.5                # reported, not decisive


def test_r3_a_tokenizer_change_or_a_lost_capability_is_never_equivalent():
    tok = ident(**{**NVFP4.model_dump(), "tokenizer_digest": "sha256:" + "4" * 64})
    lost = ident(**{**NVFP4.model_dump(), "capabilities": ["text"]})
    for cand, reason in ((tok, "tokenizer_changed"), (lost, "capability_lost:finite_video")):
        variant = register(variant=cand)
        runs = (RUN, run_of(variant["variant_serving_ref"]))
        loads = (load("base"), load("variant", serving_ref=variant["variant_serving_ref"]))
        out = r3.compare(variant, BASE, cand, report=report(runs=runs), runs=runs, loads=loads)
        assert out["outcome"] == "not_equivalent" and out["reasons"] == [reason]
        assert not out["optimization_claimed"]
    # a variant that ADDS a capability is not refused for it (it is reported)
    more = ident(**{**NVFP4.model_dump(), "capabilities": ["text", "finite_video", "tools"]})
    variant = register(variant=more)
    runs = (RUN, run_of(variant["variant_serving_ref"]))
    out = r3.compare(variant, BASE, more, report=report(runs=runs), runs=runs, loads=None)
    assert out["outcome"] == "equivalent" and out["capabilities"]["variant"] == [
        "finite_video", "text", "tools"]


def test_r3_unmeasured_or_inconclusive_claims_nothing():
    out = compare(loads=None)
    assert out["outcome"] == "equivalent" and out["performance"] is None
    assert not out["optimization_claimed"]
    out = compare(rep=report("inconclusive", reasons=["coverage 3/100"]))
    assert out["outcome"] == "inconclusive" and out["reasons"] == ["coverage 3/100"]
    assert not out["optimization_claimed"]
