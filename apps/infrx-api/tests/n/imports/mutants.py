#!/usr/bin/env python3
"""R32/R40/R83 for N1: one single-edit defect per decision `test_import.py` claims, through
the shared runner with `require_every_case` (every named case must notice, nothing else may
fail). The PostgreSQL rerun (`test_import_pg.py`) is outside the runner (T2I/G8's pattern).

    uv run --frozen pytest -q tests/n/imports/test_mutants.py                     # subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/n/imports/test_mutants.py   # all
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

SUITE = "tests/n/imports/test_import.py"
P = "datasets/imports/__init__.py"

BENCH = "test_n1_an_owned_benchmark_is_published_through_d7"
SAM = "test_n1_a_sam_style_video_annotation_export_keeps_spans_and_method"
MAL = "test_n1_malformed_rows_are_quarantined_and_never_silently_omitted"
UNITS = "test_n1_timestamp_units_are_checked"
BIG = "test_n1_oversized_rows_and_uploads_are_refused"
CLIP = "test_n1_an_oversized_clip_is_refused_before_it_is_read"
PATHS = "test_n1_traversal_ssrf_and_foreign_objects_are_refused"
RIGHTS = "test_n1_a_spec_for_another_provider_or_grant_is_refused"
CRASH = "test_n1_a_crash_mid_staging_publishes_nothing_and_resumes_to_the_same_dataset"
SRC = "test_n1_a_crash_between_source_and_manifest_resumes_once"
REPLAY = "test_n1_a_replayed_upload_is_one_dataset_and_changed_bytes_conflict"
DUP = "test_n1_duplicate_rows_are_dedup_candidates"
GSPLIT = "test_n1_a_group_split_across_splits_is_quarantined"
PREVIEW = "test_n1_the_schema_preview_shows_fields_mapping_and_row_errors"
STRICT = "test_n1_the_spec_is_strict"
ACTING = "test_wrn2_only_a_current_developer_member_acts_for_the_provider"
JOBS = "test_n4_an_import_job_is_enqueued_once_and_worked_by_the_pool_under_its_lease"


def m(name, invariant, old, new, *cases, dies_by=(), occurrences=1):
    return Mutant(name=name, invariant=invariant, file=P, old=old, new=new, cases=cases,
                  dies_by=dies_by, occurrences=occurrences)


MUTANTS: tuple[Mutant, ...] = (
    # --- the spec (DATA-RIGHTS, P-09)
    m("n1_spec_provider_unchecked", "a provider imports only for itself",
      "if spec.provider_org_id != provider_org_id:", "if False:", RIGHTS, PREVIEW),
    m("n1_grant_provider_unchecked", "the grant names the spec's provider",
      "if lab.REF_RE.fullmatch(spec.grant_ref).group(2) != spec.provider_org_id:", "if False:",
      RIGHTS),
    m("n1_customer_content_importable", "only provider-owned data until P-09",
      'ownership: Literal["provider_owned"]', "ownership: str", RIGHTS),
    m("n1_spec_not_strict", "an unknown spec key is refused",
      'model_config = ConfigDict(frozen=True, extra="forbid", strict=True)',
      'model_config = ConfigDict(frozen=True, extra="ignore", strict=True)', STRICT),
    m("n1_video_mapping_unchecked", "finite video, and only it, maps a span",
      'if [v is not None for v in video] != [self.modality == "finite_video"] * 4:', "if False:",
      STRICT),
    # --- one row (DATA-IMPORT)
    m("n1_row_bound_trusted_to_the_stream", "a row over MAX_ROW_BYTES is refused",
      "if raw is None or len(raw) > MAX_ROW_BYTES:", "if raw is None:", BIG, PREVIEW),
    m("n1_nan_accepted", "NaN/Infinity are not JSON numbers",
      "row = json.loads(raw, parse_constant=_no_constant)", "row = json.loads(raw)", MAL),
    m("n1_missing_field_unchecked", "a mapped field that is absent is named",
      "    if missing:\n", "    if False:\n", MAL),
    m("n1_blank_text_accepted", "text content is non-blank",
      'isinstance(content, str) and content.strip() if', 'isinstance(content, str) if', MAL),
    m("n1_structured_content_unchecked", "structured content is an object or array",
      "else isinstance(content, (dict, list))):", "else True):", MAL),
    m("n1_group_type_unchecked", "a group is a string or an integer",
      'if isinstance(group, bool) or not isinstance(group, (str, int)) or group == "":',
      "if False:", MAL),
    m("n1_split_unchecked", "a declared split is one of three",
      "if got[\"split\"] not in SPLITS:", "if False:", MAL),
    m("n1_seconds_read_as_ms", "a span in seconds is converted",
      '(1000 if unit == "s" else 1)', "1", SAM),
    m("n1_fraction_rounded", "a sub-millisecond span is refused, not rounded",
      "if ms.is_finite() and ms == ms.to_integral_value() else None",
      "if ms.is_finite() else None", UNITS),
    m("n1_infinite_timestamp", "an out-of-range timestamp is quarantined, never a crash",
      "if ms.is_finite() and ms ==", "if ms ==", UNITS, dies_by=("OverflowError",)),
    m("n1_timestamp_through_float", "a millisecond count is exact (no float round trip)",
      "ms = Decimal(str(value))", "ms = Decimal(str(float(value)))", UNITS),
    m("n1_boolean_timestamp", "a boolean is not a timestamp",
      "if isinstance(value, bool) or not isinstance(value, (int, float)):",
      "if not isinstance(value, (int, float)):", UNITS,
      dies_by=("InvalidOperation",)),          # Decimal("True"): exact, so no float to hide it
    m("n1_backwards_span", "a span ends after it starts",
      "0 <= start < end <= start", "0 <= start <= end <= start", UNITS),
    m("n1_negative_start", "a span starts at 0 or later",
      "not 0 <= start < end", "not -10**9 <= start < end", UNITS),
    m("n1_cap_exclusive", "the 82 s cap is inclusive",
      "end <= start + lab.MAX_VIDEO_MS", "end < start + lab.MAX_VIDEO_MS", SAM),
    m("n1_cap_moved", "past 82 s is refused",
      "end <= start + lab.MAX_VIDEO_MS", "end <= start + lab.MAX_VIDEO_MS + 1", UNITS),
    m("n1_media_path_unchecked", "a media path is a relative bundle key",
      "or PATH_RE.fullmatch(media)):", "or media):", PATHS),
    m("n1_dot_segments", "no `.`/`..` segment", "[A-Za-z0-9_-][A-Za-z0-9._-]{0,127}",
      "[A-Za-z0-9._-][A-Za-z0-9._-]{0,127}", PATHS, occurrences=2),
    # --- streaming bounds
    m("n1_line_buffered_past_the_bound", "a line never buffers past MAX_ROW_BYTES",
      "            if len(buf) > MAX_ROW_BYTES:\n", "            if False:\n", BIG),
    m("n1_over_sticks", "the row after an oversized one is read",
      "buf, over, start = bytearray(), False, end + 1",
      "buf, over, start = bytearray(), over, end + 1", BIG),
    m("n1_last_line_dropped", "the last line needs no newline", "if buf or over:", "if over:",
      BIG),
    m("n1_upload_unbounded", "an upload over MAX_REQUEST_BYTES is refused",
      "if total > limit:", "if False:", BIG),
    m("n1_source_digest_not_the_upload", "the source is the uploaded bytes' digest",
      "        whole.update(piece)\n", "        pass\n", BENCH),
    # --- media (M ports)
    m("n1_url_skips_the_fetcher", "an http(s) source goes through M's SSRF-hardened fetcher",
      'if source.startswith(("http://", "https://")):', "if False:", PATHS),
    m("n1_fetch_refusal_escapes", "a refused fetch quarantines its row",
      "except errors.DomainError as refused:", "except ZeroDivisionError as refused:", PATHS),
    m("n1_clip_size_unchecked", "a clip over MAX_MEDIA_BYTES is refused unread",
      "if size > self.limits.max_media_bytes:", "if False:", CLIP),
    m("n1_clip_type_unchecked", "a bundle object is a video type",
      "if mime not in ALLOWED_MIME:", "if False:", PATHS),
    m("n1_bundle_not_provider_scoped", "a media key is inside this provider's import",
      'return f"lab/{provider}/imports/{import_id}/bundle/{path}"', "return path", PATHS),
    m("n1_clip_not_copied", "the clip is kept content-addressed",
      "await self.objects.put_if_absent(media_key(spec.provider_org_id, digest), data, mime)",
      "None", SAM),
    # --- staging, quarantine, resume
    m("n1_blank_line_is_a_row", "a blank line is not a row",
      "if raw is not None and not raw.strip():", "if False:", BENCH, CRASH, SRC, REPLAY),
    m("n1_content_not_stored", "each sample's content is stored under its digest",
      "await self._once(sample_key(spec.provider_org_id, digest), data)", "None", BENCH),
    m("n1_original_dropped", "the original row stays with the sample",
      '"original": row,', '"original": {},', BENCH),
    m("n1_method_dropped", "the annotation method/version stays with the sample",
      '"annotation": spec.annotation.model_dump(exclude_none=True)}', '"annotation": {}}',
      BENCH, SAM),
    m("n1_duplicates_kept", "a repeated row is a dedup candidate",
      "if digest in seen:", "if False:", DUP),
    m("n1_resume_forgets_duplicates", "a resumed chunk's rows still dedup",
      'seen.setdefault(sample["content_digest"], sample["line"])', "None", DUP),
    m("n1_group_split_unchecked", "one group, one split", "if groups.get(group, split) != split:",
      "if False:", GSPLIT),
    m("n1_resume_forgets_groups", "a resumed chunk's groups still bind",
      'groups.setdefault(sample["group_key"], sample["split"])', "None", GSPLIT),
    m("n1_default_group_shared", "an unmapped group is the row's own",
      'group = checked.get("group", digest)', 'group = checked.get("group", "all")', GSPLIT),
    m("n1_default_split_holdout", "an unmapped split is train",
      'split = checked.get("split", "train")', 'split = checked.get("split", "holdout")', SAM),
    m("n1_duration_is_the_end", "a sample's duration is its span",
      'checked["span_ms"][1] - checked["span_ms"][0]', 'checked["span_ms"][1]', SAM),
    m("n1_changed_chunk_trusted", "a staged chunk is reused only for the same lines",
      'if chunk["lines"] != lines.hexdigest():', "if False:", REPLAY),
    m("n1_chunk_restaged", "a staged chunk is reused, never staged again",
      "if stored is None:", "if True:", CRASH),
    m("n1_one_chunk", "staging is chunked, so a resume skips what is durable",
      "if block and (number - 1) // self.chunk_rows != block[0][0] // self.chunk_rows:",
      "if False:", CRASH),
    m("n1_write_once_unchecked", "other bytes at a staged key are a conflict",
      "await objects.head(key) != digest_of(data):", "False:", REPLAY),
    # --- publication
    m("n1_rejects_published_silently", "rejects block publication unless accepted",
      "if not accepted or (rejected and not accept_rejects):", "if not accepted:", MAL),
    m("n1_empty_import_published", "an import that accepted nothing is refused",
      "if not accepted or (rejected and not accept_rejects):",
      "if rejected and not accept_rejects:", CLIP),
    m("n1_declared_split_ignored", "the manifest's splits are the rows'",
      'if s["split"] == name]', 'if name == "train"]', BENCH),
    # --- preview
    m("n1_preview_bool_is_number", "a boolean previews as boolean",
      'bool: "boolean"', 'bool: "number"', PREVIEW),
    m("n1_preview_uncapped", "the preview shows at most `rows` rows",
      "if len(shown) == rows:", "if False:", PREVIEW),
    m("n1_preview_blank_row", "a blank line is not a preview row",
      "        if not raw.strip():\n            continue\n        try:",
      "        try:", PREVIEW),
    # --- WR-N4-3 (composition-5): the durable import-job queue's halves
    m("n4_rows_not_stored", "the upload's rows are stored before the job is enqueued",
      "    await write_once(objects, rows_key(provider_org_id, spec.import_id), body[\"body\"].encode(),\n"
      "                     \"application/x-ndjson\")\n", "", JOBS),
    m("n4_accept_rejects_forced", "the job keeps the caller's accept_rejects",
      '"accept_rejects": bool(body.get("accept_rejects")),', '"accept_rejects": True,', JOBS),
    m("n4_other_actor", "the import runs as the job's actor",
      'provider_org_id=provider, actor=task["actor"],', 'provider_org_id=provider, actor="worker",',
      JOBS),
    m("n4_no_heartbeat", "the lease is heartbeaten while the import runs",
      "                await jobs.heartbeat(job_id, worker_id=worker_id)\n",
      "                pass\n", JOBS),
    m("n4_rejected_succeeds", "rejected rows fail the job, keeping their report",
      '            result, error = vars(rejected.report), "rejected"',
      "            result, error = vars(rejected.report), None", JOBS),
    m("n4_missing_upload_retried", "a missing upload fails the job by name",
      "            if rows is None:\n", "            if False:\n", JOBS),
    m("n4_infra_failure_finishes", "an infrastructure failure finishes nothing (the lease lapses)",
      '            done["retry"] += 1\n            continue\n', '            error = "died"\n', JOBS),
    m("n4_claims_every_job", "a pass claims at most `limit` jobs",
      "    for job in await jobs.claim(limit=limit, worker_id=worker_id, redelivery_s=lease_s):",
      "    for job in await jobs.claim(limit=99, worker_id=worker_id, redelivery_s=lease_s):", JOBS),
    # --- WR-N-2: the acting provider (the L2 membership half)
    Mutant(name="wrn2_role_unchecked", invariant="a viewer never acts on dataset content",
           file="datasets/__init__.py", old="if ACTS not in ROLE_CAPABILITIES[membership.role]:",
           new="if False:", cases=(ACTING,)),
    Mutant(name="wrn2_viewer_capability", invariant="dataset work needs developer or above",
           file="datasets/__init__.py", old="ACTS = ProviderCapability.manage_dev_deployment",
           new="ACTS = ProviderCapability.read_aggregate_health", cases=(ACTING,)),
    Mutant(name="wrn2_any_provider", invariant="a membership acts only for its own provider",
           file="datasets/__init__.py",
           old="if membership.provider_org_id == provider_org_id:", new="if True:",
           cases=(ACTING,)),
)


def case_names() -> set[str]:
    tree = ast.parse((API_DIR / SUITE).read_text())
    return {node.name for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")}


RUNNER = Runner(name="n1", targets=(SUITE,), require_every_case=True)


def run_mutant(mutant: Mutant) -> Result:
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run the N1 import mutation list"))
