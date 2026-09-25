#!/usr/bin/env python3
"""M1-L2: `S3ObjectStore` (infrx/media/s3.py) - the `ObjectStore` port's conformance, run
against `InMemoryObjectStore` and a real S3-compatible store, plus the settings that
place it and the startup that refuses without it.

    uv run --frozen pytest -q tests/m/test_s3.py            # memory + the no-Docker cases
    # MinIO (the E2 stack's s3 service, or any MinIO taking the E2 literals):
    INFRX_M_S3_ENDPOINT=http://127.0.0.1:55500 INFRX_M_S3_LOCAL_CREDS=1 \
        uv run --frozen pytest -q tests/m/test_s3.py
    # AWS S3 on the box, with its instance role (nothing replaces botocore's chain):
    INFRX_M_S3_ENDPOINT=https://s3.us-east-1.amazonaws.com INFRX_M_S3_BUCKET=<bucket> \
        uv run --frozen pytest -q tests/m/test_s3.py

Without `INFRX_M_S3_ENDPOINT` the S3 half skips, naming its owner. Each S3 case writes
under a prefix of its own, `test/m1l2/<uuid>/`, of `INFRX_M_S3_BUCKET`, and everything
under it is deleted after the case. Credentials: with `INFRX_M_S3_LOCAL_CREDS=1` the E2
literals are the only ones botocore can find (and the bucket, default `infrx-m1l2`, is
created); without it botocore's own chain is untouched - the environment, then the
instance role - and no bucket is created. The no-Docker cases always use local literals
and dead local endpoints: they never reach AWS.
"""
from __future__ import annotations

import asyncio
import io
import os
import re
import sys
import uuid

import pytest
from infrx import config
from infrx.config import DEPLOYMENT_DEFAULTS, RuntimeMisconfigured
from infrx.contracts import errors
from infrx.contracts.conformance import builders as b
from infrx.contracts.limits import DEFAULTS
from infrx.media import store
from infrx.media.fetch import digest_of
from infrx.media.s3 import NO_DIGEST, S3ObjectStore

from . import test_gc
from .test_uploads import CLIP, adapter_for, created

ENDPOINT = os.environ.get("INFRX_M_S3_ENDPOINT", "")
BUCKET = os.environ.get("INFRX_M_S3_BUCKET", "infrx-m1l2")
# The E2 stack's MinIO literals (tests/integration/harness.py): local test strings.
ACCESS_KEY, SECRET_KEY = "infrxe2minio", "infrx-e2-local-secret"
# Nothing listens on the discard port: a store there never answers.
UNREACHABLE = "http://127.0.0.1:9"
LOCAL_FLAG = "INFRX_M_S3_LOCAL_CREDS"
needs_s3 = pytest.mark.skipif(
    not ENDPOINT, reason="M1-L2 (owner: M): no S3-compatible endpoint - start the E2 "
                         "stack's s3 service and export INFRX_M_S3_ENDPOINT (and "
                         "INFRX_M_S3_LOCAL_CREDS=1 for MinIO)")


def run(coroutine):
    return asyncio.run(coroutine)


def s3_env(monkeypatch, secret: str | None = None, *, local: bool = False) -> None:
    """The credentials a case's store is built with. The E2 literals become the only ones
    botocore can find with INFRX_M_S3_LOCAL_CREDS=1 (MinIO) or `local` (a case that never
    reaches a store); `secret` is a deliberately wrong one. Otherwise botocore's own chain
    is left untouched: on the box, the instance role."""
    if secret is None and not local and os.environ.get(LOCAL_FLAG) != "1":
        return
    for name in ("AWS_SESSION_TOKEN", "AWS_PROFILE", "AWS_ENDPOINT_URL", "AWS_ENDPOINT_URL_S3"):
        monkeypatch.delenv(name, raising=False)
    for name, value in (("AWS_ACCESS_KEY_ID", ACCESS_KEY),
                        ("AWS_SECRET_ACCESS_KEY", secret or SECRET_KEY),
                        ("AWS_DEFAULT_REGION", "us-east-1"), ("AWS_EC2_METADATA_DISABLED", "true"),
                        ("AWS_CONFIG_FILE", os.devnull),
                        ("AWS_SHARED_CREDENTIALS_FILE", os.devnull)):
        monkeypatch.setenv(name, value)


def unique_prefix() -> str:
    return f"test/m1l2/{uuid.uuid4().hex}/"


def absent_bucket() -> str:
    """A bucket name nobody has: random, so on AWS it is not someone else's (a 403)."""
    return f"infrx-m1l2-absent-{uuid.uuid4().hex[:20]}"


