#!/usr/bin/env python3
"""r1 R32 for M1-L2: one single-edit mutant per guard `tests/m/test_s3.py` claims.

The shared runner (`tests/contracts/mutants.py`, F2R item 9) applies each mutant to a copy
of the package and runs the named cases there; only "the named cases failed, and only
they" is a kill. The S3 cases read `INFRX_M_S3_ENDPOINT`/`INFRX_M_S3_BUCKET`, which the
runner passes through (`Runner.env`) and nothing else of the caller's environment.

    INFRX_M_S3_ENDPOINT=http://127.0.0.1:55500 INFRX_MUTANTS=all \\
        uv run --frozen pytest -q tests/m/test_s3_mutants.py
    uv run --frozen python -m tests.m.s3_mutants --list
"""
from __future__ import annotations

import sys

from ..contracts import mutants as shared
from ..contracts.mutants import Mutant, Runner

S3 = "media/s3.py"
C = "config.py"


def _m(name, invariant, file, old, new, *cases, dies_by=(), occurrences=1, s3=False):
    """`s3=True`: every case that can see this mutant needs the S3 endpoint."""
    mutant = Mutant(name=name, invariant=invariant, file=file, old=old, new=new, cases=cases,
                    dies_by=tuple(dies_by), occurrences=occurrences)
    if s3:
        NEEDS_S3.add(name)
    return mutant


NEEDS_S3: set[str] = set()
SETTINGS = "test_the_store_settings_refuse_a_value_that_cannot_place_an_object"

MUTANTS: tuple[Mutant, ...] = (
    # === item 1: the settings that place the store, and a store that cannot answer =======
    _m("s3_prefix_unchecked", "S3_MEDIA_PREFIX is path segments ending in / or startup refuses",
       C, "    if not S3_PREFIX_RE.fullmatch(deployment.s3_media_prefix):", "    if False:",
       SETTINGS),
    _m("s3_prefix_prefix_matched", "the whole prefix is checked, not its first segment",
       C, "    if not S3_PREFIX_RE.fullmatch(deployment.s3_media_prefix):",
       "    if not S3_PREFIX_RE.match(deployment.s3_media_prefix):", SETTINGS),
    _m("s3_endpoint_unchecked", "S3_ENDPOINT_URL is a scheme and an authority or startup refuses",
       C, "    if deployment.s3_endpoint_url and not S3_ENDPOINT_RE.fullmatch(deployment.s3_endpoint_url):",
       "    if False:", SETTINGS),
    _m("s3_endpoint_prefix_matched", "an endpoint with a path, query or credentials refuses",
       C, "    if deployment.s3_endpoint_url and not S3_ENDPOINT_RE.fullmatch(deployment.s3_endpoint_url):",
       "    if deployment.s3_endpoint_url and not S3_ENDPOINT_RE.match(deployment.s3_endpoint_url):",
       SETTINGS),
    _m("s3_transport_failure_is_absence", "a store that does not answer is an error, never absent",
       S3, "        except BotoCoreError as failure:\n            raise",
       "        except BotoCoreError as failure:\n            return None\n            raise",
       "test_an_unreachable_store_is_an_error_never_absence"),
)

RUNNER = Runner(name="m1l2", targets=("tests/m/test_s3.py",),
                env=("INFRX_M_S3_ENDPOINT", "INFRX_M_S3_BUCKET"))


def run_mutant(mutant: Mutant):
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    sys.exit(shared.main(MUTANTS, RUNNER, "run M1-L2's S3 object store mutation list"))
