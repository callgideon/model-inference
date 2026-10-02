"""AP-10 10a: the `/lab/v1/evaluations` ports `lab/compose.py` left unwired (`experiments`,
`ledger`, `catalog`), over the SQL that already exists (0043, 0034, 0042), so an authorized
read with nothing in it is 200 empty and a database failure is the route's 503. It replaces
the route suite's fakes (`tests/g/lab_evaluations`: `Experiments`, `Ledger`, `Catalog`) that
the E6L/LAB-E2E backends composed in their stead; it adds no table and no RPC.

* `Experiments` - WR-B4-2 over 0043's `lab_put_experiment` / `lab_experiments`. 0043 keys an
  experiment by its two published run records, while the route fixes the launch's clock
  first (`put` before `freeze`, so a resubmit rebuilds the same records). `put` therefore
  publishes both arms' run records (the payloads `freeze` will publish again - a replay)
  and stores the pair; the row it answers is rebuilt from those records, so a resubmit
  under the same id is the first launch with its first `created_at`, and another launch
  under it is `IdempotencyConflict` - also when a concurrent first launch won the insert.
* `Subscriptions` - WR-B3-1: D8's `PgCheckpointLedger`, its 0043 listing flattened to the
  route's row (`{...subscription, decisions}`).
* `Catalog` - WR-LAB2-2: the evaluator spec by ref is 0034's `lab_evaluator`; the launch
  catalog is 0066's `infrx.lab_eval_catalog` (SR-AP10-1, carried since merge #100).
"""
from __future__ import annotations

import json
from typing import Any

from ...contracts import errors
from ...evaluation.reports import _digest
from ...gateway.routes import lab_evaluations as le

#: SR-AP10-1 (b): 0043's listing names a run only by its status, so no ref before `freeze`.
NO_REFS = ("an experiment stored before its runs were created needs its run refs in "
           "infrx.lab_experiments' listing (SR-AP10-1)")
#: WR-AP10-2 (wiring request): the launch's session user, for the records it publishes.
NO_ACTOR = "an experiment is stored as its launching user (WR-AP10-2: put(..., actor=))"


def _launch(launch: dict[str, Any]) -> le.Launch:
    return le.Launch.model_validate(launch)


class Experiments:
    """`lab_evaluations.ExperimentStore` over 0043 (D7's records + `PgLabReads`)."""

    def __init__(self, store, reads) -> None:
        self.store, self.reads = store, reads              # PgLabDataStore, PgLabReads

    async def _row(self, provider: str, e: dict[str, Any], refs: list[str]) -> dict[str, Any]:
        """The route's `{experiment_id, created_at, launch, report}` of one 0043 row: the
        launch and its clock are the two run records' (what `freeze` published)."""
        # ponytail: two record reads per experiment (N+1); the launch as a column of
        # `lab_experiments` when a provider's experiments outgrow one page.
        base, cand = [(await self.store.resolve(ref, provider_org_id=provider))
                      .model_dump(mode="json", by_alias=True, exclude_unset=True)
                      for ref in refs]
        report = e["report"]
        return {"experiment_id": e["experiment_id"], "created_at": base["created_at"],
                "launch": {"experiment_id": e["experiment_id"],
                           **{k: base[k] for k in ("dataset_ref", "harness_ref",
                                                   "evaluator_ref", "seed", "max_cases")},
                           "baseline_serving_ref": base["serving_ref"],
                           "candidate_serving_ref": cand["serving_ref"],
                           "run_limit": base["budgets"][0]["limit"],
                           "protocol": e["protocol"]},
                "report": None if report is None else {**json.loads(report["body"]),
                                                       "report_digest": report["report_digest"]}}

    async def experiments(self, provider_org_id: str) -> list[dict[str, Any]]:
        """Newest first; one whose runs `freeze` never created is not listed (as the route)."""
        return [await self._row(provider_org_id, e, refs)
                for e in await self.reads.experiments(provider_org_id=provider_org_id)
                if (refs := _refs(e)) is not None]

    async def put(self, provider_org_id: str, experiment: dict[str, Any], *,
                  actor: str | None = None) -> dict[str, Any]:
        wanted = _launch(experiment["launch"])
        have = await self._stored(provider_org_id, wanted.experiment_id)
        if have is None:
            if actor is None:
                raise errors.DependencyUnavailable(NO_ACTOR)
            refs = [await self.store.publish(
                le._run_payload(provider_org_id, wanted, arm, experiment["created_at"]),
                provider_org_id=provider_org_id, actor=actor) for arm in le.ARMS]
            protocol = experiment["launch"]["protocol"]
            try:
                await self.reads.put_experiment(
                    wanted.experiment_id, provider_org_id=provider_org_id, protocol=protocol,
                    protocol_digest=_digest(protocol), baseline_run_ref=refs[0],
                    candidate_run_ref=refs[1], actor=actor)
                have = {**experiment, "launch": wanted.model_dump(mode="json",
                                                                  exclude_unset=True)}
            except errors.IdempotencyConflict:      # a concurrent first launch won the id
                have = await self._stored(provider_org_id, wanted.experiment_id)
        if have is None or _launch(have["launch"]) != wanted:
            raise errors.IdempotencyConflict("the experiment id names another launch")
        return have

    async def _stored(self, provider: str, experiment_id: str) -> dict[str, Any] | None:
        for e in await self.reads.experiments(provider_org_id=provider):
            if e["experiment_id"] == experiment_id:
                refs = _refs(e)
                if refs is None:
                    raise errors.DependencyUnavailable(NO_REFS)
                return await self._row(provider, e, refs)
        return None


def _refs(e: dict[str, Any]) -> list[str] | None:
    """The run refs of a 0043 row - which names its runs only by their status (SR-AP10-1)."""
    runs = [e[arm] for arm in le.ARMS]
    return None if None in runs else [run["run_ref"] for run in runs]


class Subscriptions:
    """B3's ledger (`PgCheckpointLedger`) with WR-B3-1's provider listing as the route reads
    it: 0043's `{subscription, decisions}` rows flattened."""

    def __init__(self, ledger) -> None:
        self.ledger = ledger

    def __getattr__(self, name):
        return getattr(self.ledger, name)

    async def listing(self, provider_org_id: str) -> list[dict[str, Any]]:
        return [{**row["subscription"], "decisions": row["decisions"]}
                for row in await self.ledger.listing(provider_org_id=provider_org_id)]


class Catalog:
    """`lab_evaluations.Catalog`: the evaluator by ref (0034) and 0066's launch listing."""

    def __init__(self, store) -> None:
        self.store = store

    async def catalog(self, provider_org_id: str) -> dict[str, Any]:
        """SR-AP10-1 (0066): the provider's datasets, harnesses, evaluators and ready private
        dev servings; a database failure is the route's 503."""
        return await self.store.eval_catalog(provider_org_id=provider_org_id)

    async def evaluator(self, provider_org_id: str, evaluator_ref: str) -> dict[str, Any]:
        return await self.store.evaluator(evaluator_ref, provider_org_id=provider_org_id)


def evaluation_ports(connect) -> dict[str, Any]:
    """The three ports for `LabEvaluations(sessions, access, store=..., **ports)` on one
    pool (WR-AP10-1 into `lab.compose.lab_evaluations_pipelines_releases`)."""
    from ...state.lab_data import PgLabDataStore, PgLabReads
    from ...state.lab_pipeline import PgCheckpointLedger
    store = PgLabDataStore(connect)
    return {"experiments": Experiments(store, PgLabReads(connect)),
            "ledger": Subscriptions(PgCheckpointLedger(connect)),
            "catalog": Catalog(store)}