_READY: set[str] = set()
#: The stores this case wrote through; `_remove_what_the_case_wrote` empties their prefixes.
_WRITTEN: list[S3ObjectStore] = []


#: The only prefix a cleanup may empty: one case's own. On the box the bucket is the
#: project's, so neither `infrx/` nor another run's `test/m1l2/` is ever in reach.
CASE_PREFIX_RE = re.compile(r"test/m1l2/[0-9a-f]{32}/")


def remove_prefix(objects: S3ObjectStore) -> None:
    """Delete everything under one case's `test/m1l2/<uuid>/` prefix, 1000 keys a call. A
    `raise`, not an `assert`: the guard holds under `python -O` too (verifier V2)."""
    if not CASE_PREFIX_RE.fullmatch(objects.prefix):
        raise ValueError(f"refusing to empty {objects.prefix!r}: not one case's test prefix")
    pages = objects.client.get_paginator("list_objects_v2").paginate(
        Bucket=objects.bucket, Prefix=objects.prefix)
    for page in pages:
        batch = [{"Key": item["Key"]} for item in page.get("Contents", ())]
        if batch:
            objects.client.delete_objects(Bucket=objects.bucket,
                                          Delete={"Objects": batch, "Quiet": True})


def empty_what_was_written() -> None:
    """The autouse fixture's teardown: every registered store's prefix, emptied."""
    while _WRITTEN:
        remove_prefix(_WRITTEN.pop())


@pytest.fixture(autouse=True)
def _remove_what_the_case_wrote():
    yield
    empty_what_was_written()


def s3_store(monkeypatch, prefix: str | None = None, secret: str | None = None) -> S3ObjectStore:
    """A store on the test bucket under a prefix of its own, emptied after the case. With
    local credentials the bucket is made once; on AWS it must exist."""
    s3_env(monkeypatch, secret)
    objects = S3ObjectStore.connect(BUCKET, prefix or unique_prefix(), ENDPOINT)
    if secret is None:
        _WRITTEN.append(objects)
    if BUCKET not in _READY and secret is None and os.environ.get(LOCAL_FLAG) == "1":
        from botocore.exceptions import ClientError
        try:
            objects.client.create_bucket(Bucket=BUCKET)
        except ClientError as exists:
            if exists.response["Error"]["Code"] not in ("BucketAlreadyOwnedByYou",
                                                        "BucketAlreadyExists"):
                raise
        _READY.add(BUCKET)
    return objects


@pytest.fixture(params=["memory", pytest.param("s3", marks=needs_s3)])
def objects(request, monkeypatch):
    """Every conformance case runs on both stores: the reference and the adapter."""
    return store.InMemoryObjectStore() if request.param == "memory" else s3_store(monkeypatch)


@pytest.fixture(params=["memory", pytest.param("s3", marks=needs_s3)])
def pair(request, monkeypatch):
    """Two stores: two instances, or two sibling prefixes (`test/m1l2/<uuid>/`) of ONE
    bucket."""
    if request.param == "memory":
        return store.InMemoryObjectStore(), store.InMemoryObjectStore()
    return s3_store(monkeypatch), s3_store(monkeypatch)


class Counting:
    """Any store, saying how often an object was really downloaded."""

    def __init__(self, inner) -> None:
        self.inner, self.gets = inner, 0

    def __getattr__(self, name):
        return getattr(self.inner, name)

    async def get(self, key):
        self.gets += 1
        return await self.inner.get(key)


def uploaded(adapter, data=CLIP, **constraints):
    """An upload through the port only (no `seed`): create, the bytes, finalize."""
    handle = created(adapter, **constraints)
    run(adapter.put_upload(b.ORG_A, handle, data, "video/mp4"))
    return run(adapter.finalize_upload(b.ORG_A, handle))


# --- the settings that place the store (08 §5.1) ----------------------------------------
BAD_PREFIXES = ("/infrx/", "infrx", "infrx//", "in frx/", "infrx/media", "", "./", "../",
                "infrx/../other/", "infrx/./")
BAD_ENDPOINTS = ("ftp://minio:9000", "http://user:secret@minio:9000",
                 "http://minio:9000/bucket", "minio:9000", "https://minio:9000?x=1")


