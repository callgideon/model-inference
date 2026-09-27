"""F3.a/b/c: the Lab records, one validator, refs, keys, rights gates and cohorts.

**Refs are immutable by grammar.** Every Lab reference is
`lab:<kind>:<provider_org_id>:<object_id>@sha256:<64 hex>`; a label (`@latest`), a
missing digest or any other spelling is a *mutable ref* and is refused. A record's own
ref is the sha256 of its RFC 8785 canonical JSON (keys by UTF-16 code unit, compact,
ECMAScript number spelling, UTF-8), so both halves name one record identically and
changing one byte of a manifest, harness or policy changes its identity (EVAL-REPRO,
DATA-IMMUTABLE). A number the halves would read differently (non-finite, or an integer
past 2^53) is `invalid`.

**Free-form content is opaque.** A tool's `input_schema`, an `input_mapping`, a
`reference_output` and a `label` are content: nothing inside them is read as a ref or an
amount (`OPAQUE`), so a tool property named `order_ref` is data.

**Every ref in a record belongs to the record's provider.** The provider segment is
checked against `provider_org_id` before anything else is read; a server additionally
resolves each ref against its own rows (the fake: `FakeLabCatalog.resolve`).

**Refusals have one of five reasons**, decided in this order by both halves:
`unknown_schema`, `mutable_ref`, `cross_provider_ref`, `mixed_units`, `invalid`.

**Money is unit-tagged.** An amount is `{"unit", "value"}` in CREDIT (provider_dev
inference) or PROVIDER_USD (judge/teacher/training, with a named payer); two amounts
side by side in a different unit are `mixed_units`; the legacy consumer USD regime is
never a Lab unit. There is no conversion (v2 `money_units`).

**Rights are checked at the gate, never carried.** `authorize` takes the *current*
membership and grant (server-derived) at scheduling, access, export and external
submission, for the purpose the gate allows; at external submission that is the run's
own purpose (judging or training), never a default. A published manifest names its
grants as audit evidence and confers no continued access (DATA-RIGHTS). A role alone
never authorizes data reuse.
"""
from __future__ import annotations

import enum
import hashlib
import json
import math
import re
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import (AfterValidator, BaseModel, ConfigDict, Field, ValidationError,
                      model_validator)

from .. import errors
from ..ids import UUID_RE
from ..v2 import records as v2
from ..v2.money_units import Credit, ProviderUsd
from .states import STATES

SURFACE_VERSION = "contracts-lab.1"
SCHEMAS = ("lab.dataset_manifest.1", "lab.harness_revision.1", "lab.eval_run.1",
           "lab.attempt.1", "lab.checkpoint.1", "lab.annotation.1", "lab.external_run.1",
           "lab.rollout_policy.1", "lab.rollout_assignment.1", "lab.rollout_decision.1",
           "lab.optimization_variant.1")
REF_KINDS = ("source", "grant", "dataset", "harness", "serving", "evaluator", "run",
             "external_run", "checkpoint", "annotation", "rubric", "payer", "policy", "variant")
MODALITIES = ("text", "finite_video", "structured")
MAX_VIDEO_MS = 82_000                  # the product's finite-video cap (LANE-RULES 8)
REASONS = ("unknown_schema", "mutable_ref", "cross_provider_ref", "mixed_units", "invalid")
LAB_UNITS = ("CREDIT", "PROVIDER_USD")
# The free-form dicts records carry as content: never scanned for refs or amounts.
OPAQUE = ("input_schema", "input_mapping", "reference_output", "label")
SAFE_INT = 2**53 - 1                   # the largest integer both halves read exactly

REF_RE = re.compile(rf"lab:({'|'.join(REF_KINDS)}):({UUID_RE.pattern}):({UUID_RE.pattern})"
                    r"@sha256:([0-9a-f]{64})")


class LabRejected(errors.InvalidRequest):
    """A refused Lab payload; `reason` is one of `REASONS`."""

    def __init__(self, reason: str, detail: str) -> None:
        self.reason = reason
        super().__init__(f"{reason}: {detail}")


