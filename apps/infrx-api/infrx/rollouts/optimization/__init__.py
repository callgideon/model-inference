"""R3: register and compare optimized serving variants (OPT-PARITY).

**R3.a registration.** An externally produced variant (quantized, pruned, adapter,
speculative, another runtime or hardware) is described by an `Identity`: everything that
changes what a serving revision computes. Its serving ref is the sha256 of that identity's
canonical JSON, so re-registering one checkpoint with another engine, version, hardware,
quantization or preprocessor is a distinct serving identity, and registering the same
identity twice is the same ref. `register` first asks the variant's engine through W3's
capability probe (`VllmEngine.capabilities`, which refuses a model it does not serve or a
pinned version it is not) and refuses a variant whose engine version or served model is
not the declared one, or whose declared `engine` is not the probing adapter's kind (only
`vllm` has a W3 adapter); the result is an F3 `lab.optimization_variant.1` naming what changed.

**R3.b comparison.** `compare` takes the registered variant, both identities (re-derived to
the registered refs, so an identity cannot be swapped afterwards), the two B1 runs and the
B2 report on them. The runs must differ in the serving ref only (H1's `compare`: same
provider, dataset, evaluator, environment and case bound; an untagged second factor, harness
or seed, is refused) and serve the base and the variant; the report must carry its own digest and name exactly these runs.
Anything else is a mismatched workload and is refused. Load measurements, when given, are
one per side under one load profile, on the registered engine version and hardware, and
come only from an experiment branch's `results/` at a commit (`<exp>/results/...@<sha>`).

**R3.c verdict.** A B2 `reject` (a failed required slice) is `rejected` whatever the
throughput; a changed tokenizer or a lost base capability is `not_equivalent`; a
non-accepting report is `inconclusive`, and so is a variant claiming a capability the base
lacks: W3's probe does not report capabilities and the paired report covers only the base's
workload, so such a claim is unverified (reported as `capability_unverified:<c>`); otherwise `equivalent`. An optimization is claimed
only for an equivalent variant with measurements. ponytail: no cost column; attach one when
a measured $/GPU-hour source (cloud-pricing) is wired to the load records.

**R3.d storage** (`store`, WR-LSQ-6). The comparison rests on its variant and its B2 report:
the variant record is published (content-addressed), the report stored write-once by its
digest (0034 `put_eval_report`), and only then the comparison (0040
`put_variant_comparison`), all as the provider's.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, Field, ValidationError

from ...contracts import errors
from ...contracts.lab import records as lab
from ...harnesses.replay import compare as pair_runs
from ...worker.engine import VllmEngine

ADAPTERS = {"vllm": VllmEngine}   # W3's engine adapters by the `engine` an identity declares

CAPABILITIES = ("text", "finite_video", "structured", "tools")
EXPERIMENTS = ("deepseek41f", "deepseek41fnvfp4", "qwen3827b", "kimik3", "marlin2b")
RESULTS = re.compile(rf"(?:models/)?(?:{'|'.join(EXPERIMENTS)})/results/\S+@[0-9a-f]{{7,40}}")


class Identity(lab.LabModel):
    checkpoint_digest: lab.Sha256
    tokenizer_digest: lab.Sha256
    served_model: lab.Text
    engine: lab.Text
    engine_version: lab.Text
    quantization: lab.Text
    hardware: lab.Text
    preprocessor: lab.Text
    capabilities: Annotated[list[Literal[CAPABILITIES]], Field(min_length=1),
                            AfterValidator(lambda v: sorted(set(v)))]


class Load(lab.LabModel):
    serving_ref: lab.RefOf("serving")
    profile_digest: lab.Sha256
    engine_version: lab.Text
    hardware: lab.Text
    source: Annotated[str, Field(pattern=f"^{RESULTS.pattern}$")]
    throughput_tok_s: float = Field(gt=0)
    p99_ms: int = Field(ge=0)
    memory_gib: float = Field(gt=0)


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(lab.canonical(value)).hexdigest()


def serving_ref(provider_org_id: str, identity: Identity) -> str:
    digest = _digest(identity.model_dump(mode="json")).removeprefix("sha256:")
    return f"lab:serving:{provider_org_id}:{uuid.UUID(hex=digest[:32], version=4)}@sha256:{digest}"


async def register(provider_org_id: str, variant_id: str, base: Identity, variant: Identity,
                   engine) -> dict[str, Any]:
    """The `lab.optimization_variant.1` of `variant` over `base`, after W3's probe of the
    variant's engine (`engine.capabilities()`)."""
    if not isinstance(engine, ADAPTERS.get(variant.engine, ())):
        raise errors.InvalidRequest(f"a {variant.engine} variant probed through another engine")
    probe = await engine.capabilities()
    if probe.get("version") != variant.engine_version or \
            variant.served_model not in probe.get("models", []):
        raise errors.InvalidRequest("the variant's engine is not the one it declares")
    was = base.model_dump()
    changes = [f"{k}:{','.join(v) if isinstance(v, list) else v}"
               for k, v in variant.model_dump().items() if was[k] != v]
    payload = {"schema": "lab.optimization_variant.1", "provider_org_id": provider_org_id,
               "variant_id": variant_id, "base_serving_ref": serving_ref(provider_org_id, base),
               "variant_serving_ref": serving_ref(provider_org_id, variant), "changes": changes}
    lab.parse(payload)            # the same identity is no variant: no changes, one serving ref
    return payload


