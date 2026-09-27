#!/usr/bin/env python3
"""R32/R83 for F3: one single-edit defect per decision `tests/contracts/lab` claims.

Python mutants run through the shared runner (`tests/contracts/mutants.py`) with its
rules unchanged, plus the stricter `require_every_case`. TypeScript mutants cannot go
through it (it compiles the mutated file as Python), so `run_ts_mutant` applies the
same rules to a `.ts` edit: the one edit lands exactly `occurrences` times in a copy,
the named cases pass unmutated first, then fail, only they fail, every one of them
notices, and every death is an assertion or a typed `DomainError`.

The copy keeps the repository's shape: `apps/infrx-api/{infrx,tests,pyproject.toml}`,
`packages/shared` (copied: TS mutants edit it) and `apps/app` (linked, read only: the
frozen-contract pin hashes its contracts).

    uv run --frozen pytest -q tests/contracts/lab/test_mutants.py                     # subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/contracts/lab/test_mutants.py   # all
    uv run --frozen python -m tests.contracts.lab.mutants --list
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import tempfile

API_DIR = pathlib.Path(__file__).resolve().parents[3]
REPO = API_DIR.parents[1]
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from tests.contracts import mutants as shared  # noqa: E402
from tests.contracts.mutants import Mutant, Outcome, Result, Runner  # noqa: E402,F401

SUITE = "tests/contracts/lab/test_lab_contracts.py"
R, S, F = "contracts/lab/records.py", "contracts/lab/states.py", "contracts/lab/fakes.py"
TI, TF = "packages/shared/contracts/lab/index.ts", "packages/shared/contracts/lab/fakes.ts"

ACC = "test_lab_accepted_fixtures_parse_in_python"
REJ = "test_lab_rejected_fixture_is_refused_with_its_reason"
TS = "test_lab_the_typescript_half_reads_every_fixture_the_same_way"
REF2 = "test_lab_both_halves_derive_the_same_immutable_ref"
VOCAB = "test_lab_vocabularies_and_transitions_match_across_halves"
COVER = "test_lab_fixtures_cover_text_finite_video_and_structured_tool_io"
DIGEST = "test_lab_a_ref_is_the_digest_of_the_canonical_record"
FREE = "test_lab_free_form_content_is_opaque_to_the_ref_and_unit_scans"
JCS = "test_lab_canonical_json_is_rfc8785_in_both_halves"
EXTSUB = "test_lab_external_submission_is_authorized_for_the_runs_own_purpose"
RESOLVE = "test_lab_resolve_is_provider_scoped_and_digest_exact"
TRANS = "test_lab_transitions_allow_only_the_declared_moves"
AMBIG = "test_lab_an_ambiguous_submit_is_reconciled_never_resubmitted"
KEYS = "test_lab_idempotency_keys_are_stable_and_distinct"
BUDGET = "test_lab_budgets_are_exact_and_typed_by_unit"
GATE = "test_lab_every_gate_needs_the_current_grant_for_its_purpose"
FOUR = "test_lab_the_gates_name_four_distinct_permissions"
ROLE = "test_lab_a_role_alone_never_authorizes_data_reuse"
MANIFEST = "test_lab_an_immutable_manifest_confers_no_continued_access"
COHORT = "test_lab_rollout_assignment_is_a_stable_cohort"
FROZEN = "test_lab_existing_frozen_contracts_are_byte_identical"


# `LabRejected` is a typed `DomainError` (an `InvalidRequest`), but the shared runner lists
# the hierarchy when it is imported, before this package exists in its process; the mutants
# whose honest death is the validator refusing a fixture declare it.
REFUSED = ("LabRejected",)


def m(name, invariant, file, old, new, *cases, occurrences=1, dies_by=()):
    return Mutant(name=name, invariant=invariant, file=file, old=old, new=new, cases=cases,
                  occurrences=occurrences, dies_by=dies_by)


MUTANTS: tuple[Mutant, ...] = (
    # --- the validator: reasons and their order ----------------------------------------------
    m("lab_unknown_version_read_as_v1", "an unknown schema version is refused, never upgraded", R,
      "model = MODELS.get(schema) if", 'model = MODELS.get(schema.rsplit(".", 1)[0] + ".1") if', REJ),
    m("lab_ref_accepts_a_label", "a ref without a sha256 digest is mutable", R,
      'r"@sha256:([0-9a-f]{64})")', 'r"@(?:sha256:)?([0-9a-z]{6,64})")', REJ),
    m("lab_provider_segment_ignored", "every ref belongs to the record's provider", R,
      'if REF_RE.fullmatch(ref).group(2) != payload.get("provider_org_id"):', "if False:", REJ),
    m("lab_units_side_by_side_unchecked", "amounts side by side share one unit", R,
      "if _mixed(payload):", "if False:", REJ),
    m("lab_refs_scan_free_form", "a tool schema, mapping, output or label holds no Lab refs", R,
      "            if key in OPAQUE:", "            if False:", ACC, REF2, FREE, dies_by=REFUSED),
    m("lab_units_scan_free_form", "units inside free-form content are data, not amounts", R,
      "values = [v for k, v in node.items() if k not in OPAQUE]", "values = list(node.values())",
      ACC, REF2, FREE, dies_by=REFUSED),
    m("lab_unsafe_integer_accepted", "an integer past 2^53 is refused (the TS half cannot read it)", R,
      "return isinstance(node, bool) or not isinstance(node, int) or abs(node) <= SAFE_INT",
      "return True", REJ),
    m("lab_non_finite_accepted", "a non-finite number is refused", R,
      "return math.isfinite(node) and (not node.is_integer() or abs(node) <= SAFE_INT)",
      "return not node.is_integer() or abs(node) <= SAFE_INT", JCS),
    m("lab_ref_kind_unchecked", "a ref names the kind its field expects", R,
      "if match is None or match.group(1) not in kinds:", "if match is None:", REJ),
    m("lab_lax_types", "'7' is not a seed (strict, like the TS half)", R,
      "strict=True", "strict=False", REJ),
    m("lab_extra_fields_ignored", "a field the contract does not name is refused", R,
      'extra="forbid"', 'extra="ignore"', REJ),
    # --- F3.a datasets ------------------------------------------------------------------------
    m("lab_video_cap_moved", "a finite video is at most 82 s", R,
      "MAX_VIDEO_MS = 82_000", "MAX_VIDEO_MS = 82_001", REJ, VOCAB),
    m("lab_duration_on_any_modality", "only finite video carries a duration", R,
      'if (self.modality == "finite_video") != (self.duration_ms is not None):', "if False:", REJ),
    m("lab_split_membership_as_a_set", "every sample is in exactly one split", R,
      "sorted(placed) != sorted(ids)", "set(placed) != set(ids)", REJ),
    m("lab_group_spans_splits", "a group never spans two splits", R,
      'raise ValueError(f"group {sample.group_key!r} spans two splits")', "pass", REJ),
    m("lab_derivation_without_parents", "a derived version names its parents", R,
      'if (self.derivation == "derive") != bool(self.parent_refs):', "if False:", REJ),
    m("lab_one_modality_dropped", "text, finite video and structured I/O are all samples", R,
      "modality: Literal[MODALITIES]", "modality: Literal[MODALITIES[:2]]",
      ACC, COVER, REF2, DIGEST, RESOLVE, MANIFEST, dies_by=REFUSED),
    # --- F3.b budgets, keys, runs -------------------------------------------------------------
    m("lab_payer_optional", "PROVIDER_USD work names its payer", R,
      'if self.limit.unit == "PROVIDER_USD" and self.payer_ref is None:', "if False:", REJ),
    m("lab_reservation_unbounded", "a reservation never exceeds its limit", R,
      "if _units(self.reserved.value) > _units(self.limit.value):", "if False:", REJ),
    m("lab_cap_not_reservable", "the limit itself is reservable (no off-by-one)", R,
      "if _units(self.reserved.value) > _units(self.limit.value):",
      "if _units(self.reserved.value) >= _units(self.limit.value):", BUDGET),
    m("lab_amount_unit_swapped", "an amount is typed by its own unit", R,
      '(Credit if self.unit == "CREDIT" else ProviderUsd)', '(ProviderUsd if self.unit == "CREDIT" else Credit)',
      BUDGET),
    m("lab_eval_against_prod", "evaluation never targets production", R,
      'environment: Literal["dev"]', 'environment: Literal["dev", "prod"]', REJ),
    m("lab_run_key_unchecked", "a run's key is derived from its id", R,
      "if self.idempotency_key != run_key(self.run_id):", "if False:", REJ),
    m("lab_two_budgets_one_unit", "one budget per unit", R,
      "if len(set(units)) != len(units):", "if False:", REJ),
    m("lab_attempt_key_loses_the_attempt", "each attempt has its own stable key", R,
      'return f"attempt:{run_id}:{case_id}:{attempt}"', 'return f"attempt:{run_id}:{case_id}"',
      KEYS, VOCAB, ACC),
    m("lab_attempt_key_unchecked", "an attempt's key is derived, not chosen", R,
      "if self.idempotency_key != attempt_key(", "if False and self.idempotency_key != attempt_key(", REJ),
    m("lab_submit_key_per_retry", "one external submission key per run", R,
      'return f"submit:{external_run_id}"', 'return f"submit:{external_run_id}:1"',
      KEYS, VOCAB, ACC, REF2, dies_by=REFUSED),
    m("lab_external_paid_in_credit", "external work is PROVIDER_USD", R,
      'if self.budget.limit.unit != "PROVIDER_USD":', "if False:", REJ),
    m("lab_synthetic_ground_truth", "a synthetic label is never ground truth", R,
      'if self.method == "synthetic" and self.ground_truth:', "if False:", REJ),
    m("lab_human_label_unreviewed", "a human label names its reviewer", R,
      'if self.method == "human" and self.reviewer_id is None:', "if False:", REJ),
    # --- F3.c rollout -------------------------------------------------------------------------
    m("lab_weights_over_full", "candidates never exceed 10000 bp", R,
      "if total > 10_000:", "if total > 10_001:", REJ),
    m("lab_shadow_serves_traffic", "only a canary serves candidate traffic", R,
      'if self.mode != "canary" and total:', "if False:", REJ),
    m("lab_expand_without_evidence", "expansion names its evidence", R,
      'if self.decision == "expand" and not self.evidence_refs:', "if False:", REJ),
    m("lab_variant_is_its_base", "a variant is a different serving revision", R,
      "if self.base_serving_ref == self.variant_serving_ref:", "if False:", REJ),
    m("lab_cohort_by_version", "the cohort is stable across policy versions", R,
      'f"{policy.policy_id}\\n{subject_key}"', 'f"{policy.version}\\n{subject_key}"', COHORT),
    m("lab_bucket_scale_wrong", "weights are basis points of 10000", R,
      "int(digest[:8], 16) % 10_000", "int(digest[:8], 16) % 1_000", COHORT),
    # --- identity -----------------------------------------------------------------------------
    m("lab_digest_depends_on_key_order", "a ref is the digest of canonical (sorted) JSON", R,
      'keys = sorted(node, key=lambda key: key.encode("utf-16-be"))', "keys = list(node)",
      DIGEST, REF2, JCS),
    m("lab_digest_keys_by_code_point", "keys sort by UTF-16 code unit, as the TS half does", R,
      'keys = sorted(node, key=lambda key: key.encode("utf-16-be"))', "keys = sorted(node)",
      REF2, JCS),
    m("lab_digest_numbers_as_python", "numbers are spelled as ECMAScript does (1.0 is 1)", R,
      "        return _number(node)", "        return repr(node)", REF2, JCS),
    m("lab_digest_small_exponent_as_fixed", "1e-7 is exponent form, 1e-6 fixed", R,
      "if -6 < n <= 0:", "if -7 < n <= 0:", REF2, JCS),
    m("lab_digest_of_the_id_only", "a ref's digest covers the whole record", R,
      "hashlib.sha256(canonical(payload)).hexdigest()", 'hashlib.sha256(payload[id_field].encode()).hexdigest()',
      DIGEST, REF2),
    m("lab_resolve_across_providers", "another provider's ref is not_found", F,
      "if record is None or record.provider_org_id != provider_org_id:", "if record is None:", RESOLVE),
    # --- F3.b state machines ------------------------------------------------------------------
    m("lab_ambiguous_resubmits", "an ambiguous submit is reconciled, never resubmitted", S,
      '"ambiguous": frozenset({"submitted", "failed"}),',
      '"ambiguous": frozenset({"submitted", "failed", "submitting"}),', AMBIG, VOCAB),
    m("lab_self_loops_allowed", "only declared moves; no silent no-op", S,
      "if target not in TRANSITIONS.get(kind, {}).get(current, frozenset()):",
      "if target not in TRANSITIONS.get(kind, {}).get(current, frozenset()) and current != target:", TRANS),
    m("lab_every_state_terminal", "a state with exits is not terminal", S,
      "return state in STATES.get(kind, ()) and state not in TRANSITIONS[kind]",
      "return state in STATES.get(kind, ())", TRANS),
    # --- DATA-RIGHTS --------------------------------------------------------------------------
    m("lab_gate_open", "every gate needs a current membership and grant", R,
      "if not v2.may_read_customer_content(", "if False and not v2.may_read_customer_content(",
      GATE, ROLE, MANIFEST, EXTSUB),
    m("lab_export_is_sharing", "export needs a training grant", R,
      "Gate.export: (v2.DataPurpose.training,),", "Gate.export: (v2.DataPurpose.provider_sharing,),", FOUR),
    m("lab_submission_is_sharing", "external submission needs a judging or training grant", R,
      "Gate.external_submission: (v2.DataPurpose.external_judging, v2.DataPurpose.training),",
      "Gate.external_submission: (v2.DataPurpose.provider_sharing,),", FOUR, EXTSUB),
    m("lab_submission_is_judging_only", "an external training run is submittable (0-LC-1 table)", R,
      "Gate.external_submission: (v2.DataPurpose.external_judging, v2.DataPurpose.training),",
      "Gate.external_submission: (v2.DataPurpose.external_judging,),", FOUR, EXTSUB),
    m("lab_submission_checks_first_purpose", "the gate checks the run's purpose, not judging (0-LC-1)", R,
      "category=category, purpose=purpose):", "category=category, purpose=allowed[0]):", GATE, EXTSUB),
    m("lab_gate_purpose_unchecked", "a gate checks only the purposes it allows", R,
      "if purpose not in allowed:", "if False:", EXTSUB),
    m("lab_submission_defaults_to_judging", "external submission names its purpose; no default", R,
      "if purpose is None and len(allowed) == 1:", "if purpose is None:", EXTSUB),
    # --- vocabulary parity and the frozen v1/v2 bytes ------------------------------------------
    m("lab_modality_added_one_side", "both halves declare the same modalities", R,
      'MODALITIES = ("text", "finite_video", "structured")',
      'MODALITIES = ("text", "finite_video", "structured", "stream")', VOCAB),
    m("lab_frozen_contract_edited", "v1/v2 contract files stay byte-identical", "contracts/money.py",
      "from __future__ import annotations", "from __future__ import annotations  # edited", FROZEN),
)

TS_MUTANTS: tuple[Mutant, ...] = (
    m("ts_unknown_schema_reads_as_invalid", "the TS half refuses unknown schemas by name", TI,
      'return "unknown_schema";', 'return "invalid";', TS),
    m("ts_ref_accepts_a_label", "the TS half refuses mutable refs", TI,
      "@sha256:([0-9a-f]{64})$`);", "@(sha256:)?([0-9a-z]{6,64})$`);", TS),
    m("ts_provider_segment_ignored", "the TS half refuses cross-provider refs", TI,
      "if (!found.every((ref) => (REF_RE.exec(ref) as RegExpExecArray)[2] === p.provider_org_id)) {",
      "if (false) {", TS),
    m("ts_units_unchecked", "the TS half refuses mixed units", TI,
      'if (mixed(p)) return "mixed_units";', 'if (false) return "mixed_units";', TS),
    m("ts_refs_scan_free_form", "the TS half reads no refs inside free-form content", TI,
      "      if (opaque(key)) continue;\n", "", TS, REF2, FREE),
    m("ts_units_scan_free_form", "the TS half reads no amounts inside free-form content", TI,
      "Object.entries(node).filter(([key]) => !opaque(key)).map(([, v]) => v)", "Object.values(node)",
      TS, REF2, FREE),
    m("ts_unsafe_integer_accepted", "the TS half refuses integers past 2^53", TI,
      "(!Number.isInteger(node) || Number.isSafeInteger(node))", "true", TS),
    m("ts_non_finite_accepted", "the TS half refuses non-finite numbers", TI,
      "(Number.isFinite(node) && (", "(true && (", JCS),
    m("ts_ref_kind_unchecked", "the TS half checks ref kinds", TI,
      "return match !== null && (kinds as string[]).includes(match[1]);", "return match !== null;", TS),
    m("ts_lax_integers", "the TS half does not coerce '7'", TI,
      "(v) => Number.isInteger(v) && v >= min && v <= max;",
      "(v) => Number.isInteger(Number(v)) && Number(v) >= min && Number(v) <= max;", TS),
    m("ts_extra_keys_allowed", "the TS half refuses unnamed fields", TI,
      "Object.keys(v).every((key) => key in required", "Object.keys(v).every((key) => true || key in required", TS),
    m("ts_video_cap_ignored", "the TS half caps finite video at 82 s", TI,
      "(duration >= 1 && duration <= MAX_VIDEO_MS)", "(duration >= 1)", TS),
    m("ts_split_membership_unchecked", "the TS half places each sample in one split", TI,
      "|| JSON.stringify([...placed].sort()) !== JSON.stringify([...ids].sort())) return false;",
      ") return false;", TS),
    m("ts_payer_optional", "the TS half requires a PROVIDER_USD payer", TI,
      '(b.limit.unit !== "PROVIDER_USD" || (b.payer_ref ?? null) !== null)', "true", TS),
    m("ts_reservation_unbounded", "the TS half bounds a reservation by its limit", TI,
      "\n    && units(b.reserved.value) <= units(b.limit.value));", ");", TS),
    m("ts_synthetic_ground_truth", "the TS half refuses synthetic ground truth", TI,
      '\n    && !(a.method === "synthetic" && a.ground_truth)),', "),", TS),
    m("ts_shadow_serves_traffic", "the TS half keeps shadow traffic at 0", TI,
      '(p.mode === "canary" || total === 0)', "true", TS),
    m("ts_ambiguous_resubmits", "the TS table never resubmits after ambiguous", TI,
      'ambiguous: ["failed", "submitted"],', 'ambiguous: ["failed", "submitted", "submitting"],', VOCAB),
    m("ts_attempt_key_loses_the_attempt", "the TS attempt key names the attempt", TI,
      "`attempt:${runId}:${caseId}:${attempt}`", "`attempt:${runId}:${caseId}`", VOCAB, TS),
    m("ts_canonical_unsorted", "the TS ref digests sorted-key JSON", TF,
      ".sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))", "", REF2, JCS),
)


def case_names() -> set[str]:
    import ast
    tree = ast.parse((API_DIR / SUITE).read_text())
    return {node.name for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")}


def _layout(root: pathlib.Path) -> pathlib.Path:
    api = root / "apps" / "infrx-api"
    junk = shutil.ignore_patterns("__pycache__", "node_modules")
    for name in ("infrx", "tests"):
        shutil.copytree(API_DIR / name, api / name, ignore=junk)
    shutil.copy2(API_DIR / "pyproject.toml", api / "pyproject.toml")
    shutil.copytree(REPO / "packages" / "shared", root / "packages" / "shared", ignore=junk)
    (root / "apps" / "app").symlink_to(REPO / "apps" / "app")
    return api


RUNNER = Runner(name="lab-contracts", targets=(SUITE,), layout=_layout, require_every_case=True)


def run_mutant(mutant: Mutant) -> Result:
    return shared.run_mutant(mutant, RUNNER)


def run_ts_mutant(mutant: Mutant) -> Result:
    """The shared rules, for an edit to a TypeScript file (relative to the repo root)."""
    refused = shared.pristine(tuple(sorted({c for t in TS_MUTANTS for c in t.cases})), RUNNER)
    if refused is not None:
        return refused
    with tempfile.TemporaryDirectory(prefix=f"lab-ts-mutant-{mutant.name}-") as tmp:
        root = pathlib.Path(tmp)
        api = _layout(root)
        target = root / mutant.file
        source = target.read_text()
        if source.count(mutant.old) != mutant.occurrences:
            return Result(Outcome.misdeclared, f"anchor appears {source.count(mutant.old)} times")
        target.write_text(source.replace(mutant.old, mutant.new))
        try:
            done = shared._pytest(root, api, RUNNER, RUNNER.targets, " or ".join(mutant.cases),
                                  RUNNER.timeout_s)
        except subprocess.TimeoutExpired:
            return Result(Outcome.broken_runner, "timed out")
    stdout = done.stdout or ""
    summary = (stdout.strip().splitlines() or ["no output"])[-1]
    failed, errored = shared._failing_ids(stdout)
    if done.returncode == shared.PYTEST_ALL_PASSED:
        return Result(Outcome.survived, summary)
    if done.returncode != shared.PYTEST_TESTS_FAILED or errored:
        return Result(Outcome.broken_runner, f"pytest exit {done.returncode}: {summary}")
    stray = [t for t in failed if shared.case_of(t, mutant.cases) is None]
    if stray:
        return Result(Outcome.broken_runner, f"failures outside the named cases: {stray[:3]}")
    unproven = set(mutant.cases) - {shared.case_of(t, mutant.cases) for t in failed}
    if unproven:
        return Result(Outcome.misdeclared, f"named cases that did not notice: {sorted(unproven)}")
    crashed = shared._undeclared(shared._death_kinds(stdout), set(shared.HONEST_DEATHS))
    if crashed:
        return Result(Outcome.broken_runner, f"undeclared exception deaths {crashed}")
    return Result(Outcome.killed, summary)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run the F3 Lab contracts mutation list"))