def test_the_store_settings_refuse_a_value_that_cannot_place_an_object():
    """A prefix is path segments each ending in `/` (never the root, never `//`); an
    endpoint is a scheme and an authority - no path, no query, no credentials. Refused
    at startup in any mode, naming the setting and never the value."""
    for field, name, values in (("s3_media_prefix", "S3_MEDIA_PREFIX", BAD_PREFIXES),
                                ("s3_endpoint_url", "S3_ENDPOINT_URL", BAD_ENDPOINTS)):
        for value in values:
            deployment = DEPLOYMENT_DEFAULTS.replace(**{field: value})
            with pytest.raises(RuntimeMisconfigured) as refused:
                config.validate_deployment(deployment, "pilot")
            assert name in str(refused.value), value
            assert not value or value not in str(refused.value)
            assert "secret" not in str(refused.value)
    for prefix in ("infrx/", "test/m1l2/0a1b/", "a.b-c_d/", ".hidden/", "v1.2/"):
        config.validate_deployment(DEPLOYMENT_DEFAULTS.replace(s3_media_prefix=prefix), "pilot")
    for endpoint in ("", "http://127.0.0.1:55500", "https://s3.us-east-1.amazonaws.com",
                     "http://minio:9000/"):
        config.validate_deployment(DEPLOYMENT_DEFAULTS.replace(s3_endpoint_url=endpoint), "dev")


def test_a_store_built_in_code_refuses_the_prefixes_the_settings_refuse():
    for prefix in ("", "infrx", "../", "infrx/../other/", "./"):
        with pytest.raises(ValueError, match="S3_MEDIA_PREFIX"):
            S3ObjectStore(None, "infrx-m1l2", prefix)
    assert S3ObjectStore(None, "infrx-m1l2", "infrx/").prefix == "infrx/"


def test_the_store_settings_are_read_from_the_environment():
    read = config.from_env({"S3_MEDIA_PREFIX": "pilot/media/",
                            "S3_ENDPOINT_URL": "http://127.0.0.1:55500"}).deployment
    assert (read.s3_media_prefix, read.s3_endpoint_url) == ("pilot/media/",
                                                             "http://127.0.0.1:55500")
    assert config.from_env({}).deployment.s3_media_prefix == "infrx/"


# --- a store that cannot answer (no Docker) --------------------------------------------
def test_an_unreachable_store_is_an_error_never_absence(monkeypatch):
    """Fail closed: a store that does not answer never says "not there" (a staging would
    then write, a finalize would call the destination empty) - it is a typed, retryable
    `dependency_unavailable`, whatever the transport said."""
    s3_env(monkeypatch, local=True)
    objects = S3ObjectStore.connect("infrx-m1l2", "test/m1l2/", UNREACHABLE)
    with pytest.raises(errors.DependencyUnavailable):
        run(objects.head("media/k"))


# --- the port's conformance, on both stores (item 2) -----------------------------------
SOURCE = f"media/{b.ORG_A}/v1/0123456789abcdef/source"


def test_a_round_trip_returns_the_bytes_their_size_type_and_digest(objects):
    """One byte and a body at MAX_MEDIA_BYTES: the bytes, `(size, type)` and the digest of
    exactly what was stored come back."""
    for key, data, mime in ((SOURCE, b"x", "video/mp4"),
                            (f"payloads/{b.ORG_A}/r.json", os.urandom(DEFAULTS.max_media_bytes),
                             "application/json")):
        assert run(objects.put_if_absent(key, data, mime)) is True
        assert run(objects.get(key)) == data
        assert run(objects.describe(key)) == (len(data), mime)
        assert run(objects.head(key)) == digest_of(data)


def test_put_is_write_once(objects):
    """The same bytes again, or other bytes, write nothing and say so (False): the first
    object, its type and its digest stay."""
    assert run(objects.put_if_absent(SOURCE, b"first", "video/mp4")) is True
    assert run(objects.put_if_absent(SOURCE, b"first", "video/mp4")) is False
    assert run(objects.put_if_absent(SOURCE, b"other bytes", "video/webm")) is False
    assert run(objects.get(SOURCE)) == b"first" and run(objects.head(SOURCE)) == digest_of(b"first")
    assert run(objects.describe(SOURCE)) == (5, "video/mp4")


def test_a_missing_key_is_absent_to_every_operation(objects):
    assert run(objects.head(SOURCE)) is None and run(objects.get(SOURCE)) is None
    assert run(objects.describe(SOURCE)) is None and run(objects.keys("media/")) == []
    run(objects.delete(SOURCE))                        # absent is not an error


def test_delete_removes_exactly_one_object(objects):
    other = SOURCE.replace("/source", "/prepared")
    for key in (SOURCE, other):
        run(objects.put_if_absent(key, b"bytes", "video/mp4"))
    run(objects.delete(SOURCE))
    run(objects.delete(SOURCE))                        # twice is not an error either
    assert run(objects.head(SOURCE)) is None and run(objects.get(other)) == b"bytes"
    assert run(objects.keys("")) == [other]


