#!/usr/bin/env python3
"""R32/R40/R83 for lab-sql LW7/LW9's Python ports (`PgLabVariants`, `PgLabImportJobs.requeue`,
`PgReleaseStore.tally`, R3's identity write): one single-edit defect per decision, killed by
`test_lw7_units.py` / `test_lw9_units.py`. The SQL lists are
`tests/d/test_code_mutants_lw7.py` / `_lw9.py`.

    INFRX_MUTANTS=all uv run --frozen pytest -q tests/l3sql/test_mutants.py
"""
from __future__ import annotations

from ..contracts import mutants as shared
from ..contracts.mutants import Mutant, Runner

RUNNER = Runner(name="l3sql", targets=("tests/l3sql/test_lw7_units.py",
                                        "tests/l3sql/test_lw9_units.py"))
V, J, D9, R3 = ("state/lab_variants.py", "state/lab_data.py", "state/lab_rollout.py",
                "rollouts/optimization/__init__.py")
VARIANTS = "test_variants__are_read_for_the_callers_provider_with_their_identities"
PUT = "test_put_identities__sends_both_identities_for_the_providers_variant"
TALLY = "test_tally__is_read_for_the_revision_as_answered"
STORE = "test_r3_store__writes_both_identities_as_the_variant_is_created"
REQUEUE = "test_requeue__names_the_failed_job_the_new_id_the_provider_and_the_requeuer"

MUTANTS: tuple[Mutant, ...] = (
    Mutant(name="lw7_variants_other_provider", file=V,
           invariant="the listing is the caller's provider's",
           old='{"provider_org_id": provider_org_id})', new='{"provider_org_id": None})',
           cases=(VARIANTS,)),
    Mutant(name="lw7_variants_dropped", file=V, invariant="the listing is returned whole",
           old='        return await self._call("lab_optimization_variant_listing",',
           new='        return [] and await self._call("lab_optimization_variant_listing",',
           cases=(VARIANTS,)),
    # --- LW9 (0058)
    Mutant(name="lw9_variants_unidentified", file=V,
           invariant="the listing read is 0058's, with both identities (WR-LW7-3a)",
           old='"lab_optimization_variant_listing",', new='"lab_optimization_variants",',
           cases=(VARIANTS,)),
    Mutant(name="lw9_identities_swapped", file=V,
           invariant="the base identity is stored as the base",
           old='"base": base, "variant": variant,', new='"base": variant, "variant": base,',
           cases=(PUT,)),
    Mutant(name="lw9_identities_other_provider", file=V,
           invariant="the identities are written as the caller's provider's (R227)",
           old='"provider_org_id": provider_org_id, "actor": actor}',
           new='"provider_org_id": None, "actor": actor}', cases=(PUT,)),
    Mutant(name="lw9_tally_other_revision", file=D9,
           invariant="the tally is this revision's",
           old='self._call("lab_release_tally", {"policy_ref": policy_ref})',
           new='self._call("lab_release_tally", {"policy_ref": None})', cases=(TALLY,)),
    Mutant(name="lw9_tally_dropped", file=D9, invariant="the tally is returned whole",
           old='        return await self._call("lab_release_tally",',
           new='        return [] and await self._call("lab_release_tally",', cases=(TALLY,)),
    Mutant(name="lw9_r3_identities_unchecked", file=R3,
           invariant="only the registered identities are stored beside the variant",
           old="    if identities is not None and tuple(", new="    if False and tuple(",
           cases=(STORE,)),
    Mutant(name="lw9_r3_identities_portless", file=R3,
           invariant="identities without the variants port are refused before publishing "
                     "(F3, 1-LW9-RV-3)",
           old="    if identities is not None and variants is None:",
           new="    if False:", cases=(STORE,)),
    Mutant(name="lw9_r3_identities_unwritten", file=R3,
           invariant="R3 writes both identities when it creates the variant",
           old="    if identities is not None:\n        base, candidate",
           new="    if False:\n        base, candidate", cases=(STORE,)),
    Mutant(name="lw9_r3_identities_swapped", file=R3,
           invariant="the base revision's identity is stored as the base",
           old="base=base, variant=candidate,", new="base=candidate, variant=base,",
           cases=(STORE,)),
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
