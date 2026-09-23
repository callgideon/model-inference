#!/usr/bin/env python3
"""M1-L2: `S3ObjectStore` (infrx/media/s3.py) - the `ObjectStore` port's conformance, run
against `InMemoryObjectStore` and a real S3-compatible store, plus the settings that
place it and the startup that refuses without it.

    uv run --frozen pytest -q tests/m/test_s3.py            # memory + the no-Docker cases
    INFRX_M_S3_ENDPOINT=http://127.0.0.1:55500 uv run --frozen pytest -q tests/m/test_s3.py

The S3 half needs the E2 stack's `s3` service (MinIO, tests/integration/compose.yaml) or
another S3-compatible endpoint taking the E2 literals as credentials. Without
`INFRX_M_S3_ENDPOINT` it skips, naming its owner. Each S3 case writes under a prefix of
its own, `test/m1l2/<uuid>/`, in `INFRX_M_S3_BUCKET` (default `infrx-m1l2`, created if
absent). Nothing reaches AWS: every case replaces the environment's AWS variables with
the local literals and turns the instance-metadata lookup off.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from infrx import config
from infrx.config import DEPLOYMENT_DEFAULTS, RuntimeMisconfigured
from infrx.contracts import errors
from infrx.media.s3 import S3ObjectStore

ENDPOINT = os.environ.get("INFRX_M_S3_ENDPOINT", "")
BUCKET = os.environ.get("INFRX_M_S3_BUCKET", "infrx-m1l2")
# The E2 stack's MinIO literals (tests/integration/harness.py): local test strings.
ACCESS_KEY, SECRET_KEY = "infrxe2minio", "infrx-e2-local-secret"
# Nothing listens on the discard port: a store there never answers.
UNREACHABLE = "http://127.0.0.1:9"
needs_s3 = pytest.mark.skipif(
    not ENDPOINT, reason="M1-L2 (owner: M): no S3-compatible endpoint - start the E2 "
                         "stack's s3 service and export INFRX_M_S3_ENDPOINT")


def run(coroutine):
    return asyncio.run(coroutine)


def s3_env(monkeypatch, secret: str = SECRET_KEY) -> None:
    """The local literals as the only credentials botocore can find."""
    for name in ("AWS_SESSION_TOKEN", "AWS_PROFILE", "AWS_ENDPOINT_URL", "AWS_ENDPOINT_URL_S3"):
        monkeypatch.delenv(name, raising=False)
    for name, value in (("AWS_ACCESS_KEY_ID", ACCESS_KEY), ("AWS_SECRET_ACCESS_KEY", secret),
                        ("AWS_DEFAULT_REGION", "us-east-1"), ("AWS_EC2_METADATA_DISABLED", "true"),
                        ("AWS_CONFIG_FILE", os.devnull),
                        ("AWS_SHARED_CREDENTIALS_FILE", os.devnull)):
        monkeypatch.setenv(name, value)


def unique_prefix() -> str:
    return f"test/m1l2/{uuid.uuid4().hex}/"


# --- the settings that place the store (08 §5.1) ----------------------------------------
BAD_PREFIXES = ("/infrx/", "infrx", "infrx//", "in frx/", "infrx/media")
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
            assert value not in str(refused.value) and "secret" not in str(refused.value)
    for prefix in ("infrx/", "test/m1l2/0a1b/", "a.b-c_d/"):
        config.validate_deployment(DEPLOYMENT_DEFAULTS.replace(s3_media_prefix=prefix), "pilot")
    for endpoint in ("", "http://127.0.0.1:55500", "https://s3.us-east-1.amazonaws.com",
                     "http://minio:9000/"):
        config.validate_deployment(DEPLOYMENT_DEFAULTS.replace(s3_endpoint_url=endpoint), "dev")


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
    s3_env(monkeypatch)
    objects = S3ObjectStore.connect("infrx-m1l2", "test/m1l2/", UNREACHABLE)
    with pytest.raises(errors.DependencyUnavailable):
        run(objects.head("media/k"))