def test_a_listing_is_exactly_its_prefix_and_names_keys_the_store_takes(objects):
    keys = (f"media/{b.ORG_A}/v1/1/source", f"media/{b.ORG_A}/v1/2/prepared",
            f"uploads/{b.ORG_A}/upl_x", f"payloads/{b.ORG_A}/r.json")
    for key in keys:
        run(objects.put_if_absent(key, key.encode(), "video/mp4"))
    assert run(objects.keys("media/")) == sorted(keys[:2])
    assert run(objects.keys("uploads/")) == [keys[2]]
    assert run(objects.keys("")) == sorted(keys)
    for key in run(objects.keys("")):
        assert run(objects.get(key)) == key.encode()


def test_two_stores_never_see_each_others_objects(pair):
    """Prefix isolation: on one bucket, a sibling prefix neither reads, lists, blocks nor
    deletes another's object - which really is stored under its own prefix."""
    one, other = pair
    assert run(one.put_if_absent(SOURCE, b"one's bytes", "video/mp4")) is True
    assert run(other.head(SOURCE)) is None and run(other.get(SOURCE)) is None
    assert run(other.describe(SOURCE)) is None and run(other.keys("")) == []
    assert run(other.put_if_absent(SOURCE, b"other's", "video/webm")) is True
    run(other.delete(SOURCE))
    assert run(one.get(SOURCE)) == b"one's bytes" and run(one.keys("")) == [SOURCE]
    if isinstance(one, S3ObjectStore):
        raw = one.client.head_object(Bucket=one.bucket, Key=one.prefix + SOURCE)
        assert raw["ContentLength"] == len(b"one's bytes")


def test_an_upload_at_its_byte_cap_finalizes_and_one_byte_over_is_refused_unread(objects):
    """The size bound: an upload of exactly `max_bytes` finalizes; a PUT one byte over its
    ticket's cap stores nothing (M5: the receipt is refused before the write); and bytes
    over `MAX_MEDIA_BYTES` - every ticket's ceiling - found at a destination are
    `request_too_large` from `describe`'s exact size, without a download."""
    counting = Counting(objects)
    adapter = adapter_for(objects=counting, limits=DEFAULTS.replace(max_media_bytes=len(CLIP)))
    assert uploaded(adapter, max_bytes=len(CLIP)).bytes == len(CLIP)
    over = created(adapter, max_bytes=len(CLIP) - 1)
    with pytest.raises(errors.RequestTooLarge):
        run(adapter.put_upload(b.ORG_A, over, CLIP, "video/mp4"))
    assert run(objects.describe(adapter.upload_key(b.ORG_A, over))) is None
    beyond = created(adapter)
    # bytes at the destination behind the store's back, one past the ceiling
    assert run(objects.put_if_absent(adapter.upload_key(b.ORG_A, beyond), CLIP + b"\0",
                                     "video/mp4"))
    gets = counting.gets
    with pytest.raises(errors.RequestTooLarge):
        run(adapter.finalize_upload(b.ORG_A, beyond))
    assert counting.gets == gets, "an oversize object was downloaded to be refused"


def test_the_collector_keeps_a_live_jobs_media_and_collects_the_rest(objects):
    """M3's sweep through the store: a closed upload's destination goes at once, an
    unreferenced object after the grace, and a live job's source never."""
    adapter, jobs = adapter_for(objects=objects), test_gc.Jobs()
    ref = uploaded(adapter)
    test_gc.staged_job(adapter, jobs, ref)
    orphan = f"media/{b.ORG_B}/v1/{'a' * 16}/source"
    assert run(objects.put_if_absent(orphan, b"half a request", "video/mp4"))
    sweeper = test_gc.collector(adapter, jobs)
    assert run(sweeper.sweep()).deleted == [adapter.upload_key(b.ORG_A, ref.handle)]
    adapter.clock.advance(test_gc.GRACE)
    assert run(sweeper.sweep()).deleted == [orphan]
    assert run(objects.keys("media/")) == [ref.storage_ref]
    assert run(objects.keys("uploads/")) == []
    assert run(objects.get(ref.storage_ref)) == CLIP


# --- S3 only ---------------------------------------------------------------------------
@needs_s3
def test_a_listing_past_one_page_names_every_key(monkeypatch):
    """ListObjectsV2 answers 1000 keys a page; the 1001st is listed too. A first-page-only
    listing never deletes a live ref, but everything past page one would leak."""
    from concurrent.futures import ThreadPoolExecutor
    objects = s3_store(monkeypatch)
    keys = [f"media/{b.ORG_A}/v1/{index:016x}/source" for index in range(1001)]

    def put(key):
        objects.client.put_object(Bucket=objects.bucket, Key=objects.prefix + key, Body=b"x")

    with ThreadPoolExecutor(16) as pool:
        list(pool.map(put, keys))
    assert run(objects.keys("media/")) == sorted(keys)


