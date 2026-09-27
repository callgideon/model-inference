#!/usr/bin/env python3
"""F3: the Lab dataset / evaluation / training / rollout contracts, both halves.

One fixture file (`packages/shared/contracts/lab/fixtures.json`) drives the Python
validators, the Python fake, the TypeScript validators and the TypeScript fake: a
fixture the two halves read differently fails here (fake/real conformance with the
same fixtures, 13-lab-improvement-handoffs.md F3). Node is required, never skipped.

    uv run --frozen pytest -q tests/contracts/lab/test_lab_contracts.py
"""
from __future__ import annotations

import copy
import functools
import hashlib
import json
import pathlib
import subprocess
from datetime import datetime, timedelta, timezone

import pytest
from infrx.contracts import errors
from infrx.contracts.lab import fakes, records as lab, states
from infrx.contracts.v2 import money_units as mu, records as v2

API = pathlib.Path(__file__).resolve().parents[3]            # apps/infrx-api
REPO = API.parents[1]
SHARED = REPO / "packages" / "shared" / "contracts" / "lab"
FIXTURES = json.loads((SHARED / "fixtures.json").read_text())
ACCEPTED, REJECTED = FIXTURES["accepted"], FIXTURES["rejected"]
PIN = pathlib.Path(__file__).with_name("frozen_contracts.json")

A, B = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"
MODEL = "33333333-3333-4333-8333-333333333333"
NOW = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)


def node(script: str) -> dict:
    """Run an ES module against the TypeScript half (type stripping; no install)."""
    done = subprocess.run(["node", "--no-warnings", "--input-type=module", "-e", script],
                          capture_output=True, text=True, timeout=60, cwd=str(SHARED))
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@functools.cache
def ts() -> dict:
    """The TypeScript half's verdicts, refs, vocabulary and keys over the same fixtures."""
    return node(f"""
import {{ readFileSync }} from "node:fs";
import * as lab from "{(SHARED / 'index.ts').as_uri()}";
import {{ FakeLabCatalog, refOf }} from "{(SHARED / 'fakes.ts').as_uri()}";
const fx = JSON.parse(readFileSync("{SHARED / 'fixtures.json'}", "utf8"));
const out = {{ accepted: {{}}, refs: {{}}, published: {{}}, rejected: [] }};
const catalog = new FakeLabCatalog();
for (const [name, payload] of Object.entries(fx.accepted)) {{
  out.accepted[name] = lab.validate(payload);
  out.refs[name] = lab.REFERABLE[payload.schema] ? refOf(payload) : null;
  if (out.refs[name]) out.published[name] = catalog.publish(payload) === catalog.publish(payload);
}}
for (const item of fx.rejected) out.rejected.push(lab.validate(item.payload));
out.vocab = {{ SCHEMAS: lab.SCHEMAS, REF_KINDS: lab.REF_KINDS, MODALITIES: lab.MODALITIES,
  TRANSITIONS: lab.TRANSITIONS, REASONS: lab.REASONS, MAX_VIDEO_MS: lab.MAX_VIDEO_MS,
  SURFACE_VERSION: lab.SURFACE_VERSION }};
out.reordered = refOf(Object.fromEntries(Object.entries(fx.accepted.rollout_policy).reverse()));
out.keys = [lab.attemptKey("{A}", "{B}", 3), lab.submitKey("{A}")];
console.log(JSON.stringify(out));
""")


def grant(**changes) -> v2.AccessGrant:
    fields = dict(grant_id=MODEL, version=1, grantor_org_id=B, recipient_provider_org_id=A,
                  model_ids=(MODEL,), categories=(v2.DataCategory.request_content,),
                  purposes=(v2.DataPurpose.provider_sharing,), retention_days=30,
                  effective_at=NOW - timedelta(days=1))
    return v2.AccessGrant(**{**fields, **changes})


def member(role=v2.ProviderRole.developer) -> v2.ProviderMembership:
    return v2.ProviderMembership(provider_org_id=A, user_id=MODEL, role=role, granted_by="admin",
                                 granted_at=NOW - timedelta(days=1))


def authorize(gate, **changes):
    args = dict(membership=member(), grant=grant(purposes=(lab.GATE_PURPOSE[gate],)), now=NOW,
                provider_org_id=A, model_id=MODEL, category=v2.DataCategory.request_content)
    return lab.authorize(gate, **{**args, **changes})


# --- F3.a / F3.c: every record, both halves ------------------------------------------------
def test_lab_accepted_fixtures_parse_in_python():
    assert set(ACCEPTED) == {schema.split(".")[1] for schema in lab.SCHEMAS}, "one per schema"
    for name, payload in ACCEPTED.items():
        assert lab.validate(payload) is None, name
        assert lab.parse(payload).schema_id == payload["schema"]


@pytest.mark.parametrize("item", REJECTED, ids=[item["name"] for item in REJECTED])
def test_lab_rejected_fixture_is_refused_with_its_reason(item):
    assert lab.validate(item["payload"]) == item["reason"]
    with pytest.raises(lab.LabRejected) as refused:
        lab.parse(item["payload"])
    assert refused.value.reason == item["reason"]


