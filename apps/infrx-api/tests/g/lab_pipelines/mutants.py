#!/usr/bin/env python3
"""R32/R40/R83 for WR-P4-1: one single-edit defect per invariant `/lab/v1/pipelines` claims.

Mutants live in the router and, for the two seam decisions its route cases prove over HTTP
(consumer-only 403, foreign provider 404), in `lab_auth` (lab-api, batch #4).

    uv run --frozen pytest -q tests/g/lab_pipelines/test_mutants.py
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/g/lab_pipelines/test_mutants.py
    uv run --frozen python -m tests.g.lab_pipelines.mutants --list
"""
from __future__ import annotations

from ...contracts import mutants as shared
from ...contracts.mutants import Mutant, Result
from ..lab_auth import mutants as auth

SUITE_FILES = ("tests/g/lab_pipelines/test_lab_pipelines.py",)
F = "gateway/routes/lab_pipelines.py"
FILES = (F, auth.F)
C = "test_lab_pipelines__"
MOUNT, SESSION = C + "nothing_is_mounted_without_the_switch", \
    C + "every_route_needs_the_session_before_anything_else"
ACCESS, ROLES = C + "a_consumer_only_user_is_denied_and_another_provider_is_not_found", \
    C + "a_viewer_reads_nothing_and_only_an_administrator_assigns"
LINEAGE, REJECTED = C + "lineage_runs_from_labels_to_an_eligible_checkpoint", \
    C + "a_rejected_checkpoint_is_never_evaluated"
IMPORT, EXPORT = C + "a_label_import_is_write_once_with_every_rejected_row", \
    C + "a_label_export_id_answers_the_export_already_made"
HUMAN, CONNECTOR = C + "review_corrections_and_adjudications_are_human_ground_truth", \
    C + "the_connector_is_never_the_callers"
GONE, UNWIRED = C + "an_expired_export_is_gone", \
    C + "an_unwired_port_is_unavailable_after_the_access_checks"
BODY = C + "a_body_is_json_bounded_and_valid_before_p1_and_p3"
DRY, APPROVE = C + "a_teacher_batch_is_a_dry_run_until_approved", \
    C + "only_an_administrator_approves_a_batch_within_its_budget"
READBACK, FORM = C + "an_ambiguous_or_stopped_batch_reads_back_as_recorded", \
    C + "a_teacher_form_names_only_what_the_provider_holds"


def _m(name, invariant, old, new, *cases, file=F) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=file, old=old, new=new, cases=cases)


