#!/usr/bin/env python3
"""R32/R40/R83 for P1: one single-edit defect per decision `test_annotations.py` claims,
through the shared runner with `require_every_case`. The PostgreSQL rerun
(`test_annotations_pg.py`) is outside the runner (T2I/G8's pattern).

    uv run --frozen pytest -q tests/p/annotations/test_mutants.py                     # subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/p/annotations/test_mutants.py   # all
"""
from __future__ import annotations

import ast
import pathlib
import sys

API_DIR = pathlib.Path(__file__).resolve().parents[3]
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from tests.contracts import mutants as shared  # noqa: E402
from tests.contracts.mutants import Mutant, Outcome, Result, Runner  # noqa: E402,F401

SUITE = "tests/p/annotations/test_annotations.py"
P = "pipelines/annotations/__init__.py"

IMP = "test_p1_import_keeps_provenance_and_links_the_original_evidence"
REJ = "test_p1_rejected_rows_are_reported_with_their_reason"
GATES = "test_p1_each_step_reads_its_own_gate_now"
ROLE = "test_p1_a_forged_or_unassigned_reviewer_is_denied"
ONCE = "test_p1_a_review_moves_a_label_once"
CORR = "test_p1_a_human_correction_round_trips_with_provenance"
ADJ = "test_p1_a_disagreement_is_adjudicated_by_an_independent_reviewer"
ADJREJ = "test_p1_an_adjudication_rejects_submitted_labels"
SEL = "test_p1_select_keeps_splits_and_only_authorized_samples"
EXP = "test_p1_a_training_export_is_train_only_and_byte_identical"
PREF = "test_p1_preference_pairs_map_or_are_reported"
DESC = "test_p1_holdout_descendants_never_reach_a_training_export"


def m(name, invariant, old, new, *cases, dies_by=(), occurrences=1):
    return Mutant(name=name, invariant=invariant, file=P, old=old, new=new, cases=cases,
                  dies_by=dies_by, occurrences=occurrences)