def test_lab_the_typescript_half_reads_every_fixture_the_same_way():
    assert ts()["accepted"] == {name: None for name in ACCEPTED}
    assert ts()["rejected"] == [item["reason"] for item in REJECTED]
    assert {item["reason"] for item in REJECTED} == set(lab.REASONS)
    assert ts()["vocab"]["REASONS"] == list(lab.REASONS)


def test_lab_both_halves_derive_the_same_immutable_ref():
    """The TS fake and the Python fake name one record identically, and publishing the
    same bytes twice yields one ref on both sides."""
    catalog = fakes.FakeLabCatalog()
    for name, payload in ACCEPTED.items():
        if payload["schema"] not in lab.REFERABLE:
            assert ts()["refs"][name] is None
            continue
        ref = catalog.publish(payload)
        assert ref == lab.ref_of(payload) == ts()["refs"][name], name
        assert catalog.publish(copy.deepcopy(payload)) == ref and ts()["published"][name]
    assert ts()["reordered"] == lab.ref_of(ACCEPTED["rollout_policy"])     # key order is not identity


def test_lab_vocabularies_and_transitions_match_across_halves():
    vocab = ts()["vocab"]
    assert vocab["SCHEMAS"] == list(lab.SCHEMAS)
    assert vocab["REF_KINDS"] == list(lab.REF_KINDS)
    assert vocab["MODALITIES"] == list(lab.MODALITIES)
    assert vocab["MAX_VIDEO_MS"] == lab.MAX_VIDEO_MS == 82_000
    assert vocab["SURFACE_VERSION"] == lab.SURFACE_VERSION
    assert vocab["TRANSITIONS"] == {kind: {state: sorted(targets) for state, targets in table.items()}
                                    for kind, table in states.TRANSITIONS.items()}
    assert ts()["keys"] == [lab.attempt_key(A, B, 3), lab.submit_key(A)]


def test_lab_fixtures_cover_text_finite_video_and_structured_tool_io():
    manifest = lab.parse(ACCEPTED["dataset_manifest"])
    assert [s.modality for s in manifest.samples] == ["text", "finite_video", "structured"]
    assert lab.parse(ACCEPTED["harness_revision"]).tools[0].effect == "read_only"


# --- identity: refs are content digests ----------------------------------------------------
def test_lab_a_ref_is_the_digest_of_the_canonical_record():
    payload = ACCEPTED["dataset_manifest"]
    reordered = dict(reversed(list(payload.items())))
    assert lab.ref_of(reordered) == lab.ref_of(payload)
    changed = copy.deepcopy(payload)
    changed["samples"][0]["group_key"] = "doc-2"
    assert lab.ref_of(changed) != lab.ref_of(payload)
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    assert lab.ref_of(payload) == (f"lab:dataset:{A}:{payload['dataset_id']}"
                                   f"@sha256:{hashlib.sha256(body).hexdigest()}")


def test_lab_resolve_is_provider_scoped_and_digest_exact():
    catalog = fakes.FakeLabCatalog()
    ref = catalog.publish(ACCEPTED["dataset_manifest"])
    assert catalog.resolve(ref, provider_org_id=A).dataset_id == ACCEPTED["dataset_manifest"]["dataset_id"]
    with pytest.raises(errors.NotFound):
        catalog.resolve(ref, provider_org_id=B)
    with pytest.raises(errors.NotFound):
        catalog.resolve(ref[:-64] + "0" * 64, provider_org_id=A)


# --- F3.b: state machines, keys, units ------------------------------------------------------
def test_lab_transitions_allow_only_the_declared_moves():
    for kind, table in states.TRANSITIONS.items():
        every = set(table) | {s for targets in table.values() for s in targets}
        for current in every:
            for target in every:
                if target in table.get(current, ()):
                    states.transition(kind, current, target)
                else:
                    with pytest.raises(errors.StateConflict):
                        states.transition(kind, current, target)
    for kind, terminal in (("run", "succeeded"), ("case", "done"), ("attempt", "expired"),
                           ("checkpoint", "evaluated"), ("annotation", "superseded")):
        assert states.is_terminal(kind, terminal)
    assert not states.is_terminal("run", "running") and not states.is_terminal("run", "unknown")
    with pytest.raises(errors.StateConflict):
        states.transition("unknown", "queued", "running")


def test_lab_an_ambiguous_submit_is_reconciled_never_resubmitted():
    assert states.transition("external_run", "submitting", "ambiguous") == "ambiguous"
    for reconciled in ("submitted", "failed"):
        assert states.transition("external_run", "ambiguous", reconciled) == reconciled
    for retry in ("submitting", "prepared"):
        with pytest.raises(errors.StateConflict):
            states.transition("external_run", "ambiguous", retry)