MUTANTS: tuple[Mutant, ...] = (
    # --- mounting ----------------------------------------------------------------------------
    _m("mounted_without_the_switch", "LAB_PIPELINES off (no rt.lab_pipelines): no route",
       "    if x is None:\n        return None", "    if False:\n        return None", MOUNT),
    _m("runtime_pipelines_ignored", "register(app, rt) mounts over rt.lab_pipelines",
       'getattr(rt, "lab_pipelines", None)', "None", MOUNT),
    # --- identity, access, capability --------------------------------------------------------
    _m("body_before_identity", "a body is read only after the session and membership",
       "            who = await actor(request, capability)\n",
       "            early = model and await lab_body(request, rt, model, MAX_BODY_BYTES)\n"
       "            who = await actor(request, capability)\n", SESSION),
    _m("consumer_only_not_denied", "a consumer-only user is a 403 on every route",
       "    if not workspaces:\n", "    if False:\n", ACCESS, file=auth.F),
    _m("foreign_provider_is_forbidden", "another provider's id is a 404 on every route",
       'raise errors.NotFound("no such provider workspace")',
       'raise errors.Forbidden("no such provider workspace")', ACCESS, file=auth.F),
    _m("viewer_reads_labels", "label values are content: every route needs run_evaluation",
       "              capability: Cap = Cap.run_evaluation, listing: bool = False, query=()):",
       "              capability: Cap = Cap.read_aggregate_health, listing: bool = False,"
       " query=()):", ROLES),
    _m("developer_assigns", "assigning a reviewer needs manage_members",
       "capability=Cap.manage_members)", "capability=Cap.run_evaluation)", ROLES),
    # --- the connector (P-11) ----------------------------------------------------------------
    _m("connector_in_the_body", "a body naming a connector (or anything else) is refused",
       "class Body(lab.LabModel):\n    pass",
       'class Body(lab.LabModel):\n    model_config = {**lab.LabModel.model_config, '
       '"extra": "ignore"}', CONNECTOR),
    _m("connector_not_manual", "every run is the manual bundle until P-11",
       "limit=body.limit, now=await _now(x), connector=p3.MANUAL)",
       'limit=body.limit, now=await _now(x), connector="protocol-test")', CONNECTOR),
    _m("reservation_reported", "the manual bundle reserves nothing",
       '"reserved_usd": ZERO,', '"reserved_usd": row["limit"],', CONNECTOR),
    _m("unsettled_reported_settled", "a run is settled only once a cost was recorded",
       '"settled": "cost" in row,', '"settled": True,', CONNECTOR),
    # --- write-once ids ----------------------------------------------------------------------
    _m("import_conflict_unchecked", "another import under an import id is a 409",
       '    if receipt["request_sha256"] != asked:\n', "    if False:\n", IMPORT),
    _m("import_rerun_on_replay", "a replayed import id asks P1 nothing",
       "    if stored is None:\n        done = await p1.import_labels(",
       "    if True:\n        done = await p1.import_labels(", IMPORT),
    _m("receipt_leaks_the_digest", "a receipt is the port's record, nothing more",
       '    return {k: v for k, v in stored.items() if k != "request_sha256"}',
       "    return stored", IMPORT),
    _m("unreadable_row_dropped", "an unreadable line is a rejected row, never skipped",
       "                out.append(None)", "                continue", IMPORT),
    _m("blank_line_is_a_row", "blank lines are not rows",
       "        if line.strip():", "        if True:", IMPORT),
    _m("import_answered_200", "an import is a 201",
       "model=ImportBody, status=201)", "model=ImportBody)", IMPORT),
    _m("export_rerun_on_replay", "a replayed export id is the stored export",
       "    if stored is None:\n        return await p1.export(",
       "    if True:\n        return await p1.export(", EXPORT),
    _m("export_conflict_unchecked", "another export under an export id is a 409",
       '        raise errors.IdempotencyConflict("the export id names another export")\n',
       "        pass\n", EXPORT),
    # --- PIPELINE-LINEAGE: the records -------------------------------------------------------
    _m("export_listing_reads_examples", "only export records are listed",
       '            if key.endswith("/export.json")]', "            ]", LINEAGE),
    _m("run_train_count_is_dev", "a run shows its own train count",
       '"train": len(bundle["train"]),', '"train": len(bundle["dev"]),', LINEAGE),
    _m("run_holdout_unpinned", "a run shows the frozen holdout's pin",
       '"holdout": bundle["holdout"],', '"holdout": {},', LINEAGE),
    _m("run_export_unpinned", "a run shows the export it trained from",
       '"dataset_ref": bundle["dataset_ref"], "export": bundle["export"],',
       '"dataset_ref": bundle["dataset_ref"], "export": {},', LINEAGE),
    _m("one_op_for_every_move", "submit, finish and cancel are three operations",
       "lambda x, who, rid, op=op: move(x, who, rid, op)",
       "lambda x, who, rid: move(x, who, rid, op)", LINEAGE),
    _m("dataset_query_ignored", "labels and disagreements are the named dataset's",
       '*(request.query_params.get(name, "") for name in query)',
       '*("" for name in query)', LINEAGE, HUMAN),
    _m("checkpoint_run_is_the_provider", "a checkpoint names its external run",
       '"external_run_id": lab.REF_RE.fullmatch(row["external_run_ref"]).group(3),',
       '"external_run_id": lab.REF_RE.fullmatch(row["external_run_ref"]).group(2),', LINEAGE),
    _m("checkpoint_eligible_early", "a checkpoint is eligible only once approved",
       '"eligible": eligible is not None}', '"eligible": True}', LINEAGE),
    _m("checkpoint_evaluation_hidden", "a checkpoint shows its held-out evaluation",
       '"evaluation": None if evaluation is None else {',
       '"evaluation": None if True else {', LINEAGE),
    _m("rejection_reason_hidden", "a rejected checkpoint shows its reason",
       '"reason": row.get("reason"),\n', '"reason": None,\n', REJECTED),
    _m("ground_truth_not_the_records", "only a human review is ground truth, as recorded",
       '"ground_truth": record.ground_truth,', '"ground_truth": False,', HUMAN),
    _m("label_value_not_canonical", "a label value is its canonical JSON text",
       '"value": lab.canonical(record.label["value"]).decode(),',
       '"value": json.dumps(record.label["value"]),', HUMAN),
    _m("correction_not_parsed", "a correction is the reviewer's JSON value",
       "    correction = None if body.correction is None else _json(body.correction)",
       "    correction = body.correction", HUMAN),
    _m("adjudication_not_parsed", "an adjudication is the adjudicator's JSON value",
       "value=_json(body.value),", "value=body.value,", HUMAN),
    _m("disagreement_refs_hidden", "a disagreement lists its live labels",
       '[{"sample_id": s, "annotation_refs": refs}', '[{"sample_id": s, "annotation_refs": []}',
       HUMAN),
    # --- gone, the ports, the body ------------------------------------------------------------
    _m("gone_is_unavailable", "an expired export is 410 gone, not a retryable 503",
       "        except errors.Gone:\n", "        except errors.NotFound:\n", GONE),
    _m("gone_cacheable", "a refusal is never cached",
       'status_code=410, headers=lab_auth.NO_STORE)', "status_code=410)", GONE),
    _m("unwired_is_a_bug", "a port not merged yet is a typed 503, not an AttributeError",
       "        if value is None:                   # expected until its table merges: a 503\n",
       "        if False:\n", UNWIRED),
    _m("body_bounded_by_the_chat_cap", "an import body is bounded by the pipelines cap",
       "args.append(await lab_body(request, rt, model, MAX_BODY_BYTES))",
       "args.append(await lab_body(request, rt, model))", BODY),
    _m("usd_limit_unshaped", "a run's limit is an exact 8-decimal USD amount",
       "    limit: str = Field(pattern=USD)", "    limit: str", BODY),
    # --- P2 teacher batches (P4.b): the dry run --------------------------------------------
    _m("dry_run_sends", "a dry run reserves and sends nothing",
       "    return await _teacher_batch(x, provider, stored)\n\n\nasync def approve_teachers",
       "    await p2.run_batch(_teacher(stored, provider, who.user_id), wiring=wiring)\n"
       "    return await _teacher_batch(x, provider, stored)\n\n\nasync def approve_teachers",
       DRY),
    _m("batch_conflict_unchecked", "another batch under a batch id is a 409",
       '    if stored["request_sha256"] != asked:\n        raise errors.IdempotencyConflict('
       '"the batch id names another batch")',
       '    if False:\n        raise errors.IdempotencyConflict("the batch id names another batch")',
       DRY),
    _m("batch_rewritten_on_replay", "a replayed batch id is the stored batch",
       "    if await objects.get(key) is None:\n        await held(",
       "    if True:\n        await held(", DRY),
    _m("batch_record_leaks_the_digest", "a batch is the port's record, nothing more",
       "    return {**_receipt(stored), ", "    return {**stored, ", DRY),
    _m("batch_answered_200", "a dry run is a 201",
       "model=TeacherBody, status=201)", "model=TeacherBody)", DRY),
    _m("chunk_ceiling_hidden", "each chunk shows its reservation at the rate in force",
       '"ceiling_usd": rate and str(ProviderUsd(worst_case(rate, batch.ceilings, len(ids)))),',
       '"ceiling_usd": None,', DRY),
    _m("chunk_sent_is_its_size", "a chunk shows the samples that left, none before approval",
       '"sent": 0 if run is None else len(run.sent_ids),', '"sent": len(ids),', DRY),
    _m("holdout_count_hidden", "the plan shows the holdout it leaves out",
       '"holdout": len(planned.omitted),', '"holdout": 0,', DRY),
    _m("not_permitted_hidden", "the plan shows the samples that may not leave",
       '"not_permitted": len(planned.not_permitted),', '"not_permitted": 0,', DRY),
    _m("price_version_hidden", "the plan names the rate it priced at",
       '"price_version": planned.price_version,', '"price_version": None,', DRY),
    _m("within_budget_ignores_the_budget", "a ceiling over the batch budget is not within it",
       '"within_budget": ceiling is not None and ceiling <= ProviderUsd(stored["budget_usd"]),',
       '"within_budget": ceiling is not None,', APPROVE),
    _m("within_budget_unpriced", "an unpriced batch is not within its budget",
       '"within_budget": ceiling is not None and ceiling <= ProviderUsd(stored["budget_usd"]),',
       '"within_budget": ceiling is None or ceiling <= ProviderUsd(stored["budget_usd"]),', DRY),
    # --- the approval --------------------------------------------------------------------------
    _m("approve_role_before_the_batch", "R183: an unknown batch is a 404 whatever the role",
       "          capability=Cap.read_aggregate_health)",
       "          capability=Cap.manage_members)", APPROVE),
    _m("developer_approves", "only an administrator approves a live batch",
       "    require(who, Cap.manage_members)\n    wiring, stored",
       "    require(who, Cap.run_evaluation)\n    wiring, stored", APPROVE),
    _m("over_budget_approved", "a ceiling over the batch budget is a 409",
       '    if ceiling is None or ceiling > ProviderUsd(stored["budget_usd"]):',
       "    if ceiling is None:", APPROVE),
    _m("unpriced_approved", "an unpriced batch is a 409, never sent",
       '    if ceiling is None or ceiling > ProviderUsd(stored["budget_usd"]):',
       '    if ceiling is not None and ceiling > ProviderUsd(stored["budget_usd"]):', APPROVE),
    _m("live_switch_unchecked", "live submission off: nothing approved or reserved (503)",
       "    if wiring.settings.judge_mode != JUDGE_MODE_LIVE:\n",
       "    if False:\n", APPROVE),
    _m("approval_unrecorded", "the approval (who, when) is recorded",
       '    await objects.put_if_absent(_batch_key(provider, batch_id, "approval.json")',
       '    await objects.head(_batch_key(provider, batch_id, "approval.json")', APPROVE),
    _m("approved_by_the_requester", "the approver is the session's administrator",
       '"approved_by": who.user_id,', '"approved_by": stored["requested_by"],', APPROVE),
    _m("approval_hidden", "a batch shows its approval",
       '"approval": approval and json.loads(approval),', '"approval": None,', APPROVE),
    _m("approval_does_not_run", "an approval runs the batch",
       "    await p2.run_batch(batch, wiring=wiring)\n", "", APPROVE),
    # --- read back from P2's ledger -----------------------------------------------------------
    _m("chunk_state_not_the_ledgers", "a chunk's state is the ledger's (ambiguous included)",
       '"state": "unreserved" if run is None else run.state,', '"state": "unreserved",',
       APPROVE, READBACK),
    _m("chunk_hold_hidden", "a chunk shows the hold the ledger keeps",
       '"reserved_usd": run and str(run.reserved),', '"reserved_usd": None,', APPROVE, READBACK),
    _m("chunk_cost_hidden", "a chunk shows its settled cost",
       '"cost_usd": run and _usd(run.actual),', '"cost_usd": None,', READBACK),
    _m("failures_hidden", "a chunk's per-item failures read back from the ledger's log",
       "                                                for s, r in await wiring.ledger.failures("
       "run_id)]})", "                                                for s, r in []]})",
       READBACK),
    _m("batch_listing_reads_approvals", "only batch records are listed",
       '            for key in sorted(keys) if key.endswith("/batch.json")]',
       "            for key in sorted(keys)]", READBACK),
    # --- the form ------------------------------------------------------------------------------
    _m("foreign_payer_accepted", "a batch is paid by this provider's own payer",
       "        require_own_payer(provider, body.payer_ref)\n", "        pass\n", FORM),
    _m("foreign_payer_is_forbidden", "R183: a payer the provider does not hold is a 422",
       '        raise errors.InvalidRequest("the payer is not this provider\'s") from None',
       "        raise", FORM),
    _m("unknown_dataset_is_not_found", "R183: a dataset the provider does not hold is a 422",
       "        await held(wiring.store.resolve(", "        await (wiring.store.resolve(", FORM),
    _m("budget_unshaped", "a batch budget is an exact 8-decimal USD amount",
       "    budget_usd: str = Field(pattern=USD)", "    budget_usd: str", FORM),
    _m("chunk_unbounded", "a chunk is at most J1's scan bound, refused before anything is stored",
       "    chunk_size: int = Field(ge=1, le=MAX_CANDIDATES)", "    chunk_size: int = Field(ge=1)",
       FORM),
    _m("teachers_unwired_lists_nothing", "no teacher wiring (P-10): the listing is a 503",
       '    x.port("teachers")\n    keys', "    keys", FORM),
)


def case_names() -> set[str]:
    return auth.case_names_in(SUITE_FILES)


RUNNER = auth.runner("lab-pipelines", SUITE_FILES)


def run_mutant(mutant) -> Result:
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run WR-P4-1's pipelines mutation list"))