@needs_s3
def test_a_denied_store_is_an_error_never_absence(monkeypatch):
    """A 403 is not a 404: every operation of a store the bucket refuses raises the typed,
    retryable `dependency_unavailable` rather than answering "absent" or "not written"."""
    s3_store(monkeypatch)                              # the bucket exists
    denied = s3_store(monkeypatch, secret="not-the-local-secret")
    for call in (denied.head(SOURCE), denied.get(SOURCE), denied.describe(SOURCE),
                 denied.keys("media/"), denied.put_if_absent(SOURCE, b"x", "video/mp4"),
                 denied.delete(SOURCE)):
        with pytest.raises(errors.DependencyUnavailable):
            run(call)


@needs_s3
def test_an_object_stored_without_our_checksum_is_present_and_matches_no_digest(monkeypatch):
    """An object written behind the store's back (no x-amz-checksum-sha256) is there - never
    absent, so never overwritten or finalized as empty - and equal to no digest."""
    objects = s3_store(monkeypatch)
    key = f"uploads/{b.ORG_A}/upl_behind_the_back"
    objects.client.put_object(Bucket=objects.bucket, Key=objects.prefix + key, Body=b"bytes")
    assert run(objects.head(key)) == NO_DIGEST != digest_of(b"bytes")
    assert run(objects.put_if_absent(key, b"bytes", "video/mp4")) is False


# --- the composition: create_app from settings, and the installer (item 3) ---------------
def cutover_app(config, **adapters):
    """`create_app` as the unit runs it, the object store NOT injected (the Valkey index
    and the two HTTP clients are, so nothing else leaves the process)."""
    from infrx.gateway.app import create_app
    from infrx.scheduling.memory import MemoryScheduler

    from ..g import support as g
    return create_app(config, client=g.upstream(), sb=g.supabase(),
                      index=MemoryScheduler(lambda: None), **adapters)


def settings(mode: str, bucket: str, **deployment):
    from ..g import support as g
    return g.settings(mode, s3_media_bucket=bucket,
                      deployment=DEPLOYMENT_DEFAULTS.replace(**deployment))


@needs_s3
def test_create_app_from_settings_stages_into_the_configured_bucket(monkeypatch):
    """With S3 configured, `create_app` composes `S3ObjectStore` on that bucket and prefix,
    and the bytes an upload puts land there - not in process memory. `dev`, because the
    database here is unreachable and `pilot` refuses to start without it (G2). The upload
    tickets are M5's durable authority on that database, so F2C's reference one is injected
    (`lifecycle`, wiring request 1) - the case is about where the bytes land."""
    import psycopg
    from infrx.contracts.fakes.lifecycle import FakeLifecycle
    from infrx.contracts.fakes.support import FakeClock, SequentialIds

    async def no_database(*args, **kw):
        raise psycopg.OperationalError("no database in this case")

    monkeypatch.setattr(psycopg.AsyncConnection, "connect", no_database)
    s3_store(monkeypatch)                              # the bucket exists
    prefix = unique_prefix()
    media = cutover_app(settings("dev", BUCKET, s3_media_prefix=prefix,
                                 s3_endpoint_url=ENDPOINT),
                        lifecycle=FakeLifecycle(None, FakeClock(), SequentialIds())
                        ).state.runtime.media_store
    assert isinstance(media.objects, S3ObjectStore)
    _WRITTEN.append(S3ObjectStore(media.objects.client, BUCKET, prefix))   # the case's prefix
    assert (media.objects.bucket, media.objects.prefix) == (BUCKET, prefix)
    handle = run(media.create_upload(b.ORG_A, {}))["upload_handle"]
    run(media.put_upload(b.ORG_A, handle, CLIP, "video/mp4"))
    raw = media.objects.client.head_object(Bucket=BUCKET,
                                           Key=prefix + media.upload_key(b.ORG_A, handle))
    assert raw["ContentLength"] == len(CLIP)


