#!/usr/bin/env python3
"""R32/R40/R83 for lab-sql LW7's Python ports (`PgLabVariants`, `PgLabImportJobs.requeue`):
one single-edit defect per decision, killed by `test_lw7_units.py`. The SQL list is
`tests/d/test_code_mutants_lw7.py`.

    INFRX_MUTANTS=all uv run --frozen pytest -q tests/l3sql/test_mutants.py
"""
from __future__ import annotations

from ..contracts import mutants as shared
from ..contracts.mutants import Mutant, Runner

RUNNER = Runner(name="l3sql", targets=("tests/l3sql/test_lw7_units.py",))
V, J = "state/lab_variants.py", "state/lab_data.py"
VARIANTS = "test_variants__are_read_for_the_callers_provider_as_answered"
REQUEUE = "test_requeue__names_the_failed_job_the_new_id_the_provider_and_the_requeuer"

MUTANTS: tuple[Mutant, ...] = (
    Mutant(name="lw7_variants_other_provider", file=V,
           invariant="the listing is the caller's provider's",
           old='{"provider_org_id": provider_org_id})', new='{"provider_org_id": None})',
           cases=(VARIANTS,)),
    Mutant(name="lw7_variants_dropped", file=V, invariant="the listing is returned whole",
           old="        return await self._call(", new="        return [] and await self._call(",
           cases=(VARIANTS,)),
    Mutant(name="lw7_requeue_new_id_dropped", file=J,
           invariant="the gateway's new id (where it copied the rows) is the new job's",
           old='"job_id": job_id, "new_job_id": new_job_id,', new='"job_id": job_id,',
           cases=(REQUEUE,)),
    Mutant(name="lw7_requeue_actor_dropped", file=J,
           invariant="the requeuer is sent as the new job's actor",
           old='"provider_org_id": provider_org_id,\n            "actor": actor})',
           new='"provider_org_id": provider_org_id,\n            "actor": None})',
           cases=(REQUEUE,)),
)


def run_mutant(mutant) -> shared.Result:
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run lab-sql LW7's Python mutation list"))