def _performance(loads, base: Identity, variant: Identity,
                 refs: tuple[str, str]) -> dict[str, Any] | None:
    if loads is None:
        return None
    try:
        b, v = (Load.model_validate(x) for x in loads)
    except ValidationError as refused:
        raise errors.InvalidRequest(f"a load record: {refused}") from None
    if b.profile_digest != v.profile_digest:
        raise errors.InvalidRequest("the two sides ran different load profiles")
    for load, ident, ref in ((b, base, refs[0]), (v, variant, refs[1])):
        if (load.serving_ref, load.engine_version, load.hardware) != \
                (ref, ident.engine_version, ident.hardware):
            raise errors.InvalidRequest("a measurement of something other than the registered identity")
    return {"throughput_ratio": v.throughput_tok_s / b.throughput_tok_s,
            "p99_ms_delta": v.p99_ms - b.p99_ms, "memory_gib_delta": v.memory_gib - b.memory_gib,
            "sources": [b.source, v.source]}


def compare(variant: dict[str, Any], base: Identity, candidate: Identity, *,
            report: dict[str, Any], runs: tuple[dict[str, Any], dict[str, Any]],
            loads: tuple[dict[str, Any], dict[str, Any]] | None = None) -> dict[str, Any]:
    record = lab.parse(variant)
    refs = (record.base_serving_ref, record.variant_serving_ref)
    if (serving_ref(record.provider_org_id, base),
            serving_ref(record.provider_org_id, candidate)) != refs:
        raise errors.InvalidRequest("the identities are not the registered ones")
    # H1: one case universe, and an untagged multifactor pair (serving AND harness or seed)
    # is refused; so runs serving the base and the variant differ in serving only.
    pair_runs(runs[0], runs[1])
    if (runs[0]["serving_ref"], runs[1]["serving_ref"]) != refs:
        raise errors.InvalidRequest("the runs serve the base and then the variant")
    body = {k: v for k, v in report.items() if k != "report_digest"}
    if report.get("report_digest") != _digest(body) or \
            (report.get("baseline_run"), report.get("candidate_run")) != \
            (lab.ref_of(runs[0]), lab.ref_of(runs[1])):
        raise errors.InvalidRequest("the report is not bound to these runs")
    performance = _performance(loads, base, candidate, refs)
    decided, reasons = report["decision"]["outcome"], list(report["decision"]["reasons"])
    unlike = (["tokenizer_changed"] if base.tokenizer_digest != candidate.tokenizer_digest else []) \
        + [f"capability_lost:{c}" for c in base.capabilities if c not in candidate.capabilities]
    unverified = [f"capability_unverified:{c}" for c in candidate.capabilities
                  if c not in base.capabilities]
    outcome = "rejected" if decided == "reject" else "not_equivalent" if unlike else \
        "inconclusive" if decided != "accept" or unverified else "equivalent"
    return {"schema": "infrx.variant_comparison.1", "variant_ref": lab.ref_of(variant),
            "outcome": outcome,
            "reasons": reasons if decided == "reject" else unlike + unverified + reasons,
            "capabilities": {"base": base.capabilities, "variant": candidate.capabilities},
            "report_digest": report["report_digest"], "performance": performance,
            "optimization_claimed": outcome == "equivalent" and performance is not None}


async def store(data, variant: dict[str, Any], comparison: dict[str, Any],
                report: dict[str, Any], *, provider_org_id: str, actor: str) -> str:
    """Store `comparison` (from `compare`) beside its variant and B2 report, in that order,
    through D7 (`PgLabDataStore`); its digest. A comparison of another variant or resting on
    another report is refused before anything is written."""
    if comparison["variant_ref"] != lab.ref_of(variant) or \
            comparison["report_digest"] != report.get("report_digest"):
        raise errors.InvalidRequest("the comparison is not of this variant and report")
    await data.publish(variant, provider_org_id=provider_org_id, actor=actor)
    await data.put_eval_report(report, provider_org_id=provider_org_id, actor=actor)
    return await data.put_variant_comparison(comparison, provider_org_id=provider_org_id,
                                             actor=actor)