# --- scalars --------------------------------------------------------------------------------
Uuid = Annotated[str, Field(pattern=f"^{UUID_RE.pattern}$")]
Ts = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$")]
Sha256 = v2.Sha256
Text = Annotated[str, Field(min_length=1)]


def RefOf(*kinds: str):  # noqa: N802 - a type factory, read like a type
    def check(value: str) -> str:
        match = REF_RE.fullmatch(value)
        if match is None or match.group(1) not in kinds:
            raise ValueError(f"expected a {'/'.join(kinds)} ref, got {value!r}")
        return value
    return Annotated[str, AfterValidator(check)]


class LabModel(BaseModel):
    # strict: "7" is not 7 and True is not 1, exactly as the TypeScript half reads them.
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, populate_by_name=True)


class LabRecord(LabModel):
    schema_id: str = Field(alias="schema")
    provider_org_id: Uuid


class Amount(LabModel):
    unit: Literal[LAB_UNITS]
    value: Annotated[str, Field(pattern=r"^(0|[1-9][0-9]{0,11})\.[0-9]{8}$")]

    @property
    def amount(self) -> Credit | ProviderUsd:
        return (Credit if self.unit == "CREDIT" else ProviderUsd)(self.value)


def _units(value: str) -> int:
    """Exact 1e-8 units of an already-validated amount string."""
    return int(value.replace(".", ""))


class Budget(LabModel):
    limit: Amount
    reserved: Amount
    payer_ref: RefOf("payer") | None = None

    @model_validator(mode="after")
    def _paid_work_names_its_payer(self) -> Budget:
        if self.limit.unit == "PROVIDER_USD" and self.payer_ref is None:
            raise ValueError("PROVIDER_USD work names its payer")
        if _units(self.reserved.value) > _units(self.limit.value):
            raise ValueError("reserved exceeds the limit")
        return self


# --- F3.a: datasets -------------------------------------------------------------------------
class Sample(LabModel):
    sample_id: Uuid
    modality: Literal[MODALITIES]
    source_ref: RefOf("source")
    grant_ref: RefOf("grant")
    content_digest: Sha256
    group_key: Text
    duration_ms: int | None = None

    @model_validator(mode="after")
    def _video_is_finite(self) -> Sample:
        if (self.modality == "finite_video") != (self.duration_ms is not None):
            raise ValueError("duration_ms is set for finite video and only for it")
        if self.duration_ms is not None and not 1 <= self.duration_ms <= MAX_VIDEO_MS:
            raise ValueError(f"a finite video is 1..{MAX_VIDEO_MS} ms")
        return self


class Splits(LabModel):
    train: list[Uuid]
    validation: list[Uuid]
    holdout: list[Uuid]


class DatasetManifest(LabRecord):
    schema_id: Literal["lab.dataset_manifest.1"] = Field(alias="schema")
    dataset_id: Uuid
    version: int = Field(ge=1)
    created_at: Ts
    derivation: Literal["import", "derive"]
    parent_refs: list[RefOf("dataset")]
    samples: list[Sample] = Field(min_length=1)
    splits: Splits

    @model_validator(mode="after")
    def _lineage_and_splits(self) -> DatasetManifest:
        if (self.derivation == "derive") != bool(self.parent_refs):
            raise ValueError("a derived version names its parents; an import names none")
        ids = [s.sample_id for s in self.samples]
        placed = self.splits.train + self.splits.validation + self.splits.holdout
        if len(set(ids)) != len(ids) or sorted(placed) != sorted(ids):
            raise ValueError("every sample is in exactly one split")
        split_of = {sid: name for name in ("train", "validation", "holdout")
                    for sid in getattr(self.splits, name)}
        groups: dict[str, str] = {}
        for sample in self.samples:
            if groups.setdefault(sample.group_key, split_of[sample.sample_id]) != split_of[sample.sample_id]:
                raise ValueError(f"group {sample.group_key!r} spans two splits")
        return self