def test_lab_idempotency_keys_are_stable_and_distinct():
    assert lab.attempt_key(A, B, 1) == lab.attempt_key(A, B, 1) == f"attempt:{A}:{B}:1"
    assert len({lab.attempt_key(A, B, 1), lab.attempt_key(A, B, 2), lab.attempt_key(B, A, 1)}) == 3
    assert lab.submit_key(A) == f"submit:{A}"


def test_lab_budgets_are_exact_and_typed_by_unit():
    run = lab.parse(ACCEPTED["eval_run"])
    credit, provider = run.budgets
    assert isinstance(credit.limit.amount, mu.Credit) and isinstance(provider.limit.amount, mu.ProviderUsd)
    assert provider.reserved.amount.raw(mu.PROVIDER_USD) == mu.ProviderUsd("1.5").raw(mu.PROVIDER_USD)
    full = copy.deepcopy(ACCEPTED["eval_run"])
    full["budgets"][1]["reserved"]["value"] = "25.00000000"       # the cap itself is reservable
    assert lab.validate(full) is None


# --- DATA-RIGHTS: server-derived authorization at every gate --------------------------------
@pytest.mark.parametrize("gate", list(lab.Gate))
def test_lab_every_gate_needs_the_current_grant_for_its_purpose(gate):
    assert authorize(gate) is None
    with pytest.raises(errors.Forbidden):        # revoked after selection, before the read
        authorize(gate, grant=grant(purposes=(lab.GATE_PURPOSE[gate],), revoked_at=NOW))
    other = next(p for p in v2.DataPurpose if p is not lab.GATE_PURPOSE[gate])
    with pytest.raises(errors.Forbidden):        # capture/sharing never implies another purpose
        authorize(gate, grant=grant(purposes=(other,)))
    with pytest.raises(errors.Forbidden):
        authorize(gate, provider_org_id=B)


def test_lab_the_gates_name_four_distinct_permissions():
    assert {g.value for g in lab.Gate} == {"schedule", "access", "export", "external_submission"}
    assert lab.GATE_PURPOSE[lab.Gate.export] is v2.DataPurpose.training
    assert lab.GATE_PURPOSE[lab.Gate.external_submission] is v2.DataPurpose.external_judging


def test_lab_a_role_alone_never_authorizes_data_reuse():
    for role in v2.ProviderRole:
        with pytest.raises(errors.Forbidden):
            authorize(lab.Gate.access, membership=member(role), grant=None)
    with pytest.raises(errors.Forbidden):
        authorize(lab.Gate.access, membership=None)


def test_lab_an_immutable_manifest_confers_no_continued_access():
    catalog = fakes.FakeLabCatalog()
    ref = catalog.publish(ACCEPTED["dataset_manifest"])
    assert catalog.resolve(ref, provider_org_id=A)          # the manifest stays readable
    with pytest.raises(errors.Forbidden):                     # its content does not
        authorize(lab.Gate.access, grant=grant(revoked_at=NOW - timedelta(seconds=1)))


# --- ROLLOUT-PIN ----------------------------------------------------------------------------
def test_lab_rollout_assignment_is_a_stable_cohort():
    policy = lab.parse(ACCEPTED["rollout_policy"])
    ref = lab.ref_of(ACCEPTED["rollout_policy"])
    subjects = [f"account-{n}" for n in range(2000)]
    first = [lab.assign(policy, ref, s, A) for s in subjects]
    assert first == [lab.assign(policy, ref, s, A) for s in subjects]  # retries land the same
    candidate = policy.candidates[0].serving_ref
    share = sum(a.serving_ref == candidate for a in first) / len(first)
    assert 0.03 < share < 0.07                                          # 500 bp, not 0 or all
    assert all(a.pinned_by == "cohort" and a.policy_ref == ref for a in first)
    wider = policy.model_copy(update={"candidates": [policy.candidates[0].model_copy(
        update={"weight_bp": 2000})], "version": 4})
    moved = [lab.assign(wider, ref, s, A).serving_ref for s in subjects]
    assert all(m == candidate for a, m in zip(first, moved) if a.serving_ref == candidate)
    assert sum(m == candidate for m in moved) > 3 * sum(a.serving_ref == candidate for a in first)
    off = policy.model_copy(update={"candidates": []})
    assert {lab.assign(off, ref, s, A).serving_ref for s in subjects} == {policy.baseline_ref}


# --- frozen contracts stay byte-compatible --------------------------------------------------
def frozen_files() -> dict[str, str]:
    roots = (API / "infrx" / "contracts", REPO / "apps" / "app" / "lib" / "contracts")
    found = {}
    for root in roots:
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(REPO).as_posix()
            if (path.is_file() and "__pycache__" not in path.parts
                    and not relative.startswith("apps/infrx-api/infrx/contracts/lab/")):
                found[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


def test_lab_existing_frozen_contracts_are_byte_identical():
    """Every v1/v2 contract file (Python and console) hashes to its value at the F3 base
    9a6c3685; only `infrx/contracts/lab/` is new. A deliberate revision updates the pin."""
    pinned = json.loads(PIN.read_text())
    assert pinned["base"] == "9a6c3685"
    assert frozen_files() == pinned["files"]
