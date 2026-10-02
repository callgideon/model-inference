#!/usr/bin/env python3
"""R32/R40/R83 for AP-10: one single-edit defect per decision the ap10 unit cases claim,
through the shared runner with `require_every_case`. The `_pg` files run on ap10's
PostgreSQL, outside the runner (B1's pattern).

    uv run --frozen pytest -q tests/ap10/test_mutants.py                     # subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/ap10/test_mutants.py   # all
"""
from __future__ import annotations

import ast
import pathlib
import shutil
import sys

API_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from tests.contracts import mutants as shared  # noqa: E402
from tests.contracts.mutants import Mutant, Result, Runner  # noqa: E402

SUITES = ("tests/ap10/test_evaluation_ports.py", "tests/ap10/test_row27.py",
          "tests/ap10/test_from_traces.py", "tests/ap10/test_from_traces_route.py",
          "tests/ap10/test_sop_benchmark.py", "tests/ap10/test_release.py")
P = "lab/evaluation/__init__.py"

STORED = "test_ap10_an_experiment_is_stored_once_as_its_two_run_records_and_a_resubmit_is_the_first"
CONFLICT = "test_ap10_another_launch_under_the_id_is_a_conflict_even_when_it_won_the_race"
HONEST = "test_ap10_a_launch_without_its_user_or_its_run_refs_is_an_honest_503"
REPORT = "test_ap10_the_listed_report_is_b2s_stored_body_with_its_digest"
FLAT = "test_ap10_the_subscription_listing_is_flattened_and_the_rest_is_d8s"
CATALOG = "test_ap10_the_catalog_listing_is_0066s_under_the_asking_provider_and_the_evaluator_is_d7s"
POOL = "test_ap10_the_ports_share_one_pool"
ROUTES = "test_ap10_authorized_empty_reads_are_200_empty_and_a_failed_read_is_503"
INFLIGHT = "test_ap10_row27_a_kill_with_both_attempts_in_flight_is_two_cases_each_charged_once"
GAP = "test_ap10_row27_after_the_kill_every_key_is_debited_once_and_the_gap_is_the_kill"
FT = "lab/datasets/from_traces.py"
HOLDOUT = "test_ap10_selection_then_materialisation_is_an_immutable_version_with_its_holdout"
REVOKED = "test_ap10_a_revocation_between_selection_and_materialisation_refuses"
NEWVERSION = "test_ap10_a_new_grant_version_or_an_expiry_after_selection_refuses"
SELECTION = "test_ap10_the_selection_needs_both_purposes_and_a_developer_of_the_provider"
KEYREUSE = "test_ap10_a_key_reused_with_another_body_conflicts_even_after_a_crash_before_start"
RESUME = "test_ap10_a_crash_mid_materialisation_resumes_to_the_same_refs_once"
CANCEL = "test_ap10_a_cancelled_operation_publishes_nothing"
C2 = "test_ap10_c2_refs_are_bound_to_the_selected_grant_version_for_training"
RT = "gateway/routes/lab_datasets.py"
RT_START = "test_ap10_route_a_session_starts_one_operation_and_a_replay_is_the_same"
RT_REFUSED = "test_ap10_route_refusals_are_r270_envelopes"
SOP = "lab/improve/sop.py"
SOP_REPORT = "test_ap10_sop_the_report_pins_identity_and_lists_every_failure_and_abstention"
SOP_BLOCKED = "test_ap10_sop_quality_is_blocked_on_p07_and_validity_is_counted_apart"
SOP_AGREE = "test_ap10_sop_agreement_is_computed_only_from_reviewed_gold_within_tolerance"
SOP_PERF = ("test_ap10_sop_performance_is_meas_only_for_a_declared_candidate_without_the_"
            "fake_surface")
