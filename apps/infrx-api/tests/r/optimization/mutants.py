#!/usr/bin/env python3
"""R32/R83 for R3: one single-edit defect per decision `test_optimization.py` claims, through
the shared runner with `require_every_case`.

    uv run --frozen pytest -q tests/r/optimization/test_mutants.py                     # subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/r/optimization/test_mutants.py   # all
"""
from __future__ import annotations

import ast
import pathlib
import sys

API_DIR = pathlib.Path(__file__).resolve().parents[3]
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from tests.contracts import mutants as shared  # noqa: E402
from tests.contracts.mutants import Mutant, Result, Runner  # noqa: E402

SUITE = "tests/r/optimization/test_optimization.py"
P = "rollouts/optimization/__init__.py"

ID = "test_r3_a_variant_is_an_immutable_distinct_serving_identity"
W3 = "test_r3_registration_checks_the_engine_through_w3"
WORK = "test_r3_mismatched_workloads_are_refused"
GPU = "test_r3_gpu_evidence_is_only_an_experiment_branch_result"
BOUND = "test_r3_the_report_and_identities_are_bound"
EQ = "test_r3_an_equivalent_measured_variant_claims_its_optimization"
SLICE = "test_r3_better_throughput_cannot_override_a_failed_slice"
TOK = "test_r3_a_tokenizer_change_or_a_lost_capability_is_never_equivalent"
UNMEAS = "test_r3_unmeasured_or_inconclusive_claims_nothing"
DIGEST = '_digest(identity.model_dump(mode="json"))'


def m(name, invariant, old, new, *cases, dies_by=(), occurrences=1):
    return Mutant(name=name, invariant=invariant, file=P, old=old, new=new, cases=cases,
                  dies_by=dies_by, occurrences=occurrences)