# --- H1's record: a harness revision --------------------------------------------------------
class Tool(LabModel):
    name: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
    effect: Literal["read_only", "network_mutation", "actuator"]
    input_schema: dict[str, Any]


class HarnessRevision(LabRecord):
    schema_id: Literal["lab.harness_revision.1"] = Field(alias="schema")
    harness_id: Uuid
    version: int = Field(ge=1)
    created_at: Ts
    adapter: Literal[MODALITIES]
    prompt_template: Text
    processor_profile: Text
    input_mapping: dict[str, str]
    tools: list[Tool]
    reference_output: dict[str, Any] | None = None


# --- F3.b: evaluation, attempts, checkpoints, annotations, external runs ---------------------
def run_key(run_id: str) -> str:
    return f"run:{run_id}"


def attempt_key(run_id: str, case_id: str, attempt: int) -> str:
    """One logical attempt, however often its dispatch is retried."""
    return f"attempt:{run_id}:{case_id}:{attempt}"


def submit_key(external_run_id: str) -> str:
    """One external submission per run; a retry after `ambiguous` reuses it."""
    return f"submit:{external_run_id}"


def _object_id(ref: str) -> str:
    return REF_RE.fullmatch(ref).group(3)


class EvalRun(LabRecord):
    schema_id: Literal["lab.eval_run.1"] = Field(alias="schema")
    run_id: Uuid
    created_at: Ts
    dataset_ref: RefOf("dataset")
    harness_ref: RefOf("harness")
    serving_ref: RefOf("serving")
    evaluator_ref: RefOf("evaluator")
    seed: int = Field(ge=0)
    environment: Literal["dev"]            # evaluation never targets a production endpoint
    max_cases: int = Field(ge=1)
    state: Literal[STATES["run"]]
    idempotency_key: str
    budgets: list[Budget]

    @model_validator(mode="after")
    def _keyed_and_one_budget_per_unit(self) -> EvalRun:
        if self.idempotency_key != run_key(self.run_id):
            raise ValueError("idempotency_key is derived from run_id")
        units = [b.limit.unit for b in self.budgets]
        if len(set(units)) != len(units):
            raise ValueError("one budget per unit")
        return self


class Attempt(LabRecord):
    schema_id: Literal["lab.attempt.1"] = Field(alias="schema")
    run_ref: RefOf("run")
    case_id: Uuid
    attempt: int = Field(ge=1)
    idempotency_key: str
    state: Literal[STATES["attempt"]]

    @model_validator(mode="after")
    def _keyed(self) -> Attempt:
        if self.idempotency_key != attempt_key(_object_id(self.run_ref), self.case_id, self.attempt):
            raise ValueError("idempotency_key is derived from run, case and attempt")
        return self


class Checkpoint(LabRecord):
    schema_id: Literal["lab.checkpoint.1"] = Field(alias="schema")
    checkpoint_id: Uuid
    external_run_ref: RefOf("external_run")
    artifact_digest: Sha256
    state: Literal[STATES["checkpoint"]]
    received_at: Ts


class Annotation(LabRecord):
    """PIPELINE-LINEAGE: the original evidence, method and reviewer stay on the label."""

    schema_id: Literal["lab.annotation.1"] = Field(alias="schema")
    annotation_id: Uuid
    dataset_ref: RefOf("dataset")
    sample_id: Uuid
    method: Literal["human", "synthetic", "imported"]
    method_version: Text
    reviewer_id: Uuid | None = None
    rubric_ref: RefOf("rubric")
    evidence_ref: RefOf("source")
    label: dict[str, Any]
    ground_truth: bool
    state: Literal[STATES["annotation"]]

    @model_validator(mode="after")
    def _no_forged_ground_truth(self) -> Annotation:
        if self.method == "human" and self.reviewer_id is None:
            raise ValueError("a human label names its reviewer")
        if self.method == "synthetic" and self.ground_truth:
            raise ValueError("a synthetic label is never ground truth")
        return self