REL = "lab/improve/release.py"
REL_TRAINER = "test_ap10_release_an_unsupported_trainer_is_an_explicit_refusal"
REL_IMPORT = "test_ap10_release_a_candidate_is_registered_only_through_ap04s_import"
REL_EVIDENCE = "test_ap10_release_evidence_pins_the_lineage_once_the_import_is_verified"
IDENTITY = '"import", candidate["repo"], candidate["commit"], candidate["files"])'
#: LAB-E2E evaluate's backend lives outside the package: its mutants run on `PROBE` (below).
PROBE_SUITE = "tests/ap10/test_e2e_probe.py"
BACKEND = "../../lab/tests/e2e/evaluate/backend.py"
J10 = "test_ap10_j10_a_catalog_answering_503_is_not_carried"


def m(name, invariant, old, new, *cases, file=P, dies_by=(), occurrences=1):
    return Mutant(name=name, invariant=invariant, file=file, old=old, new=new, cases=cases,
                  dies_by=dies_by, occurrences=occurrences)


MUTANTS: tuple[Mutant, ...] = (
    # --- 10a: experiments over 0043
    m("ap10_put_without_actor", "nothing is published without the session user",
      "            if actor is None:\n", "            if False:\n", HONEST),
    m("ap10_resubmit_republishes", "a stored id is answered from its row, never re-published",
      "        have = await self._stored(provider_org_id, wanted.experiment_id)\n"
      "        if have is None:\n",
      "        have = None\n        if have is None:\n", STORED),
    m("ap10_other_launch_accepted", "another launch under a stored id is a conflict",
      'if have is None or _launch(have["launch"]) != wanted:', "if have is None:", CONFLICT),
    m("ap10_race_loser_refused", "the loser of a first-launch race answers the winner's row",
      "                have = await self._stored(provider_org_id, wanted.experiment_id)\n"
      "        if have is None or",
      "                have = None\n        if have is None or", CONFLICT),
    m("ap10_clock_not_the_records", "an experiment's created_at is its run records' clock",
      '"created_at": base["created_at"],', '"created_at": e.get("created_at"),', STORED),
    m("ap10_candidate_is_the_baseline", "the candidate arm is the candidate record's serving",
      '"candidate_serving_ref": cand["serving_ref"],',
      '"candidate_serving_ref": base["serving_ref"],', STORED),
    m("ap10_digest_of_the_launch", "the stored digest is B2's digest of the protocol",
      "protocol_digest=_digest(protocol),",
      'protocol_digest=_digest(experiment["launch"]),', STORED),
    m("ap10_arms_swapped", "0043's baseline is the baseline record",
      "baseline_run_ref=refs[0],", "baseline_run_ref=refs[1],", STORED),
    m("ap10_unfrozen_guessed", "a row without run refs is a 503, never read",
      "                if refs is None:\n", "                if False:\n", HONEST,
      dies_by=("TypeError",)),
    m("ap10_report_digest_dropped", "the listed report carries its digest",
      '"report_digest": report["report_digest"]}}', "}}", REPORT),
    # --- 10a: the ledger listing and the catalog
    m("ap10_listing_nested", "the subscription listing is the route's flat row",
      '[{**row["subscription"], "decisions": row["decisions"]}', "[row", FLAT),
    m("ap10_catalog_guessed_empty", "the catalog is 0066's listing, never a guessed empty one",
      "        return await self.store.eval_catalog(provider_org_id=provider_org_id)",
      '        return {"datasets": [], "harnesses": [], "servings": [], "evaluators": []}',
      CATALOG, ROUTES),
    m("ap10_catalog_failure_as_empty", "a catalog the database does not answer is a 503",
      "        return await self.store.eval_catalog(provider_org_id=provider_org_id)",
      "        try:\n"
      "            return await self.store.eval_catalog(provider_org_id=provider_org_id)\n"
      "        except Exception:\n"
      '            return {"datasets": [], "harnesses": [], "servings": [], "evaluators": []}',
      CATALOG, ROUTES),
    m("ap10_evaluator_unscoped", "the evaluator is read under the asking provider",
      "evaluator_ref, provider_org_id=provider_org_id)",
      "evaluator_ref, provider_org_id=evaluator_ref)", CATALOG),
    m("ap10_reads_off_the_pool", "the experiments read the composition's pool",
      "Experiments(store, PgLabReads(connect))", "Experiments(store, PgLabReads(None))", POOL),
    m("ap10_ledger_off_the_pool", "the ledger is D8's on the composition's pool",
      "Subscriptions(PgCheckpointLedger(connect))", "Subscriptions(PgCheckpointLedger(None))",
      POOL),
    # --- 10b: row 27 - the endpoint's settlement per key is the authoritative debit
    m("ap10_row27_key_unscoped", "each paid call is keyed by its attempt (run, case, attempt)",
      "key = f\"{lease['idempotency_key']}:{bill.calls}\"", "key = f\"{bill.calls}\"",
      INFLIGHT, GAP, file="evaluation/runner/__init__.py"),
    m("ap10_row27_worker_serial", "the worker's in-flight bound is two concurrent attempts",
      "                                    concurrency=2)",
      "                                    concurrency=1)", INFLIGHT, GAP,
      file="worker/__main__.py"),
    # --- 10c: the trace -> dataset operation
    m("ft_training_not_required", "the selection needs the training purpose",
      "PURPOSES = (DataPurpose.provider_sharing, DataPurpose.training)",
      "PURPOSES = (DataPurpose.provider_sharing,)", SELECTION, file=FT),
    m("ft_sharing_not_required", "the selection needs provider_sharing to read content",
      "PURPOSES = (DataPurpose.provider_sharing, DataPurpose.training)",
      "PURPOSES = (DataPurpose.training,)", SELECTION, file=FT),
    m("ft_any_audience", "a selection is made in a web session only",
      'if actor.audience != "session" or provider is None', "if provider is None",
      SELECTION, file=FT),
    m("ft_no_holdout_accepted", "a trace dataset keeps a holdout",
      "self.train_bp + self.validation_bp >= 10_000", "self.train_bp + self.validation_bp > 10_000",
      SELECTION, file=FT),
    m("ft_selection_rewritten", "a recorded selection is never rewritten under its key",
      "    if stored is None:\n        await write_once(objects, _key(provider, sid)",
      "    if True:\n        await write_once(objects, _key(provider, sid)", KEYREUSE, file=FT),
    m("ft_hash_unchecked", "a key reused with another body is a 409 even before start",
      'elif json.loads(stored)["input_hash"] != digest:', "elif False:", KEYREUSE, file=FT),
    m("ft_version_unbound", "materialisation needs the selected grant VERSION",
      '(grant.grant_id, grant.version) != (sel["grant_id"], sel["grant_version"])',
      'grant.grant_id != sel["grant_id"]', NEWVERSION, file=FT),
    m("ft_materialise_ungated", "materialisation re-checks the grant's purposes and expiry",
      "    grant = await gate(p.access, user_id=user, provider_org_id=provider,\n"
      "                       grantor_org_id=grantor, model_id=body.model_id)\n",
      "    grant = await p.access.store.current_grant(grantor, provider)\n", NEWVERSION,
      file=FT),
    m("ft_refusal_unnamed", "a rights refusal names the grant",
      "if isinstance(refused, errors.Forbidden):", "if False:", REVOKED, NEWVERSION, file=FT),
    m("ft_split_overwrites_selection", "the split is a new version over the selection",
      "version=body.version + 1, created_at=at", "version=body.version, created_at=at",
      HOLDOUT, file=FT),
    m("ft_holdout_unrecorded", "the outcome records the manifest's holdout",
      '"holdout": list(manifest.splits.holdout),', '"holdout": [],', HOLDOUT, file=FT),
    m("ft_clock_not_the_selection", "a resumed materialisation republishes the same bytes",
      "selection_id=sid, dataset_id=dataset_id, version=body.version, created_at=at,",
      "selection_id=sid, dataset_id=dataset_id, version=body.version,"
      " created_at=iso_z(p.retention.clock()),", RESUME, file=FT),
    m("ft_transient_fails", "a 5xx refusal is retried, never a failed operation",
      "except (errors.ServerError, errors.RateLimitError):", "except (errors.RateLimitError,):",
      RESUME, file=FT),
    m("ft_crash_raises", "an unexpected failure leaves the lease to lapse",
      '            log.exception("trace dataset %s did not finish", operation_id)\n'
      '            done["retry"] += 1\n            continue\n',
      "            raise\n", RESUME, file=FT, dies_by=("RuntimeError",)),
    m("ft_cancel_ignored_first", "a cancel requested before the work is honoured",
      '"selecting")).state == "cancel_requested"', '"selecting")).state == "never"', CANCEL,
      file=FT),
    m("ft_cancel_ignored_split", "a cancel requested mid-work stops before the split",
      '"splitting")).state == "cancel_requested"', '"splitting")).state == "never"', CANCEL,
      file=FT),
    m("ft_c2_purpose", "C2 refs are issued for training",
      "purpose=DataPurpose.training)", "purpose=DataPurpose.provider_sharing)", C2, file=FT),
    m("j10_catalog_presence_is_carried", "a catalog answering 503 is not carried (j10 NOT RUN)",
      '    if got["catalog"]:\n', "    if False:\n", J10, file=BACKEND),
    # --- row 92: the R270 from-traces route (WR-AP10C-4)
    m("rt_session_not_scoped", "a web session acts in the path's workspace",
      'if actor.audience == "session" and actor.provider_org_id is None:', "if False:",
      RT_START, file=RT),
    m("rt_foreign_workspace", "an actor of another workspace never starts in its own",
      "elif actor.provider_org_id != provider:", "elif False:", RT_REFUSED, file=RT),
    m("rt_ops_unwired_crashes", "no composed ControlOps is a 503, never a 500",
      "if x.ops is None or actors is None:", "if actors is None:", RT_REFUSED, file=RT),
    m("rt_actors_unwired_crashes", "no session actors is a 503, never a 500",
      "if x.ops is None or actors is None:", "if x.ops is None:", RT_REFUSED, file=RT),
    m("rt_start_is_200", "a start is 202 + Location at the operation",
      'return control.accepted(doc, f"/lab/v1/operations/{doc.operation_id}")',
      "return control.ok(doc)", RT_START, file=RT),
    # --- 10d: the SOP benchmark report
    m("sop_no_media_sent", "an item without its video is an abstention, never sent",
      "        if item.video is None:\n", "        if False:\n", SOP_REPORT, file=SOP,
      dies_by=("AssertionError",)),
    m("sop_sampled", "every request is greedy (temperature 0)",
      '"temperature": 0, "seed": seed}', '"temperature": 1, "seed": seed}', SOP_REPORT,
      file=SOP),
    m("sop_seed_unpinned", "every request carries the run's seed",
      '"temperature": 0, "seed": seed}', '"temperature": 0, "seed": 0}', SOP_REPORT, file=SOP),
    m("sop_status_unclassed", "a refused request is a failure by its status",
      "        if response.status_code != 200:\n", "        if False:\n", SOP_REPORT,
      file=SOP),
    m("sop_identity_unchecked", "an answer from another model is a failure",
      'elif reply.get("model") != model:', "elif False:", SOP_REPORT, file=SOP),
    m("sop_untimed_answered", "an answer with no timed event is an abstention",
      '"outcome": "answered" if events else "abstained"', '"outcome": "answered"',
      SOP_REPORT, file=SOP),
    m("sop_find_mode_unparsed", "find mode's `From a to b.` is parsed",
      'r"From\s+(', 'r"Fromm\s+(', SOP_REPORT, file=SOP),
    m("sop_quality_without_definition", "no SOP definition is BLOCKED[P-07]",
      "if definition is None or gold is None:", "if gold is None:", SOP_BLOCKED, file=SOP,
      dies_by=("AssertionError",)),
    m("sop_quality_without_gold", "no gold set is BLOCKED[P-07]",
      "if definition is None or gold is None:", "if definition is None:", SOP_BLOCKED,
      file=SOP, dies_by=("AttributeError",)),
    m("sop_teacher_labels_graded", "teacher/model labels are not ground truth",
      'elif gold.provenance != "human_reviewed":', "elif False:", SOP_BLOCKED, file=SOP),
    m("sop_other_manifest_graded", "a gold set of another manifest is refused",
      "elif (gold.dataset_version, gold.manifest_sha256) != (dataset_version, manifest_sha256):",
      "elif False:", SOP_BLOCKED, file=SOP),
    m("sop_unknown_items_graded", "a gold set naming unknown items is refused",
      'elif set(gold.labels) - {r["id"] for r in rows}:', "elif False:", SOP_BLOCKED,
      file=SOP, dies_by=("KeyError",)),
    m("sop_validity_is_answers", "validity counts answers WITH timed events",
      '"with_timed_events": len(answered)}', '"with_timed_events": len(sent)}', SOP_BLOCKED,
      file=SOP),
    m("sop_end_unchecked", "a match needs the end within the tolerance too",
      " and abs(end - g.end_s) <= tolerance_s:", ":", SOP_AGREE, file=SOP),
    m("sop_prediction_reused", "matching is one-to-one",
      "matched, at = matched + 1, k + 1", "matched, at = matched + 1, at", SOP_AGREE,
      file=SOP),
    m("sop_hallucinations_dropped", "unmatched predictions are counted",
      "hallucinated += len(predicted) - hit", "hallucinated += 0", SOP_AGREE, file=SOP),
    m("sop_unanswered_unlisted", "a gold item without an answer is listed",
      'if by_id[item_id]["outcome"] != "answered":', "if False:", SOP_AGREE, file=SOP),
    m("sop_fake_probe_ignored", "the fake engine's surface labels the run fake",
      'fake = target == "fake" or await is_fake(client)', 'fake = target == "fake"',
      SOP_PERF, file=SOP),
    m("sop_declared_fake_measured", "a declared fake target is never meas.",
      'fake = target == "fake" or await is_fake(client)', "fake = await is_fake(client)",
      SOP_PERF, file=SOP),
    m("sop_p50_guessed", "p50 is refused below 6 samples",
      "MIN_P50, MIN_P95 = 6, 60", "MIN_P50, MIN_P95 = 1, 60", SOP_PERF, file=SOP),
    m("sop_p95_guessed", "p95 is refused below 60 samples",
      "MIN_P50, MIN_P95 = 6, 60", "MIN_P50, MIN_P95 = 6, 6", SOP_PERF, file=SOP),
    # --- 10e: reviewed labels -> external training -> AP-04 import -> release evidence
    m("rel_any_trainer", "only the supported trainer bundles",
      "    if trainer not in SUPPORTED:\n", "    if False:\n", REL_TRAINER, file=REL),
    m("rel_objective_unchecked", "the objective is the reviewed export's adapter",
      'if OBJECTIVE.get(json.loads(stored)["adapter"]) != config.get("objective"):',
      "if False:", REL_TRAINER, file=REL),
    m("rel_export_id_unchecked", "a label export is named by its id only",
      "    if not UUID_RE.fullmatch(label_export_id):\n", "    if False:\n", REL_TRAINER,
      file=REL),
    m("rel_not_eligible_registered", "only an approved checkpoint of this run registers",
      '        raise errors.StateConflict("not an approved checkpoint of this run")',
      "        return bundle, eligible", REL_IMPORT, file=REL),
    m("rel_foreign_workspace", "a candidate is registered in its run's workspace",
      "    if actor.provider_org_id != provider_org_id:\n", "    if False:\n", REL_IMPORT,
      file=REL),
    m("rel_descriptor_unbound", "the descriptor is the receipt's bytes",
      '"sha256:" + hashlib.sha256(data).hexdigest() != receipt[1]', "False", REL_IMPORT,
      file=REL, dies_by=("Unsupported",)),
    m("rel_descriptor_anywhere", "the descriptor is read under its run's prefix only",
      'if artifact_key.startswith(f"lab/{provider_org_id}/training/{external_run_id}/") else None',
      "if True else None", REL_IMPORT, file=REL),
    m("rel_files_unchecked", "the import declares exactly the checkpoint's files",
      "    if sorted(f.relative_path for f in body.files) != declared:\n", "    if False:\n",
      REL_IMPORT, file=REL),
    m("rel_many_imports", "one import per checkpoint",
      'await ledger.note(f"candidate:{checkpoint_id}", {',
      'await ledger.note(f"candidate:{checkpoint_id}:{key}", {', REL_IMPORT, REL_EVIDENCE,
      file=REL),
    m("rel_unverified_recorded", "evidence waits for AP-04's verification",
      '    if op.doc.state != "succeeded":\n', "    if False:\n", REL_EVIDENCE, file=REL),
    m("rel_commit_unchecked", "the verified artifact is the registered commit",
      IDENTITY, '"import", candidate["repo"], artifact.source_commit, candidate["files"])',
      REL_EVIDENCE, file=REL),
    m("rel_files_unbound", "the verified artifact is the registered files",
      IDENTITY, '"import", candidate["repo"], candidate["commit"], '
      "sorted(f.relative_path for f in artifact.files))", REL_EVIDENCE, file=REL),
    m("rel_evidence_rewritten", "the evidence record is written once",
      "    if stored is not None:\n        return json.loads(stored)\n",
      "    if False:\n        return json.loads(stored)\n", REL_EVIDENCE, file=REL),
    m("rel_self_qualified", "a new revision is qualified elsewhere, never here",
      '"qualification": {"state": "pending"', '"qualification": {"state": "qualified"',
      REL_EVIDENCE, file=REL),
    m("rel_methods_dropped", "the record carries the labels' provenance",
      'methods = Counter(m for x in export.get("lineage", ()) for m in x["methods"])',
      "methods = Counter()", REL_EVIDENCE, file=REL),
    m("ft_c2_empty_sample", "content C2 does not serve is Gone, never an empty sample",
      "        if got.content is None:\n", "        if False:\n", C2, file=FT,
      dies_by=("AttributeError",)),
)