MUTANTS: tuple[Mutant, ...] = (
    # --- R3.a the serving identity
    m("r3_caps_order_matters", "capabilities are a set",
      "AfterValidator(lambda v: sorted(set(v)))", "AfterValidator(lambda v: v)", ID),
    m("r3_caps_empty", "a variant serves something",
      "Field(min_length=1),", "Field(min_length=0),", W3),
    *(m(f"r3_ref_ignores_{field}", f"the {field} is part of the serving identity",
        DIGEST, f'_digest(identity.model_dump(mode="json", exclude={{"{field}"}}))', ID)
      for field in ("engine_version", "hardware", "preprocessor", "engine", "served_model")),
    m("r3_changes_all_fields", "changes name only what differs",
      "for k, v in variant.model_dump().items() if was[k] != v]",
      "for k, v in variant.model_dump().items()]", ID),
    m("r3_payload_unvalidated", "the same identity is no variant (F3 refuses it)",
      "    lab.parse(payload)  ", "    pass  ", ID),
    # --- R3.a W3's probe
    m("r3_probe_skipped", "the engine is asked, not believed",
      "    probe = await engine.capabilities()\n",
      '    probe = {"version": variant.engine_version, "models": [variant.served_model]}\n', W3),
    m("r3_version_unchecked", "the engine runs the declared version",
      'if probe.get("version") != variant.engine_version or', "if False or", W3),
    m("r3_model_unchecked", "the engine serves the declared model",
      'variant.served_model not in probe.get("models", [])', "False", W3),
    # --- R3.b the workload
    m("r3_h1_skipped", "the runs share one case universe (H1)",
      "    pair_runs(runs[0], runs[1])\n", "", WORK),
    m("r3_serving_unchecked", "the runs serve the base and then the variant",
      '    if (runs[0]["serving_ref"], runs[1]["serving_ref"]) != refs:\n', "    if False:\n",
      WORK),
    m("r3_profile_unchecked", "both sides ran one load profile",
      "    if b.profile_digest != v.profile_digest:\n", "    if False:\n", WORK),
    m("r3_load_ref_unchecked", "a measurement is of the registered serving",
      "(ref, ident.engine_version, ident.hardware)",
      "(load.serving_ref, ident.engine_version, ident.hardware)", WORK),
    m("r3_load_engine_unchecked", "a measurement is on the registered engine version",
      "(ref, ident.engine_version, ident.hardware)", "(ref, load.engine_version, ident.hardware)",
      WORK),
    m("r3_load_hardware_unchecked", "a measurement is on the registered hardware",
      "(ref, ident.engine_version, ident.hardware)", "(ref, ident.engine_version, load.hardware)",
      WORK),
    m("r3_loads_unvalidated", "a load record is validated",
      "b, v = (Load.model_validate(x) for x in loads)",
      "b, v = (Load.model_construct(**x) for x in loads)", GPU),
    m("r3_source_pattern_loose", "a result is pinned at a commit",
      r"/results/\S+@[0-9a-f]{{7,40}}", r"/results/\S+", GPU),
    m("r3_source_unanchored", "the whole source is a results path",
      'pattern=f"^{RESULTS.pattern}$"', "pattern=RESULTS.pattern", GPU),
    m("r3_source_any_experiment", "only the five experiments' results count",
      "(?:{'|'.join(EXPERIMENTS)})", "[a-z0-9]+", GPU),
    m("r3_source_no_models_prefix", "the models/<exp>/results layout counts too",
      'rf"(?:models/)?(?:', 'rf"(?:', GPU),
    # --- R3.b the report and identities are bound
    m("r3_identity_unbound", "identities re-derive to the registered refs",
      "            serving_ref(record.provider_org_id, candidate)) != refs:\n",
      "            serving_ref(record.provider_org_id, candidate)) != refs and False:\n", BOUND),
    m("r3_report_digest_unchecked", "a tampered report is refused",
      'if report.get("report_digest") != _digest(body) or', "if False or", BOUND),
    m("r3_report_runs_unchecked", "the report names these runs",
      '            (report.get("baseline_run"), report.get("candidate_run")) != \\\n'
      "            (lab.ref_of(runs[0]), lab.ref_of(runs[1])):\n", "            False:\n", BOUND),
    # --- R3.c the verdict
    m("r3_reject_overridden", "a failed required slice rejects whatever the throughput",
      'outcome = "rejected" if decided == "reject" else', 'outcome = "rejected" if False else',
      SLICE),
    m("r3_tokenizer_ignored", "a changed tokenizer is never equivalent",
      '(["tokenizer_changed"] if base.tokenizer_digest != candidate.tokenizer_digest else [])',
      "[]", TOK),
    m("r3_capability_loss_ignored", "a lost capability is never equivalent",
      '[f"capability_lost:{c}" for c in base.capabilities if c not in candidate.capabilities]',
      "[]", TOK),
    m("r3_capability_gain_refused", "an added capability is reported, not refused",
      "for c in base.capabilities if c not in candidate.capabilities]",
      "for c in sorted(set(base.capabilities) ^ set(candidate.capabilities))]", TOK),
    m("r3_capabilities_misreported", "both sides' capabilities are reported",
      '"variant": candidate.capabilities}', '"variant": base.capabilities}', TOK),
    m("r3_inconclusive_equivalent", "a non-accepting report is inconclusive",
      '"inconclusive" if decided != "accept" else "equivalent"', '"equivalent"', UNMEAS),
    m("r3_claim_unmeasured", "an optimization is claimed only when measured",
      'outcome == "equivalent" and performance is not None', 'outcome == "equivalent"', UNMEAS),
    m("r3_claim_any_outcome", "an optimization is claimed only when equivalent",
      'outcome == "equivalent" and performance is not None', "performance is not None", SLICE),
    m("r3_reasons_dropped", "the report's reasons are kept",
      '"reasons": reasons if decided == "reject" else unlike + reasons', '"reasons": unlike',
      SLICE, UNMEAS),
    m("r3_ratio_inverted", "the throughput ratio is variant over base",
      "v.throughput_tok_s / b.throughput_tok_s", "b.throughput_tok_s / v.throughput_tok_s", EQ),
    m("r3_p99_sign", "the p99 delta is variant - base",
      "v.p99_ms - b.p99_ms", "b.p99_ms - v.p99_ms", EQ),
    m("r3_memory_sign", "the memory delta is variant - base",
      "v.memory_gib - b.memory_gib", "b.memory_gib - v.memory_gib", EQ),
    m("r3_sources_dropped", "the measurements name their sources",
      '"sources": [b.source, v.source]}', '"sources": []}', EQ),
    m("r3_variant_ref", "the comparison names the variant record",
      '"variant_ref": lab.ref_of(variant),', '"variant_ref": refs[1],', EQ),
    m("r3_report_unnamed", "the comparison names its report",
      '"report_digest": report["report_digest"], "performance"',
      '"report_digest": None, "performance"', EQ),
)


def case_names() -> set[str]:
    tree = ast.parse((API_DIR / SUITE).read_text())
    return {node.name for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")}


RUNNER = Runner(name="r3", targets=(SUITE,), require_every_case=True)


def run_mutant(mutant: Mutant) -> Result:
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run the R3 optimization mutation list"))
