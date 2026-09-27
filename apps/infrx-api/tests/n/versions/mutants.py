#!/usr/bin/env python3
"""R32/R40/R83 for N2: one single-edit defect per decision `test_versions.py` claims, through
the shared runner with `require_every_case`. The PostgreSQL rerun (`test_versions_pg.py`) is
outside the runner (T2I/G8's pattern).

    uv run --frozen pytest -q tests/n/versions/test_mutants.py                     # subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/n/versions/test_mutants.py   # all
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

SUITE = "tests/n/versions/test_versions.py"
P = "datasets/versions/__init__.py"

SAME = "test_n2_the_same_inputs_policy_and_seed_give_the_same_version_and_split_digest"
REL = "test_n2_related_clips_and_near_duplicates_never_cross_splits"
LEAK = "test_n2_a_leaked_split_is_rejected_with_the_leak_visible"
FROZEN = "test_n2_adding_samples_is_a_new_version_over_a_frozen_holdout"
DUPI = "test_n2_a_duplicate_import_cannot_change_the_holdout"
REVOKED = "test_n2_a_revoked_source_is_excluded_from_new_versions"
SCOPE = "test_n2_derivation_reads_only_the_callers_datasets_and_a_sane_policy"
REEXP = "test_n2_a_reexport_is_byte_identical_and_carries_schema_rights_and_omissions"
EXPRIGHTS = "test_n2_an_export_omits_revoked_and_untrained_sources"
RESUME = "test_n2_an_interrupted_export_resumes_and_refuses_changed_inputs"
CANCEL = "test_n2_an_export_is_cancelled_or_expires"
REDACT = "test_n2_redaction_removes_nested_paths_and_never_the_content_itself"


def m(name, invariant, old, new, *cases, dies_by=(), occurrences=1):
    return Mutant(name=name, invariant=invariant, file=P, old=old, new=new, cases=cases,
                  dies_by=dies_by, occurrences=occurrences)


MUTANTS: tuple[Mutant, ...] = (
    # --- N2.a policy and families (DATA-SPLIT)
    m("n2_policy_over_10000", "the proportions are at most 10000 bp",
      "self.train_bp + self.validation_bp > 10_000:", "self.train_bp + self.validation_bp > 10_001:",
      SCOPE),
    m("n2_negative_proportion", "a proportion is not negative",
      "if min(self.train_bp, self.validation_bp) < 0 or \\", "if False or \\", SCOPE),
    m("n2_seed_ignored", "the seed moves the split", 'f"{self.seed}\\n{key}"', 'f"0\\n{key}"',
      SAME),
    m("n2_no_validation_band", "the validation band is placed",
      '"validation" if bucket < self.train_bp + self.validation_bp else "holdout"',
      '"validation" if bucket < self.train_bp else "holdout"', SAME),
    m("n2_holdout_not_frozen", "new samples never enter a frozen holdout",
      "            bucket %= self.train_bp + self.validation_bp\n",
      "            bucket %= 10_000\n", FROZEN),
    m("n2_split_digest_blind", "the split digest is the assignment's",
      "hashlib.sha256(lab.canonical(splits)).hexdigest()",
      "hashlib.sha256(lab.canonical(sorted(splits))).hexdigest()", SAME),
    m("n2_near_duplicates_unjoined", "near-duplicates are one family",
      '*(("n:" + near,) if near else ())', "*()", REL, LEAK),
    m("n2_text_compared_exactly", "texts equal up to case and spacing are related",
      '" ".join(body["content"].casefold().split())', 'body["content"]', REL, LEAK),
    m("n2_clips_unrelated", "spans of one clip are related",
      'return "media:" + body["media_digest"]', "return None", REL),
    m("n2_groups_unjoined", "a group key is one family",
      'for label in ("g:" + sample.group_key, *(', "for label in (*(), *(", FROZEN),
    m("n2_leak_accepted", "a base with a family in two splits is refused",
      "        if len(fixed) > 1:\n", "        if False:\n", LEAK),
    m("n2_no_review_flags", "a cross-group family is flagged for review",
      "if len({s.group_key for s in members}) > 1:", "if False:", REL),
    # --- N2.b frozen holdout, dedup, access (DATA-SPLIT, DATA-RIGHTS)
    m("n2_frozen_split_ignored", "a base sample keeps its split",
      "target = fixed.pop() if fixed else policy.place(", "target = policy.place(",
      FROZEN, DUPI),
    m("n2_holdout_grows", "a new relative of a holdout sample is omitted",
      'if target == "holdout" and base is not None and i not in placed:', "if False:", FROZEN),
    m("n2_holdout_samples_dropped", "the frozen holdout itself is kept",
      'if target == "holdout" and base is not None and i not in placed:',
      'if target == "holdout" and base is not None:', FROZEN, DUPI),
    m("n2_fresh_holdout_dropped", "without a base the holdout is placed",
      'if target == "holdout" and base is not None and i not in placed:',
      'if target == "holdout" and i not in placed:', SAME),
    m("n2_duplicates_added", "a repeated content digest adds nothing",
      "elif sample.content_digest in digests:", "elif False:", DUPI),
    m("n2_unreadable_base_unanchored", "an unreadable base sample still relates new samples",
      "                    anchors.append(sample)\n", "                    pass\n", REVOKED),
    m("n2_unreadable_base_split_forgotten", "an unreadable base sample keeps its split",
      "            if ref == base:\n                placed[",
      "            if ref == base and sample in kept:\n                placed[", REVOKED),
    m("n2_revoked_read", "derivation reads through the access gate now",
      "if sample.sample_id not in readable:", "if False:", REVOKED),
    m("n2_access_gate_is_training", "derivation is the access gate (provider_sharing)",
      'purpose="provider_sharing"))', 'purpose="training"))', REVOKED),
    m("n2_closed_policy_over_a_base", "new samples over a frozen holdout need somewhere to go",
      "if base and not policy.train_bp + policy.validation_bp:", "if False:", SCOPE),
    m("n2_parents_dropped", "a derived version names all its parents",
      '"parent_refs": parents,', '"parent_refs": parents[:1],', FROZEN),
    # --- N2.c exports (DATA-RIGHTS, DATA-IMMUTABLE)
    m("n2_holdout_exported", "an export never ships the holdout",
      '"holdout" if where[sample.sample_id] == "holdout" else', '"holdout" if False else',
      REEXP, EXPRIGHTS),
    m("n2_export_rights_unchecked", "an export reads the grant now",
      'None if sample.sample_id in allowed else "grant_not_current"', "None", EXPRIGHTS, RESUME),
    m("n2_export_gate_is_access", "the export gate is training",
      'allowed = set(await store.accessible_samples(dataset_ref, provider_org_id=provider_org_id,\n'
      '                                                 purpose="training"))',
      'allowed = set(await store.accessible_samples(dataset_ref, provider_org_id=provider_org_id,\n'
      '                                                 purpose="provider_sharing"))', EXPRIGHTS),
    m("n2_omission_unreported", "every omitted sample is reported with its reason",
      'omitted.append({"sample_id": sample.sample_id, "reason": reason})', "None",
      REEXP, EXPRIGHTS),
    m("n2_redaction_ignored", "a redacted key leaves every item",
      'original = _drop(original, key.split("."))', "original = original", REEXP, REDACT),
    m("n2_redaction_top_level_only", "a redacted key is a dotted path, removed at any depth",
      '_drop(original, key.split("."))', "_drop(original, [key])", REDACT),
    m("n2_structured_content_unredacted", "structured content loses a redacted path too",
      'return {**body, "original": original, "content": content}',
      'return {**body, "original": original}', REDACT),
    m("n2_content_redaction_accepted", "a redaction never removes the content itself",
      "    if content is _MISSING:\n", "    if False:\n", REDACT, dies_by=("TypeError",)),
    m("n2_parts_unbounded", "a part holds at most part_items",
      "for n, start in enumerate(range(0, len(items), part_items)):",
      "for n, start in enumerate(range(0, len(items), part_items + 1)):", REEXP),
    m("n2_manifest_hash_missing", "the record names the manifest hash",
      '"manifest_sha256": dataset_ref.split("@sha256:")[1]', '"manifest_sha256": dataset_ref',
      REEXP),
    m("n2_licence_dropped", "each source's licence is carried",
      '"license": spec.get("license")', '"license": None', REEXP),
    m("n2_changed_part_accepted", "a resumed export refuses parts its inputs no longer make",
      'await write_once(objects, key, data, "application/x-ndjson")',
      'await objects.put_if_absent(key, data, "application/x-ndjson")', RESUME),
    m("n2_finished_not_replayed", "a finished export replays",
      "    if done is not None:\n        record = json.loads(done)",
      "    if False:\n        record = json.loads(done)", RESUME),
    m("n2_export_of_another_dataset", "an export id is one dataset's",
      'if record["dataset_ref"] != dataset_ref:', "if False:", RESUME),
    m("n2_read_ignores_rights", "a read re-reads the grant",
      'if json.loads(line)["sample_id"] in allowed)', "if True)", EXPRIGHTS),
    m("n2_read_gate_is_access", "a read is the export gate (training)",
      'allowed = set(await store.accessible_samples(record["dataset_ref"],\n'
      '                                                 provider_org_id=provider_org_id,\n'
      '                                                 purpose="training"))',
      'allowed = set(await store.accessible_samples(record["dataset_ref"],\n'
      '                                                 provider_org_id=provider_org_id,\n'
      '                                                 purpose="provider_sharing"))', EXPRIGHTS),
    m("n2_cancel_ignored", "a cancelled export is gone",
      "    if await objects.head(f\"{base}/cancelled\") is not None:\n",
      "    if False:\n", CANCEL),
    m("n2_expiry_ignored", "an expired export is gone",
      'if now >= datetime.fromisoformat(record["expires_at"]):', "if False:", CANCEL),
    m("n2_expiry_off_by_one", "the TTL's end is expired",
      'if now >= datetime.fromisoformat(record["expires_at"]):',
      'if now > datetime.fromisoformat(record["expires_at"]):', CANCEL),
    m("n2_ttl_unbounded", "a TTL is 1 s..7 days", "if not 1 <= ttl_s <= MAX_EXPORT_TTL_S:",
      "if False:", CANCEL),
    m("n2_export_id_unchecked", "an export id is a UUID, never a path",
      "if not isinstance(export_id, str) or not UUID_RE.fullmatch(export_id):", "if False:",
      CANCEL),
    m("n2_part_index_unchecked", "a missing part is not_found, never a crash",
      'if not 0 <= part < len(record["parts"]):', "if False:", CANCEL, dies_by=("IndexError",)),
)


def case_names() -> set[str]:
    tree = ast.parse((API_DIR / SUITE).read_text())
    return {node.name for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")}


RUNNER = Runner(name="n2", targets=(SUITE,), require_every_case=True)


def run_mutant(mutant: Mutant) -> Result:
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run the N2 versions mutation list"))
