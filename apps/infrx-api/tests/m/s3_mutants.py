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

import pathlib
import shutil
import sys

from ..contracts import mutants as shared
from ..contracts.mutants import Mutant, Runner

# Relative to apps/infrx-api: item 3 mutates the installer too (`deploy/`).
S3 = "infrx/media/s3.py"
C = "infrx/config.py"
P = "infrx/gateway/pilot.py"
D = "deploy/preflight.py"
H = "tests/m/test_s3.py"        # review A1: the harness's own credential and cleanup rules


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
STARTS = "test_create_app_from_settings_stages_into_the_configured_bucket"
REFUSES = "test_create_app_refuses_to_start_when_the_bucket_does_not_answer"
INSTALL_ASKS = "test_a_pilot_install_asks_the_bucket_before_replacing_the_file"
INSTALL_REFUSES = "test_a_pilot_install_without_a_bucket_is_refused"
NO_BOTOCORE = "test_the_pilot_runtime_probe_refuses_an_image_without_botocore"
CREDENTIALS = "test_the_s3_cases_keep_the_environments_credentials_unless_told_to_use_local_ones"
CLEANUP = "test_what_a_case_writes_is_removed_after_it"
CLEANUP_GUARD = "test_the_cleanup_empties_only_one_cases_own_prefix"
NO_CREATE = "test_no_bucket_is_created_without_local_credentials"
STUBBED = "test_a_conflict_or_a_broken_body_is_an_error_and_a_404_is_absent"
NO_BUCKET = "test_a_store_on_a_missing_bucket_reads_writes_and_lists_nothing"
WRITE_404 = "test_a_404_on_a_write_or_a_listing_is_an_error_not_absence"
TWO_ATTEMPTS = "test_a_failing_call_is_tried_twice_and_no_more"
INSTALL_WAIT = "test_a_pilot_install_waits_for_the_bucket_a_bounded_time"
BUILT_IN_CODE = "test_a_store_built_in_code_refuses_the_prefixes_the_settings_refuse"
PAGES = "test_a_listing_past_one_page_names_every_key"
WHOLE_DIGEST = "test_only_a_whole_object_sha256_is_a_digest"

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
       S3, "            if absent_ok and code in MISSING:", "            if True:",
       DENIED, WRITE_ONCE, NO_CHECKSUM, STUBBED, WRITE_404, NO_BUCKET, s3=True),
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
       S3, "if len(raw) == 32 else NO_DIGEST", "if len(raw) == 32 else None",
       NO_CHECKSUM, WHOLE_DIGEST, s3=True),
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
    # === item 3: the composition and the installer ========================================
    _m("s3_probe_skipped", "a bucket that does not answer HeadBucket refuses startup",
       P, "        objects.probe()\n", "", REFUSES),
    _m("s3_refusal_echoes_the_failure", "the refusal names the S3 error, never the endpoint "
       "or the bucket", P, "({reason(failure)})", "({failure})", REFUSES),
    _m("s3_prefix_not_composed", "the composed store uses S3_MEDIA_PREFIX",
       P, "deployment.s3_media_prefix, deployment.s3_endpoint_url)",
       '"infrx/", deployment.s3_endpoint_url)', STARTS, s3=True),
    _m("s3_bucket_not_asked_at_install", "a pilot install asks the bucket before replacing "
       "the file", D, "        problems += bucket_problems(cfg, values)\n", "",
       INSTALL_REFUSES),
    _m("s3_install_bucket_optional", "a pilot install without S3_MEDIA_BUCKET is refused",
       D, "    if not bucket:\n        return [", "    if not bucket:\n        return []\n        return [",
       INSTALL_ASKS, INSTALL_REFUSES),
    _m("s3_install_refusal_ignored", "a HeadBucket the host is refused is an install refusal",
       D, "    if done.returncode == 0:\n        return []", "    if True:\n        return []",
       INSTALL_ASKS),
    _m("s3_install_endpoint_dropped", "the install asks the endpoint the gateway will use",
       D, '*(["--endpoint-url", endpoint] if endpoint else [])', "", INSTALL_ASKS),
    _m("s3_install_refusal_echoes_the_bucket", "the install refusal never names the bucket",
       D, "code.group(1) if code else f'exit {done.returncode}'", "bucket", INSTALL_ASKS),
    _m("s3_image_without_botocore", "the pilot probe refuses an image without botocore",
       D, '        if not _importable("botocore"):', "        if False:", NO_BOTOCORE),
    # === review A1: the harness can run on the box ========================================
    _m("s3_local_flag_ignored", "without INFRX_M_S3_LOCAL_CREDS the environment's (the "
       "instance role's) credentials are used", H,
       '    if secret is None and not local and os.environ.get(LOCAL_FLAG) != "1":\n        return\n',
       "", CREDENTIALS),
    _m("s3_cleanup_skipped", "what a case writes under its test prefix is deleted after it",
       H, "            objects.client.delete_objects(", "            (lambda **kw: None)(",
       CLEANUP, s3=True),
    _m("own_v_cleanup_unregistered", "every store a case makes is registered for the teardown",
       H, "    if secret is None:\n        _WRITTEN.append(objects)\n", "", CLEANUP, s3=True),
    _m("own_v_cleanup_guard_dropped", "a cleanup empties only one case's test/m1l2/<uuid>/",
       H, "    if not CASE_PREFIX_RE.fullmatch(objects.prefix):\n        raise ValueError(",
       "    if False:\n        raise ValueError(", CLEANUP_GUARD),
    _m("own_v_bucket_created_without_flag", "no CreateBucket without INFRX_M_S3_LOCAL_CREDS",
       H, '    if BUCKET not in _READY and secret is None and os.environ.get(LOCAL_FLAG) == "1":',
       "    if BUCKET not in _READY and secret is None:", NO_CREATE),
    _m("own_v_timeout_refusal_names_bucket", "an install's timeout refusal never names the bucket",
       D, '        return [f"S3_MEDIA_BUCKET: HeadBucket did not answer within "',
       '        return [f"S3_MEDIA_BUCKET: HeadBucket {bucket} did not answer within "', INSTALL_WAIT),
    # === review A2: every arm of the error-vs-absent rule ================================
    _m("own_nosuchbucket_is_absent", "a missing bucket is an error wherever S3 says so",
       S3, 'MISSING = ("404", "NoSuchKey")', 'MISSING = ("404", "NoSuchKey", "NoSuchBucket")',
       NO_BUCKET, s3=True),
    _m("own_body_failure_is_absent", "a body that breaks mid-read is an error, not absence",
       S3, '        return self.client.get_object(Bucket=self.bucket, Key=self.prefix + key)["Body"].read()',
       '        try:\n'
       '            return self.client.get_object(Bucket=self.bucket, Key=self.prefix + key)["Body"].read()\n'
       '        except Exception:\n            return None', STUBBED),
    _m("own_conflict_is_not_written", "a 409 conditional-write conflict is a retryable error, "
       "never 'occupied'", S3, '            if code == "PreconditionFailed":',
       '            if code in ("PreconditionFailed", "ConditionalRequestConflict"):', STUBBED),
    # === review A3: absent is a read's answer only ========================================
    _m("own_404_absent_everywhere", "a 404 from a write or a listing is an error, not absence",
       S3, "            if absent_ok and code in MISSING:", "            if code in MISSING:",
       WRITE_404),
    # === review A4: bounded time per call and per install =================================
    _m("own_retries_count_retries", "an S3 call is attempted twice in all",
       S3, '"total_max_attempts": ATTEMPTS}', '"max_attempts": 3}', TWO_ATTEMPTS),
    _m("own_install_waits_unbounded", "an install waits for HeadBucket a bounded time",
       D, "capture_output=True, text=True, timeout=BUCKET_PROBE_TIMEOUT_S)",
       "capture_output=True, text=True)", INSTALL_WAIT),
    # === review A5: never the bucket root, never a dot segment ============================
    _m("own_prefix_may_be_root", "the prefix is at least one segment (never the bucket root)",
       C, r'S3_PREFIX_RE = re.compile(r"(?:(?!\.\.?/)[A-Za-z0-9._-]+/)+")',
       r'S3_PREFIX_RE = re.compile(r"(?:(?!\.\.?/)[A-Za-z0-9._-]+/)*")', SETTINGS),
    _m("own_prefix_dot_segments", "a . or .. segment is refused",
       C, r'S3_PREFIX_RE = re.compile(r"(?:(?!\.\.?/)[A-Za-z0-9._-]+/)+")',
       r'S3_PREFIX_RE = re.compile(r"(?:[A-Za-z0-9._-]+/)+")', SETTINGS),
    _m("own_store_takes_any_prefix", "a store built in code refuses what the settings refuse",
       S3, "        if not S3_PREFIX_RE.fullmatch(prefix):", "        if False:", BUILT_IN_CODE),
    # === review A6: a listing is every page =================================================
    _m("own_listing_first_page_only", "a listing reads every page, not the first 1000 keys",
       S3, '        pages = self.client.get_paginator("list_objects_v2").paginate(\n'
           '            Bucket=self.bucket, Prefix=self.prefix + prefix)',
       "        pages = [self.client.list_objects_v2(\n"
       "            Bucket=self.bucket, Prefix=self.prefix + prefix)]", PAGES, s3=True),
    # === review A7: only a whole-object SHA-256 is a digest ===============================
    _m("own_composite_checksum_is_a_digest", "a COMPOSITE checksum is no object's digest",
       S3, '    if head.get("ChecksumType", "FULL_OBJECT") != "FULL_OBJECT":', "    if False:",
       WHOLE_DIGEST),
    _m("own_checksum_decoded_leniently", "a checksum is strict base64 (`...=-N` is not one)",
       S3, '"", validate=True)', '"")', WHOLE_DIGEST),
    _m("own_checksum_length_unchecked", "a digest is 32 bytes, nothing shorter",
       S3, "if len(raw) == 32 else NO_DIGEST", "if True else NO_DIGEST", WHOLE_DIGEST),
)



def _layout(root: pathlib.Path) -> pathlib.Path:
    """The package, the tests and the installer (item 3's cases load `deploy/preflight.py`
    by path, as tests/i does); `openrouter` because create_app reads its model map."""
    ignore = shutil.ignore_patterns("__pycache__", ".venv")
    for name in ("infrx", "tests", "deploy", "openrouter"):
        shutil.copytree(shared.API_DIR / name, root / name, ignore=ignore)
    shutil.copy2(shared.API_DIR / "pyproject.toml", root / "pyproject.toml")
    return root


RUNNER = Runner(name="m1l2", package="", layout=_layout, targets=("tests/m/test_s3.py",),
                env=("INFRX_M_S3_ENDPOINT", "INFRX_M_S3_BUCKET", "INFRX_M_S3_LOCAL_CREDS"))


def run_mutant(mutant: Mutant):
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    sys.exit(shared.main(MUTANTS, RUNNER, "run M1-L2's S3 object store mutation list"))
