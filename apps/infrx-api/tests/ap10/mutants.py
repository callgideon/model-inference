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
          "tests/ap10/test_from_traces.py")
P = "lab/evaluation/__init__.py"

STORED = "test_ap10_an_experiment_is_stored_once_as_its_two_run_records_and_a_resubmit_is_the_first"
CONFLICT = "test_ap10_another_launch_under_the_id_is_a_conflict_even_when_it_won_the_race"
HONEST = "test_ap10_a_launch_without_its_user_or_its_run_refs_is_an_honest_503"
REPORT = "test_ap10_the_listed_report_is_b2s_stored_body_with_its_digest"
FLAT = "test_ap10_the_subscription_listing_is_flattened_and_the_rest_is_d8s"
CATALOG = "test_ap10_the_catalog_listing_is_503_naming_its_request_and_the_evaluator_is_d7s"
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
    m("ap10_catalog_guessed_empty", "an unlisted catalog is a 503, never an empty catalog",
      "        raise errors.DependencyUnavailable(NO_CATALOG)",
      '        return {"datasets": [], "harnesses": [], "servings": [], "evaluators": []}',
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