@pytest.mark.parametrize("case", ["unset", "unreachable", pytest.param("missing", marks=needs_s3)])
def test_create_app_refuses_to_start_when_the_bucket_does_not_answer(case, monkeypatch):
    """Fail closed, before anything is served: no bucket, an endpoint nobody answers at,
    or a bucket that does not exist. The refusal names S3_MEDIA_BUCKET and the S3 error,
    never the bucket or where the store is."""
    s3_env(monkeypatch, local=case != "missing")
    bucket, endpoint = {"unset": ("", UNREACHABLE),
                        "unreachable": ("infrx-m1l2-pilot", UNREACHABLE),
                        "missing": (absent_bucket(), ENDPOINT)}[case]
    with pytest.raises(RuntimeMisconfigured) as refused:
        cutover_app(settings("pilot", bucket, s3_endpoint_url=endpoint))
    message = str(refused.value)
    assert "S3_MEDIA_BUCKET" in message
    assert "infrx-m1l2" not in message and "127.0.0.1" not in message


def fake_aws(tmp_path, name: str, exit_code: int, stderr: str = ""):
    """An `aws` that records its argv and answers with `exit_code`/`stderr`."""
    log = tmp_path / f"{name}.log"
    code = (f"import sys; open({str(log)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n'); "
            f"sys.stderr.write({stderr!r}); sys.exit({exit_code})")
    return (sys.executable, "-c", code), log


def test_a_pilot_install_asks_the_bucket_before_replacing_the_file(tmp_path):
    """preflight `apply` in pilot: no bucket is a refusal (nothing asked); a bucket is asked
    HeadBucket from the host, with the endpoint when one is set; a refusal names the setting
    and the S3 error code, never the bucket."""
    from ..i.support import preflight
    ok, asked = fake_aws(tmp_path, "ok", 0)
    cfg = preflight.Config(mode="pilot", env_file=tmp_path / "gateway.env", aws=ok)
    unset = preflight.bucket_problems(cfg, {})
    assert len(unset) == 1 and "S3_MEDIA_BUCKET" in unset[0] and not asked.exists()
    values = {"S3_MEDIA_BUCKET": "infrx-media-pilot"}
    assert preflight.bucket_problems(cfg, values) == []
    assert asked.read_text().split() == ["s3api", "head-bucket", "--bucket", "infrx-media-pilot",
                                         "--region", "us-east-1"]
    preflight.bucket_problems(cfg, {**values, "S3_ENDPOINT_URL": "http://127.0.0.1:55500"})
    assert asked.read_text().splitlines()[-1].endswith("--endpoint-url http://127.0.0.1:55500")
    denied, _ = fake_aws(tmp_path, "denied", 254,
                         "An error occurred (403) when calling the HeadBucket operation: "
                         "Forbidden")
    cfg.aws = denied
    refused = preflight.bucket_problems(cfg, values)
    assert len(refused) == 1 and "(403)" in refused[0] and "S3_MEDIA_BUCKET" in refused[0]
    assert "infrx-media-pilot" not in refused[0]


def test_a_pilot_install_without_a_bucket_is_refused(tmp_path, monkeypatch, capsys):
    from ..i import support as installer
    installer.stubs(tmp_path, monkeypatch)
    cfg = installer.config(tmp_path, mode="pilot")
    before = cfg.env_file.read_bytes()
    assert installer.preflight.apply(cfg) == installer.preflight.REFUSED
    assert "S3_MEDIA_BUCKET: not set" in capsys.readouterr().err
    assert cfg.env_file.read_bytes() == before


def test_the_pilot_runtime_probe_refuses_an_image_without_botocore(tmp_path, monkeypatch):
    from ..i.support import preflight
    staged = tmp_path / "staged.env"
    staged.write_text("INFRX_MODE=pilot\n")
    monkeypatch.setattr(preflight, "_importable", lambda module: module != "botocore")
    assert any("botocore" in problem for problem in preflight.probe(staged, "pilot")["problems"])
    monkeypatch.setattr(preflight, "_importable", lambda module: True)
    assert not any("botocore" in problem
                   for problem in preflight.probe(staged, "pilot")["problems"])


# --- the error-vs-absent rule, every arm (review A2) -------------------------------------
def stubbed():
    """An `S3ObjectStore` whose client answers from a script (botocore's Stubber): no
    socket, no credentials of anyone's."""
    import botocore.session
    from botocore.stub import Stubber
    client = botocore.session.get_session().create_client(
        "s3", region_name="us-east-1", aws_access_key_id="local", aws_secret_access_key="local")
    stub = Stubber(client)
    stub.activate()
    return S3ObjectStore(client, "infrx-m1l2", "test/m1l2/"), stub