class ExternalRun(LabRecord):
    schema_id: Literal["lab.external_run.1"] = Field(alias="schema")
    external_run_id: Uuid
    purpose: Literal["external_judging", "training"]
    connector: Text
    dataset_ref: RefOf("dataset")
    submit_key: str
    state: Literal[STATES["external_run"]]
    budget: Budget

    @model_validator(mode="after")
    def _keyed_and_paid_in_dollars(self) -> ExternalRun:
        if self.submit_key != submit_key(self.external_run_id):
            raise ValueError("submit_key is derived from external_run_id")
        if self.budget.limit.unit != "PROVIDER_USD":
            raise ValueError("external work is PROVIDER_USD with a named payer")
        return self


# --- F3.c: rollout and optimization variants -------------------------------------------------
class Candidate(LabModel):
    serving_ref: RefOf("serving")
    weight_bp: int = Field(ge=0, le=10_000)


class RolloutPolicy(LabRecord):
    schema_id: Literal["lab.rollout_policy.1"] = Field(alias="schema")
    policy_id: Uuid
    version: int = Field(ge=1)
    created_at: Ts
    endpoint_id: Uuid
    baseline_ref: RefOf("serving")
    mode: Literal["off", "shadow", "canary"]
    cohort: Literal["account", "session"]
    candidates: list[Candidate]

    @model_validator(mode="after")
    def _weights(self) -> RolloutPolicy:
        total = sum(c.weight_bp for c in self.candidates)
        if total > 10_000:
            raise ValueError("candidate weights exceed 10000 bp")
        if self.mode != "canary" and total:
            raise ValueError("only a canary serves candidate traffic; shadow output is suppressed")
        return self


class RolloutAssignment(LabRecord):
    schema_id: Literal["lab.rollout_assignment.1"] = Field(alias="schema")
    policy_ref: RefOf("policy")
    request_id: Uuid
    cohort_digest: Sha256
    serving_ref: RefOf("serving")
    pinned_by: Literal["cohort", "explicit"]


class RolloutDecision(LabRecord):
    schema_id: Literal["lab.rollout_decision.1"] = Field(alias="schema")
    policy_ref: RefOf("policy")
    decision: Literal["expand", "hold", "rollback"]
    evidence_refs: list[RefOf("run")]
    decided_by: Uuid
    decided_at: Ts

    @model_validator(mode="after")
    def _no_expansion_on_missing_evidence(self) -> RolloutDecision:
        if self.decision == "expand" and not self.evidence_refs:
            raise ValueError("expansion names its evidence")
        return self


class OptimizationVariant(LabRecord):
    """A new serving revision derived from a base one; never an edit of the base."""

    schema_id: Literal["lab.optimization_variant.1"] = Field(alias="schema")
    variant_id: Uuid
    base_serving_ref: RefOf("serving")
    variant_serving_ref: RefOf("serving")
    changes: list[Text] = Field(min_length=1)

    @model_validator(mode="after")
    def _distinct(self) -> OptimizationVariant:
        if self.base_serving_ref == self.variant_serving_ref:
            raise ValueError("a variant is a different serving revision")
        return self


MODELS: dict[str, type[LabRecord]] = {
    model.model_fields["schema_id"].annotation.__args__[0]: model
    for model in (DatasetManifest, HarnessRevision, EvalRun, Attempt, Checkpoint, Annotation,
                  ExternalRun, RolloutPolicy, RolloutAssignment, RolloutDecision,
                  OptimizationVariant)}
# schema -> (ref kind, id field) for the records other records point at.
REFERABLE: dict[str, tuple[str, str]] = {
    "lab.dataset_manifest.1": ("dataset", "dataset_id"),
    "lab.harness_revision.1": ("harness", "harness_id"),
    "lab.eval_run.1": ("run", "run_id"),
    "lab.checkpoint.1": ("checkpoint", "checkpoint_id"),
    "lab.annotation.1": ("annotation", "annotation_id"),
    "lab.external_run.1": ("external_run", "external_run_id"),
    "lab.rollout_policy.1": ("policy", "policy_id"),
    "lab.optimization_variant.1": ("variant", "variant_id"),
}


