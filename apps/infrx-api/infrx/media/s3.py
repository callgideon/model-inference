"""M1-L2: `S3ObjectStore` - the `ObjectStore` port over one S3 bucket (botocore).

The same answers as `InMemoryObjectStore`, one S3 call each:

* the port's keys live under `prefix` (`S3_MEDIA_PREFIX`): every call prefixes the key,
  and `keys()` strips it again, so what a listing returns is what `head`/`get`/`delete`
  take - and two stores with different prefixes on one bucket never see each other;
* `put_if_absent` is PutObject with `If-None-Match: *`: write-once is the server's atomic
  answer (412 = something is there), never a HEAD and then a PUT;
* `head` is the digest **S3 measured**: the PutObject carries `x-amz-checksum-sha256`,
  which the server checks against the bytes it received and HeadObject returns;
* absent is None, and only a 404 is absent. A denied, throttled or unreachable store
  raises `DependencyUnavailable`: a store that cannot answer never says "not there".

Credentials and region come from botocore's own chain (the environment, the instance
role) and never from settings text. botocore blocks, so each call runs in a worker
thread; it is imported on first use, so importing `infrx` never loads it.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib

from ..contracts import errors

#: The only answers that mean "no such object": HeadObject's bodiless 404, GetObject's code.
MISSING = ("404", "NoSuchKey")
#: `head` of an object stored without our checksum: present, and no digest's match.
NO_DIGEST = "sha256:"


def reason(failure: BaseException) -> str:
    """What failed, as an S3 error code or an exception class - never its message, which
    can carry the endpoint and, path-style, the bucket name."""
    response = getattr(failure, "response", None)
    code = response.get("Error", {}).get("Code") if isinstance(response, dict) else None
    return code or type(failure).__name__


class S3ObjectStore:
    def __init__(self, client, bucket: str, prefix: str = "") -> None:
        self.client, self.bucket, self.prefix = client, bucket, prefix

    @classmethod
    def connect(cls, bucket: str, prefix: str = "", endpoint_url: str = "") -> S3ObjectStore:
        """A client from the environment's credentials. `endpoint_url` is for an
        S3-compatible store (MinIO in tests), addressed path-style; unset is AWS S3."""
        import botocore.session
        from botocore.config import Config
        config = Config(connect_timeout=5, read_timeout=30,
                        retries={"mode": "standard", "max_attempts": 3},
                        s3={"addressing_style": "path"} if endpoint_url else None)
        client = botocore.session.get_session().create_client(
            "s3", endpoint_url=endpoint_url or None, config=config)
        return cls(client, bucket, prefix)

    def probe(self) -> None:
        """HeadBucket: the bucket exists and answers these credentials, or it raises."""
        self.client.head_bucket(Bucket=self.bucket)

    async def _s3(self, call, *args):
        """One blocking S3 call in a worker thread: its answer, None for a missing object,
        False for a failed precondition, `DependencyUnavailable` for anything else."""
        from botocore.exceptions import BotoCoreError, ClientError
        try:
            return await asyncio.to_thread(call, *args)
        except ClientError as failure:
            code = reason(failure)
            if code in MISSING:
                return None
            if code == "PreconditionFailed":        # only put_if_absent sends one
                return False
            # ponytail: 409 ConditionalRequestConflict (a concurrent conditional write to
            # the same key) is this retryable 503 too; the client's retry finds the 412.
            raise errors.DependencyUnavailable("the media object store did not answer") \
                from failure
        except BotoCoreError as failure:
            raise errors.DependencyUnavailable("the media object store did not answer") \
                from failure

    def _head_object(self, key: str):
        return self.client.head_object(Bucket=self.bucket, Key=self.prefix + key,
                                       ChecksumMode="ENABLED")

    async def head(self, key: str) -> str | None:
        head = await self._s3(self._head_object, key)
        if head is None:
            return None
        checksum = head.get("ChecksumSHA256")
        return "sha256:" + base64.b64decode(checksum).hex() if checksum else NO_DIGEST

    def _get(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=self.prefix + key)["Body"].read()

    async def get(self, key: str) -> bytes | None:
        return await self._s3(self._get, key)

    def _put(self, key: str, data: bytes, content_type: str) -> bool:
        self.client.put_object(
            Bucket=self.bucket, Key=self.prefix + key, Body=data, ContentType=content_type,
            IfNoneMatch="*",
            ChecksumSHA256=base64.b64encode(hashlib.sha256(data).digest()).decode())
        return True

    async def put_if_absent(self, key: str, data: bytes, content_type: str) -> bool:
        return await self._s3(self._put, key, bytes(data), content_type)

    async def describe(self, key: str) -> tuple[int, str] | None:
        head = await self._s3(self._head_object, key)
        return (head["ContentLength"], head.get("ContentType", "")) if head else None

    def _keys(self, prefix: str) -> list[str]:
        pages = self.client.get_paginator("list_objects_v2").paginate(
            Bucket=self.bucket, Prefix=self.prefix + prefix)
        return sorted(item["Key"][len(self.prefix):]
                      for page in pages for item in page.get("Contents", ()))

    async def keys(self, prefix: str) -> list[str]:
        return await self._s3(self._keys, prefix)

    def _delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=self.prefix + key)

    async def delete(self, key: str) -> None:
        await self._s3(self._delete, key)