def test_a_conflict_or_a_broken_body_is_an_error_and_a_404_is_absent():
    """A 409 ConditionalRequestConflict (a concurrent conditional write) is neither
    "written" nor "occupied": a retryable dependency_unavailable. A body that breaks while
    it is read is an error, never a missing object. A 404 on a read is absent."""
    from botocore.response import StreamingBody
    objects, stub = stubbed()
    stub.add_client_error("put_object", "ConditionalRequestConflict", http_status_code=409)
    with pytest.raises(errors.DependencyUnavailable) as refused:
        run(objects.put_if_absent(SOURCE, b"x", "video/mp4"))
    assert refused.value.retry_after_s
    stub.add_response("get_object", {"Body": StreamingBody(io.BytesIO(b"three"), 64)})
    with pytest.raises(errors.DependencyUnavailable):
        run(objects.get(SOURCE))
    stub.add_client_error("get_object", "NoSuchKey", http_status_code=404)
    assert run(objects.get(SOURCE)) is None
    stub.add_client_error("head_object", "404", http_status_code=404)
    assert run(objects.head(SOURCE)) is None
    stub.assert_no_pending_responses()


def test_only_a_whole_object_sha256_is_a_digest():
    """Review A7: HeadObject's checksum is the object's digest only when it is 32 bytes of
    strict base64 of the whole object. A composite checksum (a multipart upload's, marked
    COMPOSITE or written `...=-N`), a short one, or none is NO_DIGEST - present, equal to no
    digest - never a value that merely decodes."""
    import base64 as b64
    import hashlib
    whole = b64.b64encode(hashlib.sha256(b"x").digest()).decode()
    objects, stub = stubbed()
    answers = (({"ChecksumSHA256": whole, "ChecksumType": "FULL_OBJECT"}, digest_of(b"x")),
               ({"ChecksumSHA256": whole}, digest_of(b"x")),
               ({"ChecksumSHA256": whole + "-3"}, NO_DIGEST),
               ({"ChecksumSHA256": whole, "ChecksumType": "COMPOSITE"}, NO_DIGEST),
               ({"ChecksumSHA256": b64.b64encode(b"sixteen bytes!!!").decode()}, NO_DIGEST),
               ({}, NO_DIGEST))
    for response, _ in answers:
        stub.add_response("head_object", response)
    assert [run(objects.head(SOURCE)) for _ in answers] == [digest for _, digest in answers]
    stub.assert_no_pending_responses()


def test_a_404_on_a_write_or_a_listing_is_an_error_not_absence():
    """Review A3: "absent" is an answer only a one-object read (and delete) has. A write
    answered 404 was not written and did not find anything there; a listing answered 404
    listed nothing - both are dependency_unavailable (the collector would otherwise iterate
    over None, and `_write_once` would call a fault a content conflict)."""
    objects, stub = stubbed()
    stub.add_client_error("put_object", "404", http_status_code=404)
    stub.add_client_error("list_objects_v2", "NoSuchKey", http_status_code=404)
    for call in (objects.put_if_absent(SOURCE, b"x", "video/mp4"), objects.keys("media/")):
        with pytest.raises(errors.DependencyUnavailable):
            run(call)
    stub.add_client_error("delete_object", "NoSuchKey", http_status_code=404)
    assert run(objects.delete(SOURCE)) is None
    stub.assert_no_pending_responses()


@needs_s3
def test_a_store_on_a_missing_bucket_reads_writes_and_lists_nothing(monkeypatch):
    """Limit 3, pinned: HeadObject's 404 has no body, so `head`/`describe` cannot tell a
    missing bucket from a missing key and answer None; every call whose error has a body -
    get, put, list, delete - names NoSuchBucket and is dependency_unavailable."""
    s3_env(monkeypatch)
    objects = S3ObjectStore.connect(absent_bucket(), unique_prefix(), ENDPOINT)
    assert run(objects.head(SOURCE)) is None and run(objects.describe(SOURCE)) is None
    for call in (objects.get(SOURCE), objects.put_if_absent(SOURCE, b"x", "video/mp4"),
                 objects.keys("media/"), objects.delete(SOURCE)):
        with pytest.raises(errors.DependencyUnavailable):
            run(call)


# --- how long a call may take (review A4) ------------------------------------------------
def test_a_failing_call_is_tried_twice_and_no_more(monkeypatch):
    """A retryable failure (503) is retried once: two attempts in all, so a store that
    accepts and never answers costs about a minute per call (2 x 30 s), startup included -
    not the four attempts botocore's `max_attempts: 3` meant."""
    import http.server
    import threading

    seen = []

    class Unavailable(http.server.BaseHTTPRequestHandler):
        def _answer(self):
            seen.append(self.command)
            self.send_response(503)
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_HEAD = do_GET = do_PUT = _answer

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Unavailable)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        s3_env(monkeypatch, local=True)
        objects = S3ObjectStore.connect("infrx-m1l2", "test/m1l2/",
                                        f"http://127.0.0.1:{server.server_address[1]}")
        with pytest.raises(errors.DependencyUnavailable):
            run(objects.head(SOURCE))
    finally:
        server.shutdown()
    assert seen == ["HEAD", "HEAD"]


