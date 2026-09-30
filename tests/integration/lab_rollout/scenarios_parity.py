"""E8L k07: R3's optimization evidence - OPT-PARITY, local half. W3's real `VllmEngine`
probes a vLLM stub over HTTP (`lab_world.engine`: `/version`, `/v1/models`); the two runs are
published D7 records serving the base and the variant; the B2 report is `reports.compare`
over owned case records; the comparison is stored through D7 (0034/0040) and read back.

No GPU parity is claimed: a load record here is test data in the experiment-results shape and
only proves which claims R3 refuses. Measured parity on an allocated target is k08 (NOT RUN).
"""
from __future__ import annotations

import time

import lab_world as lw
import pytest
from lab_world import run

VARIANT_ID = lw.uid(1, 0x8c8)


def probe(url: str, served: str = "marlin-2b"):
    import httpx
    from infrx.worker.engine import VllmEngine
    return VllmEngine(httpx.AsyncClient(base_url=url), served_model=served, clock=time.monotonic)


def identities():
    from tests.r.optimization import test_optimization as r3t
    return r3t.BASE, r3t.NVFP4, r3t.ident


def load(serving_ref: str, identity, throughput: float, source: str) -> dict:
    return {"serving_ref": serving_ref, "profile_digest": "sha256:" + "7" * 64,
            "engine_version": identity.engine_version, "hardware": identity.hardware,
            "source": source, "throughput_tok_s": throughput, "p99_ms": 900, "memory_gib": 20.0}


def test_k07_a_variant_is_probed_compared_and_stored(lab, workdir):
    """An NVFP4 variant of the base registers only through the probe of its declared engine
    and version; on an accepting B2 report over runs serving exactly the base and the variant
    it is `equivalent`, but claims an optimization only with measurements from an
    experiment's `results/` at a commit; the comparison is stored beside its variant and
    report (once, replay-safe) and read back only by its provider."""
    from infrx.rollouts import optimization as r3
    from infrx.state.jobstore import connector
    from infrx.state.lab_data import PgLabDataStore
    from infrx.state.lab_variants import PgLabVariants
    base, nvfp4, _ = identities()
    with lw.engine(nvfp4.engine_version, (nvfp4.served_model,)) as url:
        variant = run(r3.register(lab.NEMO, VARIANT_ID, base, nvfp4, probe(url)))
    runs = lab.runs(0x8d8, variant["base_serving_ref"], variant["variant_serving_ref"])
    report = lab.report(runs, "improving", lw.PROTOCOL)
    plain = r3.compare(variant, base, nvfp4, report=report, runs=runs)
    assert (plain["outcome"], plain["optimization_claimed"]) == ("equivalent", False)
    loads = (load(variant["base_serving_ref"], base, 1000.0,
                  "marlin2b/results/e8l-base.json@0123abc"),
             load(variant["variant_serving_ref"], nvfp4, 1500.0,
                  "marlin2b/results/e8l-nvfp4.json@0123abc"))
    measured = r3.compare(variant, base, nvfp4, report=report, runs=runs, loads=loads)
    assert measured["optimization_claimed"] and measured["performance"]["throughput_ratio"] == 1.5
    data = PgLabDataStore(connector(lab.dsn))
    ids = {"identities": (base, nvfp4), "variants": PgLabVariants(connector(lab.dsn))}  # R263
    first = run(r3.store(data, variant, measured, report, provider_org_id=lab.NEMO,
                         actor="dev@nemo", **ids))
    again = run(r3.store(data, variant, measured, report, provider_org_id=lab.NEMO,
                         actor="dev@nemo", **ids))
    lw.save(workdir, "comparison.json", {"variant": variant, "comparison": measured,
                                         "digest": first})
    assert first == again
    assert run(data.variant_comparisons(report["report_digest"], provider_org_id=lab.NEMO)) \
        == [measured]
    assert run(data.variant_comparisons(report["report_digest"],
                                        provider_org_id=lab.d7.OTHER)) == []


def test_k07_incompatible_variants_and_unmeasured_claims_are_refused(lab, workdir):
    """Refused at registration: an engine answering another version, one not serving the
    declared model, a variant declaring an engine with no W3 adapter. Not equivalent: a
    changed tokenizer. Inconclusive: a claimed capability the base lacks. Rejected whatever
    its throughput: a B2 slice regression. Refused as unmeasured: a load not from an
    experiment's results at a commit, or measured on other hardware than registered."""
    from infrx.contracts import errors
    from infrx.rollouts import optimization as r3
    from infrx.worker.engine import EngineUnsupported
    base, nvfp4, ident = identities()
    with lw.engine("0.10.0", (nvfp4.served_model,)) as url:
        with pytest.raises(errors.InvalidRequest):
            run(r3.register(lab.NEMO, VARIANT_ID, base, nvfp4, probe(url)))
    with lw.engine(nvfp4.engine_version, ("another-model",)) as url:
        with pytest.raises((errors.InvalidRequest, EngineUnsupported)):
            run(r3.register(lab.NEMO, VARIANT_ID, base, nvfp4, probe(url)))
        with pytest.raises(errors.InvalidRequest):
            run(r3.register(lab.NEMO, VARIANT_ID, base, ident(engine="sglang", quantization="fp8"),
                            probe(url)))
    outcomes = {}
    with lw.engine(nvfp4.engine_version, (nvfp4.served_model,)) as url:
        for name, cand, records in (
                ("tokenizer", ident(tokenizer_digest="sha256:" + "9" * 64), "improving"),
                ("capability", ident(quantization="fp8",
                                     capabilities=["text", "finite_video", "tools"]), "improving"),
                ("regressing", nvfp4, "regressing")):
            variant = run(r3.register(lab.NEMO, VARIANT_ID, base, cand, probe(url)))
            runs = lab.runs(0x8e0 + len(outcomes), variant["base_serving_ref"],
                            variant["variant_serving_ref"])
            fast = (load(variant["base_serving_ref"], base, 1000.0,
                         "marlin2b/results/e8l-base.json@0123abc"),
                    load(variant["variant_serving_ref"], cand, 3000.0,
                         "marlin2b/results/e8l-fast.json@0123abc"))
            got = r3.compare(variant, base, cand, report=lab.report(runs, records, lw.PROTOCOL),
                             runs=runs, loads=fast)
            outcomes[name] = (got["outcome"], got["reasons"], got["optimization_claimed"])
            if name == "regressing":
                report = lab.report(runs, "improving", lw.PROTOCOL)
                for bad in ((fast[0], {**fast[1], "source": "a laptop run"}),
                            (fast[0], {**fast[1], "hardware": "H100"})):
                    with pytest.raises(errors.InvalidRequest):
                        r3.compare(variant, base, cand, report=report, runs=runs, loads=bad)
    lw.save(workdir, "outcomes.json", outcomes)
    assert outcomes["tokenizer"][:1] == ("not_equivalent",)
    assert "tokenizer_changed" in outcomes["tokenizer"][1]
    assert outcomes["capability"][:2] == ("inconclusive", ["capability_unverified:tools"])
    assert outcomes["regressing"] == ("rejected", ["slice math inferior"], False)
    assert not any(claimed for _, _, claimed in outcomes.values())