MUTANTS: tuple[Mutant, ...] = (
    # --- P1.a import with provenance (PIPELINE-LINEAGE)
    m("p1_synthetic_relabelled", "a model row stays synthetic",
      'METHODS = {"human": "imported", "model": "synthetic"}',
      'METHODS = {"human": "imported", "model": "imported"}', IMP),
    m("p1_provenance_dropped", "annotator/model/prompt/confidence/spans ride with the label",
      '"provenance": {k: row[k] for k in PROVENANCE if k in row}', '"provenance": {}', IMP),
    m("p1_declared_method_dropped", "the pipeline's own method word is kept",
      '"declared_method": row["method"],', '"declared_method": None,', IMP),
    m("p1_imported_as_accepted", "an imported label enters submitted",
      'state="submitted"), provider_org_id', 'state="accepted"), provider_org_id', IMP),
    m("p1_reimport_duplicates", "a re-import is the same label",
      '_uuid("import", dataset_ref, row)', "str(uuid.uuid4())", IMP),
    m("p1_missing_evidence_unnamed", "a row naming no sample is missing evidence",
      'if not isinstance(row, dict) or row.get("sample_id") not in samples:',
      "if not isinstance(row, dict):", REJ),
    m("p1_forged_ground_truth_accepted", "a ground-truth claim is refused",
      'if row.get("ground_truth", False) is not False:', "if False:", REJ),
    m("p1_extra_keys_accepted", "an unknown key is a bad mapping",
      "if (set(row) - ROW_KEYS or ", "if (", REJ),
    m("p1_unknown_method_accepted", "a method outside the pipeline vocabulary is refused",
      'row.get("method") not in METHODS or', "False or", REJ, dies_by=("KeyError",)),
    m("p1_label_optional", "a row carries its label", ' or "label" not in row', "", REJ,
      dies_by=("KeyError",)),
    m("p1_version_optional", "a row names its method version",
      'or not isinstance(row.get("method_version"), str) or not row["method_version"]',
      'or not isinstance(row.get("method_version", "v"), str)', REJ, dies_by=("KeyError",)),
    m("p1_confidence_unbounded", "confidence is 0..1", "0 <= confidence <= 1", "0 <= confidence",
      REJ),
    m("p1_bool_is_a_number", "a boolean is not a confidence",
      "and not isinstance(value, bool) and", "and", REJ),
    m("p1_empty_span", "a span has positive length", "0 <= s[0] < s[1]", "0 <= s[0] <= s[1]",
      REJ),
    m("p1_span_list_unchecked", "spans are a list", "            or not isinstance(spans, list)\n",
      "", REJ, dies_by=("TypeError",)),
    m("p1_non_finite_label_aborts", "an unpublishable label is one rejected row",
      "except lab.LabRejected:", "except KeyError:", REJ, dies_by=("LabRejected",)),
    m("p1_unreadable_labelled", "labels import through the access gate now",
      'if row["sample_id"] not in readable:', "if False:", GATES),
    m("p1_import_gate_is_training", "the import gate is provider_sharing",
      'purpose="provider_sharing"))\n    done = Imported()',
      'purpose="training"))\n    done = Imported()', GATES),
    # --- P1.b review (role checks, rubric versions, one move per label)
    m("p1_any_member_reviews", "a reviewer is a current developer or above",
      "if membership is None or not membership.permits(capability, now, provider):",
      "if membership is None:", ROLE),
    m("p1_developer_assigns", "only an administrator assigns",
      "user_id, ProviderCapability.manage_members)", "user_id, ProviderCapability.run_evaluation)",
      ROLE),
    m("p1_assignee_unchecked", "only a reviewer can be assigned",
      "    await _may_review(members, provider_org_id, reviewer_id)\n", "", ROLE),
    m("p1_review_role_rechecked_never", "a membership revoked after assignment cannot review",
      "    await _may_review(members, provider_org_id, user_id)\n"
      "    record = await store.resolve(annotation_ref",
      "    record = await store.resolve(annotation_ref", ROLE),
    m("p1_anyone_assigned", "a review needs its own assignment",
      'and e["reviewer_id"] == user_id and e["rubric_ref"] == rubric_ref',
      'and e["rubric_ref"] == rubric_ref', ROLE, ADJ),
    m("p1_rubric_version_ignored", "a review cites the assigned rubric version",
      'and e["reviewer_id"] == user_id and e["rubric_ref"] == rubric_ref',
      'and e["reviewer_id"] == user_id', ROLE),
    m("p1_foreign_label_reviewed", "a label is reviewed under its own dataset",
      "if annotation_ref not in _states(events):", "if False:", ONCE, dies_by=("Forbidden",)),
    m("p1_machine_skipped", "moves follow F3's annotation machine",
      'states.transition("annotation", _states(events)[ref], decision)', "None", CORR),
    m("p1_replay_redecides", "a replayed review is not a second move",
      'if not any(e["key"] == key for e in events):', "if True:", ONCE,
      dies_by=("StateConflict",)),
    m("p1_supersede_reuses_review_key", "a supersession is its own event",
      "f\"{'supersede' if decision == 'superseded' else 'review'}:{ref}\"", 'f"review:{ref}"',
      ADJ, dies_by=("IdempotencyConflict",)),
    m("p1_human_state_lost", "a reviewer's label enters accepted",
      'state[event["annotation_ref"]] = event.get("state", "submitted")',
      'state[event["annotation_ref"]] = "submitted"', CORR),
    m("p1_correction_accepts", "a correction rejects what it corrects",
      'if correction is not None and decision != "rejected":', "if False:", CORR),
    m("p1_correction_not_human", "a correction is a human label",
      'provider, annotation_id, dataset_ref, sample, method="human"',
      'provider, annotation_id, dataset_ref, sample, method="imported"', CORR),
    m("p1_human_not_ground_truth", "a reviewer's label is ground truth",
      '"ground_truth": method == "human"', '"ground_truth": False', CORR),
    m("p1_correction_provenance_dropped", "a correction cites what it corrects",
      'provenance={"corrects": annotation_ref}', "provenance={}", CORR),
    m("p1_correction_duplicates", "a replayed correction is the same label",
      'annotation_id=_uuid("correct", annotation_ref)', "annotation_id=str(uuid.uuid4())", CORR),
    # --- P1.b disagreement and adjudication
    m("p1_disagreement_blind", "differing live labels are a disagreement",
      "for v in values.values()}) > 1:", "for v in values.values()}) > 2:", ADJ),
    m("p1_dead_labels_disagree", "only live labels disagree", "if state in LIVE:", "if True:",
      ADJ),
    m("p1_adjudicator_reviewed", "an adjudicator reviewed none of the disputed labels",
      "if user_id in authors or any(", "if user_id in authors and any(", ADJ),
    m("p1_adjudicator_authored", "an adjudicator wrote none of the disputed labels",
      "if user_id in authors or any(", "if False or any(", ADJREJ),
    m("p1_adjudicator_unassigned", "an adjudicator is assigned to the sample",
      "    _assigned(events, sample_id, user_id, rubric_ref)\n", "", ADJ),
    m("p1_nothing_to_adjudicate", "an agreed sample cannot be adjudicated",
      "        if not disputed:\n", "        if False:\n", ADJ, dies_by=("TypeError",)),
    m("p1_accepted_rejected", "an accepted disputed label is superseded",
      '"superseded" if state[r] == "accepted" else "rejected"',
      '"rejected" if state[r] == "accepted" else "rejected"', ADJ,
      dies_by=("StateConflict",)),
    m("p1_submitted_superseded", "a submitted disputed label is rejected",
      '"superseded" if state[r] == "accepted" else "rejected"',
      '"superseded" if state[r] == "accepted" else "superseded"', ADJREJ,
      dies_by=("StateConflict",)),
    m("p1_adjudication_recomputed", "a replayed adjudication settles what it first settled",
      "    if prior is None:\n", "    if True:\n", ADJ, dies_by=("StateConflict",)),
    m("p1_adjudication_value_unpinned", "one adjudication per sample, one value",
      '"moves": moves,\n                      "value": value,', '"moves": moves,', ADJ,
      dies_by=("StateConflict",)),
    m("p1_adjudication_provenance_dropped", "an adjudication cites the disputed labels",
      'provenance={"adjudicates": disputed}', "provenance={}", ADJ),
    # --- P1.b select (DATA-SPLIT, DATA-RIGHTS)
    m("p1_select_ignores_rights", "a selection reads the access gate now",
      "keep = set(sample_ids) & readable", "keep = set(sample_ids)", SEL),
    m("p1_select_moves_splits", "a selected sample keeps its split",
      '"splits": {n: [i for i in getattr(manifest.splits, n) if i in keep]',
      '"splits": {n: [i for i in getattr(manifest.splits, {"validation": "holdout", '
      '"holdout": "validation"}.get(n, n)) if i in keep]', SEL),
    m("p1_select_without_lineage", "a selection names its parent",
      '"derivation": "derive", "parent_refs": [dataset_ref],',
      '"derivation": "import", "parent_refs": [],', SEL),
    # --- P1.c exports (DATA-SPLIT, PIPELINE-LINEAGE, DATA-RIGHTS)
    m("p1_validation_exported", "an export is train only",
      'if where[sample.sample_id] != "train":', 'if where[sample.sample_id] == "holdout":', EXP),
    m("p1_descendant_by_id_missed", "a moved holdout sample is a descendant",
      'holdout |= {s.sample_id, s.content_digest, "g:" + s.group_key}',
      'holdout |= {s.content_digest, "g:" + s.group_key}', DESC),
    m("p1_descendant_by_digest_missed", "a copy of holdout content is a descendant",
      'holdout |= {s.sample_id, s.content_digest, "g:" + s.group_key}',
      'holdout |= {s.sample_id, "g:" + s.group_key}', DESC),
    m("p1_descendant_by_group_missed", "a holdout group's new member is a descendant",
      'holdout |= {s.sample_id, s.content_digest, "g:" + s.group_key}',
      "holdout |= {s.sample_id, s.content_digest}", DESC),
    m("p1_ancestors_unwalked", "every ancestor's holdout binds",
      "queue.extend(manifest.parent_refs)", "pass", DESC),
    m("p1_ancestor_labels_ignored", "labels of an ancestor version carry to its samples",
      "for ref in versions:", "for ref in versions[:1]:", DESC),
    m("p1_export_rights_unchecked", "an export reads the training gate now",
      "elif sample.sample_id not in allowed:", "elif False:", EXP, GATES),
    m("p1_export_gate_is_access", "the export gate is training",
      'purpose="training"))\n    versions, holdout', 'purpose="provider_sharing"))\n'
      "    versions, holdout", GATES),
    m("p1_unaccepted_exported", "only accepted labels are examples",
      'if state == "accepted":', "if state in LIVE:", EXP),
    m("p1_disagreement_exported", "differing accepted labels are not an example",
      "elif len(values) > 1:", "elif False:", ADJ),
    m("p1_unlabelled_reported", "an unlabelled sample is not an example",
      "        if not refs:\n            continue\n", "", PREF, dies_by=("KeyError",)),
    m("p1_adapter_unchecked", "an unknown adapter is refused",
      "if adapter not in ADAPTERS:", "if False:", EXP),
    m("p1_export_id_unchecked", "an export id is a UUID, never a path",
      "if not isinstance(export_id, str) or not UUID_RE.fullmatch(export_id):", "if False:",
      EXP),
    m("p1_preference_shape_loose", "a preference label is exactly chosen and rejected",
      'set(value) == {"chosen", "rejected"}', '"chosen" in value', PREF, dies_by=("KeyError",)),
    m("p1_sft_schema_wrong", "the SFT line is {prompt, completion}",
      'return {"prompt": prompt, "completion": value}', 'return {"prompt": prompt, "output": value}',
      EXP),
    m("p1_export_rewritable", "an export's bytes are write-once",
      'await write_once(objects, f"{base}/examples.jsonl", data, "application/x-ndjson")',
      'await objects.put_if_absent(f"{base}/examples.jsonl", data, "application/x-ndjson")', EXP),
    m("p1_methods_dropped", "the lineage names each example's methods",
      '"methods": sorted({r.method for r in records.values()})', '"methods": []', EXP),
)


def case_names() -> set[str]:
    tree = ast.parse((API_DIR / SUITE).read_text())
    return {node.name for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")}


RUNNER = Runner(name="p1", targets=(SUITE,), require_every_case=True)


def run_mutant(mutant: Mutant) -> Result:
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run the P1 annotations mutation list"))