def test_a_pilot_install_waits_for_the_bucket_a_bounded_time(tmp_path, monkeypatch):
    from ..i.support import preflight
    monkeypatch.setattr(preflight, "BUCKET_PROBE_TIMEOUT_S", 0.5)
    slow = (sys.executable, "-c", "import time; time.sleep(3)")
    cfg = preflight.Config(mode="pilot", env_file=tmp_path / "gateway.env", aws=slow)
    refused = preflight.bucket_problems(cfg, {"S3_MEDIA_BUCKET": "infrx-media-pilot",
                                              "S3_ENDPOINT_URL": "http://endpoint-host.example:9000"})
    assert len(refused) == 1 and "did not answer within 0.5 s" in refused[0]
    # the setting and the bound, never the bucket or where the store is (verifier V4)
    assert "infrx-media-pilot" not in refused[0] and "endpoint-host" not in refused[0]


# --- the harness itself (review A1) -------------------------------------------------------
def test_the_s3_cases_keep_the_environments_credentials_unless_told_to_use_local_ones(
        monkeypatch):
    """The box run must reach AWS with the instance role: without INFRX_M_S3_LOCAL_CREDS
    nothing in the environment is replaced (no literal key, no metadata switch-off)."""
    monkeypatch.delenv(LOCAL_FLAG, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "ASIAFROMTHEINSTANCEROLE")
    monkeypatch.delenv("AWS_EC2_METADATA_DISABLED", raising=False)
    s3_env(monkeypatch)
    assert os.environ["AWS_ACCESS_KEY_ID"] == "ASIAFROMTHEINSTANCEROLE"
    assert "AWS_EC2_METADATA_DISABLED" not in os.environ
    monkeypatch.setenv(LOCAL_FLAG, "1")
    s3_env(monkeypatch)
    assert os.environ["AWS_ACCESS_KEY_ID"] == ACCESS_KEY
    assert os.environ["AWS_EC2_METADATA_DISABLED"] == "true"


class Untouchable:
    """A client no call may reach."""

    def __getattr__(self, name):
        raise AssertionError(f"the cleanup reached the bucket ({name}) for a refused prefix")


def test_the_cleanup_empties_only_one_cases_own_prefix():
    """Never the deployment's prefix, never the whole test area, never a nested path: the
    refusal comes before any call reaches the bucket."""
    for prefix in ("infrx/", "test/m1l2/", "test/m1l2/otherrun/",
                   f"test/m1l2/{'0' * 32}/nested/", f"test/{'0' * 32}/"):
        with pytest.raises(ValueError, match="refusing to empty"):
            remove_prefix(S3ObjectStore(Untouchable(), "infrx-m1l2", prefix))


def test_no_bucket_is_created_without_local_credentials(monkeypatch):
    """On the box the bucket is the project's: without INFRX_M_S3_LOCAL_CREDS the harness
    sends no CreateBucket (a Stubber with nothing queued refuses any call); with it, exactly
    one (verifier V3)."""
    from botocore.exceptions import UnStubbedResponseError
    module = sys.modules[__name__]
    for flag in ("", "1"):
        objects, stub = stubbed()
        monkeypatch.setattr(S3ObjectStore, "connect", classmethod(
            lambda cls, bucket, prefix, endpoint_url="": S3ObjectStore(objects.client, bucket,
                                                                      prefix)))
        monkeypatch.setattr(module, "_READY", set())
        monkeypatch.setenv(LOCAL_FLAG, flag)
        if flag:
            stub.add_response("create_bucket", {}, {"Bucket": BUCKET})
        try:
            s3_store(monkeypatch)
        except UnStubbedResponseError:
            pytest.fail("a CreateBucket was sent without INFRX_M_S3_LOCAL_CREDS")
        finally:
            _WRITTEN[:] = [kept for kept in _WRITTEN if kept.client is not objects.client]
        stub.assert_no_pending_responses()


@needs_s3
def test_what_a_case_writes_is_removed_after_it(monkeypatch):
    """Every store `s3_store` makes is registered for the teardown, and the teardown (run
    here as the fixture runs it) leaves nothing under its prefix (verifier V1)."""
    objects = s3_store(monkeypatch)
    assert objects in _WRITTEN
    run(objects.put_if_absent(SOURCE, b"x", "video/mp4"))
    empty_what_was_written()
    assert _WRITTEN == [] and run(objects.keys("")) == []