def case_names() -> set[str]:
    names = set()
    for suite in (*SUITES, PROBE_SUITE):
        tree = ast.parse((API_DIR / suite).read_text())
        names |= {node.name for node in tree.body
                  if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")}
    return names


RUNNER = Runner(name="ap10", targets=SUITES, require_every_case=True)


def _with_backend(tmp: pathlib.Path) -> pathlib.Path:
    """The default copy under `infrx-api/`, plus the e2e backend (and the `stack` it
    imports) where the probe case finds it, `../lab/tests/e2e/`."""
    root = tmp / "infrx-api"
    root.mkdir()
    shared._copy(root, RUNNER)
    e2e = tmp / "lab/tests/e2e"
    (e2e / "evaluate").mkdir(parents=True)
    source = API_DIR.parent / "lab/tests/e2e"
    shutil.copy2(source / "stack.py", e2e / "stack.py")
    shutil.copy2(source / "evaluate/backend.py", e2e / "evaluate/backend.py")
    return root


PROBE = Runner(name="ap10-probe", targets=(PROBE_SUITE,), require_every_case=True,
               layout=_with_backend)


def run_mutant(mutant: Mutant) -> Result:
    return shared.run_mutant(mutant, PROBE if mutant.file == BACKEND else RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run the AP-10 mutation list"))