# --- the one validator ----------------------------------------------------------------------
def _refs(node: Any):
    """Every ref-valued field: a key ending `_ref` (one) or `_refs` (a list), outside
    the opaque free-form content."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key in OPAQUE:
                continue
            if key.endswith("_ref") and value is not None:
                yield value
            elif key.endswith("_refs") and isinstance(value, list):
                yield from value
            else:
                yield from _refs(value)
    elif isinstance(node, list):
        for item in node:
            yield from _refs(item)


def _mixed(node: Any) -> bool:
    """True if two sibling amounts (dicts with a `unit`) disagree on the unit."""
    if isinstance(node, dict):
        values = [v for k, v in node.items() if k not in OPAQUE]
        units = [v["unit"] for v in values if isinstance(v, dict) and "unit" in v]
        return any(u != units[0] for u in units) or any(_mixed(v) for v in values)
    if isinstance(node, list):
        return any(_mixed(v) for v in node)
    return False


def _exact_numbers(node: Any) -> bool:
    """Every number means the same to both halves: finite, and integers within 2^53."""
    if isinstance(node, dict):
        return all(map(_exact_numbers, node.values()))
    if isinstance(node, list):
        return all(map(_exact_numbers, node))
    if isinstance(node, float):
        return math.isfinite(node) and (not node.is_integer() or abs(node) <= SAFE_INT)
    return isinstance(node, bool) or not isinstance(node, int) or abs(node) <= SAFE_INT


def parse(payload: Any) -> LabRecord:
    """The record, or `LabRejected` with the first reason in `REASONS` order."""
    schema = payload.get("schema") if isinstance(payload, dict) else None
    model = MODELS.get(schema) if isinstance(schema, str) else None
    if model is None:
        raise LabRejected("unknown_schema", f"{schema!r} is not one of {SCHEMAS}")
    refs = list(_refs(payload))
    for ref in refs:
        if not (isinstance(ref, str) and REF_RE.fullmatch(ref)):
            raise LabRejected("mutable_ref", f"{ref!r} is not an immutable lab ref")
    for ref in refs:
        if REF_RE.fullmatch(ref).group(2) != payload.get("provider_org_id"):
            raise LabRejected("cross_provider_ref", f"{ref} belongs to another provider")
    if _mixed(payload):
        raise LabRejected("mixed_units", "amounts side by side are in different units")
    if not _exact_numbers(payload):
        raise LabRejected("invalid", "a number is non-finite or an integer past 2^53")
    try:
        return model.model_validate(payload)
    except ValidationError as refused:
        raise LabRejected("invalid", str(refused)) from None


def validate(payload: Any) -> str | None:
    """None if `payload` is a valid Lab record, else its refusal reason."""
    try:
        parse(payload)
    except LabRejected as refused:
        return refused.reason
    return None


def _number(value: float) -> str:
    """ECMAScript Number::toString of a finite double (RFC 8785 3.2.2.3)."""
    if value == 0:
        return "0"
    sign, (_, digits, exponent) = "-" * (value < 0), Decimal(repr(abs(value))).normalize().as_tuple()
    s = "".join(map(str, digits))
    k, n = len(s), exponent + len(s)       # value = 0.s * 10^n
    if k <= n <= 21:
        return sign + s + "0" * (n - k)
    if 0 < n <= 21:
        return sign + s[:n] + "." + s[n:]
    if -6 < n <= 0:
        return sign + "0." + "0" * -n + s
    mantissa = s[0] + ("." + s[1:] if k > 1 else "")
    return f"{sign}{mantissa}e{'+' if n > 0 else '-'}{abs(n - 1)}"


def _jcs(node: Any) -> str:
    if isinstance(node, dict):
        keys = sorted(node, key=lambda key: key.encode("utf-16-be"))
        return "{" + ",".join(f"{_jcs(key)}:{_jcs(node[key])}" for key in keys) + "}"
    if isinstance(node, list):
        return "[" + ",".join(map(_jcs, node)) + "]"
    if isinstance(node, float) and math.isfinite(node):
        return _number(node)
    # str, int, bool, None; a non-finite float keeps Python's spelling (a record refuses it,
    # but a replayed model answer may carry one and is only counted and keyed).
    return json.dumps(node, ensure_ascii=False)


def canonical(payload: Any) -> bytes:
    """RFC 8785 (JCS) bytes: the same as `canonicalJson` in `packages/shared/.../fakes.ts`."""
    return _jcs(payload).encode()


def ref_of(payload: dict[str, Any]) -> str:
    """The immutable ref of a valid referable record: its kind, provider, id and digest."""
    record = parse(payload)
    kind, id_field = REFERABLE[record.schema_id]
    digest = hashlib.sha256(canonical(payload)).hexdigest()
    return f"lab:{kind}:{record.provider_org_id}:{payload[id_field]}@sha256:{digest}"


# --- DATA-RIGHTS: the four gates ------------------------------------------------------------
class Gate(enum.StrEnum):
    schedule = "schedule"
    access = "access"
    export = "export"
    external_submission = "external_submission"


# The purposes each gate may check; the first of a one-purpose gate is its default.
GATE_PURPOSES: dict[Gate, tuple[v2.DataPurpose, ...]] = {
    Gate.schedule: (v2.DataPurpose.provider_sharing,),
    Gate.access: (v2.DataPurpose.provider_sharing,),
    Gate.export: (v2.DataPurpose.training,),
    Gate.external_submission: (v2.DataPurpose.external_judging, v2.DataPurpose.training),
}


def authorize(gate: Gate, *, membership: v2.ProviderMembership | None,
              grant: v2.AccessGrant | None, now: datetime, provider_org_id: str,
              model_id: str, category: v2.DataCategory,
              purpose: v2.DataPurpose | str | None = None) -> None:
    """`Forbidden` unless a current membership AND a current grant for `purpose`.

    `purpose` must be one the gate allows; a one-purpose gate defaults to it, and external
    submission takes the `ExternalRun.purpose` of the run being sent (no default). Both
    records come from the server's own reads at the moment of the gate (the L2 port),
    never from a request or a manifest: that is what makes revocation immediate.
    """
    allowed = GATE_PURPOSES[gate]
    if purpose is None and len(allowed) == 1:
        purpose = allowed[0]
    if purpose not in allowed:
        raise errors.Forbidden(f"{gate}: purpose {purpose!r} is not one of {allowed}")
    if not v2.may_read_customer_content(membership=membership, grant=grant, now=now,
                                        provider_org_id=provider_org_id, model_id=model_id,
                                        category=category, purpose=purpose):
        raise errors.Forbidden(f"{gate}: no current membership and {purpose} grant")


# --- ROLLOUT-PIN: a stable cohort -----------------------------------------------------------
def assign(policy: RolloutPolicy, policy_ref: str, subject_key: str,
           request_id: str) -> RolloutAssignment:
    """Bucket a subject (account or session, per `policy.cohort`) by policy id, not version:
    a retry lands the same, and raising a weight only moves baseline subjects over."""
    digest = hashlib.sha256(f"{policy.policy_id}\n{subject_key}".encode()).hexdigest()
    bucket, edge, serving = int(digest[:8], 16) % 10_000, 0, policy.baseline_ref
    for candidate in policy.candidates:
        edge += candidate.weight_bp
        if bucket < edge:
            serving = candidate.serving_ref
            break
    return RolloutAssignment(schema="lab.rollout_assignment.1",
                             provider_org_id=policy.provider_org_id, policy_ref=policy_ref,
                             request_id=request_id, cohort_digest=f"sha256:{digest}",
                             serving_ref=serving, pinned_by="cohort")
