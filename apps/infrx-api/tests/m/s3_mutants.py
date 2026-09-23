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
ROUND_TRIP = "test_a_round_trip_returns_the_bytes_their_size_type_and_digest"
WRITE_ONCE = "test_put_is_write_once"
MISSING_KEY = "test_a_missing_key_is_absent_to_every_operation"
DELETE = "test_delete_removes_exactly_one_object"
LISTING = "test_a_listing_is_exactly_its_prefix_and_names_keys_the_store_takes"
ISOLATION = "test_two_stores_never_see_each_others_objects"
SIZE_BOUND = "test_an_upload_at_its_byte_cap_finalizes_and_one_byte_over_is_refused_unread"
COLLECTOR = "test_the_collector_keeps_a_live_jobs_media_and_collects_the_rest"
DENIED = "test_a_denied_store_is_an_error_never_absence"
NO_CHECKSUM = "test_an_object_stored_without_our_checksum_is_present_and_matches_no_digest"

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
    # === item 2: the port's conformance on a real S3-compatible store =====================
    _m("s3_key_without_the_prefix", "every object lives under S3_MEDIA_PREFIX",
       S3, "Key=self.prefix + key", "Key=key", ISOLATION, occurrences=4, s3=True),
    _m("s3_put_not_conditional", "put_if_absent is write-once (If-None-Match: *)",
       S3, '            IfNoneMatch="*",\n', "", WRITE_ONCE, NO_CHECKSUM, s3=True),
    _m("s3_any_error_is_absence", "only a 404 is absent; a refused call is an error",
       S3, "            if code in MISSING:", "            if True:",
       DENIED, WRITE_ONCE, NO_CHECKSUM, s3=True),
    _m("s3_absence_is_an_error", "a missing object is None, not a failure",
       S3, 'MISSING = ("404", "NoSuchKey")', "MISSING = ()", MISSING_KEY, ISOLATION, s3=True),
    _m("s3_precondition_is_an_error", "an occupied key is False, not a failure",
       S3, '            if code == "PreconditionFailed":', "            if False:",
       WRITE_ONCE, NO_CHECKSUM, s3=True),
    _m("s3_digest_not_asked_for", "head answers the SHA-256 the server measured",
       S3, ',\n                                       ChecksumMode="ENABLED")', ")",
       ROUND_TRIP, WRITE_ONCE, COLLECTOR, s3=True),
    _m("s3_checksum_not_sent", "the PutObject carries the SHA-256 the server checks",
       S3, 'IfNoneMatch="*",\n            ChecksumSHA256=base64.b64encode(hashlib.sha256(data).digest()).decode())',
       'IfNoneMatch="*")', ROUND_TRIP, WRITE_ONCE, COLLECTOR, s3=True),
    _m("s3_unchecksummed_object_is_absent", "an object without our checksum is present",
       S3, "if checksum else NO_DIGEST", "if checksum else None", NO_CHECKSUM, s3=True),
    _m("s3_size_over_by_one", "describe reports the stored size, not one more",
       S3, '(head["ContentLength"], ', '(head["ContentLength"] + 1, ',
       ROUND_TRIP, WRITE_ONCE, SIZE_BOUND, s3=True),
    _m("s3_size_under_by_one", "describe reports the stored size, not one less",
       S3, '(head["ContentLength"], ', '(head["ContentLength"] - 1, ',
       ROUND_TRIP, WRITE_ONCE, SIZE_BOUND, s3=True),
    _m("s3_content_type_dropped", "describe reports the stored content type",
       S3, " ContentType=content_type,", "", ROUND_TRIP, WRITE_ONCE, s3=True),
    _m("s3_listing_ignores_its_prefix", "a listing is only its prefix (the sweep never "
       "deletes a live ref)", S3, "Prefix=self.prefix + prefix)", "Prefix=self.prefix)",
       LISTING, COLLECTOR, s3=True),
    _m("s3_listing_keeps_the_store_prefix", "a listing names keys the store takes",
       S3, 'item["Key"][len(self.prefix):]', 'item["Key"]',
       LISTING, COLLECTOR, ISOLATION, DELETE, s3=True),
)

RUNNER = Runner(name="m1l2", targets=("tests/m/test_s3.py",),
                env=("INFRX_M_S3_ENDPOINT", "INFRX_M_S3_BUCKET"))


def run_mutant(mutant: Mutant):
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    sys.exit(shared.main(MUTANTS, RUNNER, "run M1-L2's S3 object store mutation list"))
