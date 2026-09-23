#!/usr/bin/env python3
"""R32/R40/R83 for MPILOT: one single-edit defect per invariant `test_pilot_media.py` claims.

Its own list beside M's (`tests/m/mutants.py` keeps M1-M4's floors and file set): the same
shared runner - one mutant at a time in a throwaway copy, the named cases run there, a
pristine baseline first, and only an assertion or a typed `DomainError` counts as a kill
unless the mutant declares its death in `dies_by`.

Several edits repeat an anchor M's list already mutates (`resolve_owned`'s guards): the
defect is the same, the killer is new - proof that the admission path (`materialize` of an
`infrx-upload:` source) is guarded by those lines too, not only `stage`.

    uv run --frozen pytest -q tests/m/test_pilot_mutants.py
    uv run --frozen python -m tests.m.pilot_mutants --list
"""
from __future__ import annotations

import pathlib
import re

from ..contracts import mutants as shared
from ..contracts.mutants import Mutant, Outcome, Result, Runner   # noqa: F401

API_DIR = pathlib.Path(__file__).resolve().parents[2]
SUITE_FILE = "tests/m/test_pilot_media.py"
S = "media/store.py"
R = "media/prepare.py"
U = "media/uploads.py"

E2E = "test_mpilot__an_upload_named_in_a_job_over_the_mounted_gateway"


def _m(name, invariant, file, old, new, *cases, dies_by=(), occurrences=1) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=file, old=old, new=new, cases=cases,
                  dies_by=tuple(dies_by), occurrences=occurrences)


MUTANTS: tuple[Mutant, ...] = (
    # === item 1: an `infrx-upload:` source is resolved at admission ======================
    _m("upload_ref_not_resolved",
       "an infrx-upload: source is the finalized upload, not a 400 (gap 1)",
       U, "        if not source.startswith(UPLOAD_REF_SCHEME):\n"
          "            return await super().materialize(org_id, source)",
       "        if True:\n            return await super().materialize(org_id, source)",
       "test_mpilot__a_chat_naming_a_finalized_upload_is_prepared_from_the_store", E2E),
    _m("admission_ignores_the_owner",
       "another org's upload handle is not_found at admission (ownership)",
       S, "        media = self.refs.get((org_id, ref))",
       "        media = next((m for (_, h), m in self.refs.items() if h == ref), None)",
       "test_mpilot__another_orgs_upload_is_not_found_at_admission", E2E),
    _m("admission_accepts_an_unfinalized_upload",
       "a handle whose upload is not finalized is refused at admission, squatted or not",
       U, "                and upload.state is not UploadState.finalized:",
       "                and False:",
       "test_mpilot__an_unfinalized_upload_is_refused_at_admission"),
    _m("admission_accepts_an_expired_upload",
       "an upload past its window is 410 upload_expired at admission and at staging",
       U, "        if upload is not None and upload.org_id == org_id and self.now() >= "
          "upload.expires_at:",
       "        if False:",
       "test_mpilot__an_upload_past_its_window_is_upload_expired_at_admission", E2E),
    _m("admission_skips_the_size_bound",
       "an upload over the media bound is 413 at admission, like any source",
       R, "        budget.spend(ref.bytes)", "        budget.spend(0)",
       "test_mpilot__an_upload_over_the_media_bound_is_refused_at_admission", E2E),
    _m("admission_resolves_a_changed_object",
       "the ref names the object finalize verified: replaced or collected bytes are not_found",
       U, "                and await self.objects.head(media.storage_ref) != media.digest:",
       "                and False:",
       "test_mpilot__an_upload_whose_object_changed_is_refused_at_admission"),
)


def case_names() -> set[str]:
    """Every case `test_pilot_media.py` defines."""
    return set(re.findall(r"^def (test_\w+)\(", (API_DIR / SUITE_FILE).read_text(), re.M))


#: The shared runner's default copy (package, tests, pyproject) is all these cases need.
RUNNER = Runner(name="mpilot", targets=(SUITE_FILE,))


def run_mutant(mutant) -> Result:
    """Apply one mutant to a throwaway copy and run the cases it names."""
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run MPILOT's mutation list"))
