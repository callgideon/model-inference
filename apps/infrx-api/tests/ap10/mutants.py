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
import sys

API_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from tests.contracts import mutants as shared  # noqa: E402
from tests.contracts.mutants import Mutant, Result, Runner  # noqa: E402

SUITES = ("tests/ap10/test_evaluation_ports.py",)
P = "lab/evaluation/__init__.py"

STORED = "test_ap10_an_experiment_is_stored_once_as_its_two_run_records_and_a_resubmit_is_the_first"
CONFLICT = "test_ap10_another_launch_under_the_id_is_a_conflict_even_when_it_won_the_race"
HONEST = "test_ap10_a_launch_without_its_user_or_its_run_refs_is_an_honest_503"
REPORT = "test_ap10_the_listed_report_is_b2s_stored_body_with_its_digest"
FLAT = "test_ap10_the_subscription_listing_is_flattened_and_the_rest_is_d8s"
CATALOG = "test_ap10_the_catalog_listing_is_503_naming_its_request_and_the_evaluator_is_d7s"
POOL = "test_ap10_the_ports_share_one_pool"
ROUTES = "test_ap10_authorized_empty_reads_are_200_empty_and_a_failed_read_is_503"


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
)


def case_names() -> set[str]:
    names = set()
    for suite in SUITES:
        tree = ast.parse((API_DIR / suite).read_text())
        names |= {node.name for node in tree.body
                  if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")}
    return names


RUNNER = Runner(name="ap10", targets=SUITES, require_every_case=True)


def run_mutant(mutant: Mutant) -> Result:
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run the AP-10 mutation list"))
