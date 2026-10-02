"""AP-04: the artifact rows (0061), their store, the operation protocol and the object port.

Rows: one closed record per 0061 table, columns = fields. `ArtifactStore` is the generic
row store both implementations answer the same way: `insert` is idempotent on the row's id
(the stored row comes back; another unique key taken is `Conflict`), `get` is scoped to the
provider (another provider's row is absent), `update` is a compare-and-set on `state`.
`FakeArtifactStore` keeps dicts; `PgArtifactStore` runs the same statements on 0061.

`ControlOps` is api-schema's 0060 protocol as wave-7's plan publishes it (start / lease /
advance / finish / cancel / get); `compose.DurableOps` implements it over 0060's
`ControlOps` (`PgControlOps`, or `FakeControlOps` in tests).

`Objects` is the byte port: `S3Objects` reuses `infrx.media.s3.S3ObjectStore` (its client,
prefix discipline and typed failures) and adds what artifacts need beyond media - a
presigned PUT, a streamed digest and a file upload; `MemoryObjects` is its fake.
"""
from __future__ import annotations

import dataclasses
import hashlib
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, ClassVar, Literal, Protocol, TypeVar

from pydantic import Field

from ...contracts import api, errors
from ...contracts.api import Wire
from .manifest import Card, FileEntry
from .verify import Compatibility

SHA256 = r"^sha256:[0-9a-f]{64}$"


# =========================================================================== rows ===
class Row(Wire):
    TABLE: ClassVar[str]
    KEY: ClassVar[str]
    provider_org_id: str
    created_by: str
    created_at: datetime

    @property
    def key(self) -> str:
        return getattr(self, self.KEY)


class Project(Row):
    TABLE, KEY = "model_projects", "project_id"
    project_id: str
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(max_length=4000)
    model_uuid: str | None = None
    public_model_id: str | None = None
    request_hash: str = Field(pattern=SHA256)


class Artifact(Row):
    TABLE, KEY = "artifacts", "artifact_id"
    artifact_id: str
    project_id: str
    source: Literal["upload", "import", "adopted"]
    source_repo: str | None = None
    source_commit: str | None = None
    files: tuple[FileEntry, ...]
    manifest_sha256: str = Field(pattern=SHA256)
    card: Card
    provenance: dict[str, Any]
    compatibility: Compatibility
    verified_at: datetime


class Upload(Row):
    TABLE, KEY = "artifact_uploads", "upload_id"
    upload_id: str
    project_id: str
    files: tuple[FileEntry, ...]
    manifest_sha256: str = Field(pattern=SHA256)
    card: Card
    state: Literal["open", "verifying", "verified", "failed", "expired"]
    expires_at: datetime
    received: tuple[str, ...] = ()
    operation_id: str | None = None
    artifact_id: str | None = None
    request_hash: str = Field(pattern=SHA256)
    updated_at: datetime


class Import(Row):
    TABLE, KEY = "artifact_imports", "import_id"
    import_id: str
    project_id: str
    source_host: str
    source_repo: str
    source_commit: str
    secret_ref: str | None = None
    files: tuple[FileEntry, ...]
    manifest_sha256: str = Field(pattern=SHA256)
    card: Card
    state: Literal["queued", "verified", "failed"]
    operation_id: str
    artifact_id: str | None = None
    request_hash: str = Field(pattern=SHA256)
    updated_at: datetime


class Revision(Row):
    TABLE, KEY = "model_project_revisions", "serving_version_id"
    serving_version_id: str
    project_id: str
    artifact_id: str
    profile: dict[str, Any]
    request_hash: str = Field(pattern=SHA256)


R = TypeVar("R", bound=Row)
JSON_FIELDS = frozenset({"files", "card", "provenance", "compatibility", "received", "profile"})
MUTABLE = ("state", "received", "operation_id", "artifact_id", "updated_at")


def input_hash(body: Any) -> str:
    """The canonical input hash an idempotent create stores and compares (R270)."""
    from ...contracts.lab.records import canonical
    return "sha256:" + hashlib.sha256(canonical(body)).hexdigest()


class ArtifactStore(Protocol):
    async def db_now(self) -> datetime: ...
    async def insert(self, row: R) -> R: ...
    async def get(self, kind: type[R], provider_org_id: str, key: str) -> R | None: ...
    async def rows(self, kind: type[R], provider_org_id: str, **equal: str) -> list[R]: ...
    async def update(self, row: R, *, expected_state: str) -> R: ...
    async def pending(self, kind: type[R], states: tuple[str, ...]) -> list[R]: ...
    async def bind(self, project: Project) -> Project:
        """The project bound to its `public.models` row (0061 `model_project_bind`)."""
        ...


@dataclasses.dataclass
class FakeArtifactStore:
    """Dicts per table. `models` is the registry fake's `public.models` (FakeControl.models:
    model_uuid -> (public id, provider)), so a bind is visible to `LabControl.register`."""

    clock: Callable[[], Awaitable[datetime]]
    slugs: dict[str, str]                                    # provider -> provider slug
    models: dict[str, tuple[str, str]] = dataclasses.field(default_factory=dict)
    tables: dict[str, dict[str, Row]] = dataclasses.field(default_factory=dict)

    async def db_now(self) -> datetime:
        return await self.clock()

    def _table(self, kind: type[Row]) -> dict[str, Row]:
        return self.tables.setdefault(kind.TABLE, {})

    async def insert(self, row: R) -> R:
        table = self._table(type(row))
        if row.key in table:
            return table[row.key]                            # type: ignore[return-value]
        if isinstance(row, Project) and any(
                (p.provider_org_id, getattr(p, "slug")) == (row.provider_org_id, row.slug)
                for p in table.values()):
            raise errors.Conflict("model_projects_slug_key")
        table[row.key] = row
        return row

    async def get(self, kind: type[R], provider_org_id: str, key: str) -> R | None:
        row = self._table(kind).get(key)
        return row if row is not None and row.provider_org_id == provider_org_id else None  # type: ignore[return-value]

    async def rows(self, kind: type[R], provider_org_id: str, **equal: str) -> list[R]:
        found = [r for r in self._table(kind).values() if r.provider_org_id == provider_org_id
                 and all(getattr(r, k) == v for k, v in equal.items())]
        return sorted(found, key=lambda r: (r.created_at, r.key))  # type: ignore[return-value]

    async def update(self, row: R, *, expected_state: str) -> R:
        table = self._table(type(row))
        stored = table.get(row.key)
        if stored is None or getattr(stored, "state") != expected_state:
            raise errors.StateConflict(f"{row.TABLE} {row.key} is not {expected_state}")
        table[row.key] = row.model_copy(update={"updated_at": await self.db_now()})
        return table[row.key]                                # type: ignore[return-value]

    async def pending(self, kind: type[R], states: tuple[str, ...]) -> list[R]:
        return sorted((r for r in self._table(kind).values() if getattr(r, "state") in states),
                      key=lambda r: (r.created_at, r.key))  # type: ignore[return-value]

    async def bind(self, project: Project) -> Project:
        stored = await self.get(Project, project.provider_org_id, project.project_id)
        if stored is None:
            raise errors.NotFound("no such model project for this provider")
        if stored.model_uuid is not None:
            return stored
        public = f"{self.slugs[stored.provider_org_id]}/{stored.slug}"
        if any(alias == public for alias, _ in self.models.values()):
            raise errors.StateConflict(f"the public model id {public} is another model's")
        model_uuid = str(uuid.uuid4())
        self.models[model_uuid] = (public, stored.provider_org_id)
        bound = stored.model_copy(update={"model_uuid": model_uuid, "public_model_id": public})
        self._table(Project)[stored.project_id] = bound
        return bound


def _value(name: str, value: Any) -> Any:
    from psycopg.types.json import Jsonb
    return Jsonb(value) if name in JSON_FIELDS else value


def _refusal(failed: Exception) -> Exception:
    from psycopg import errors as pg

    from ...state.jobstore import domain_error
    if isinstance(failed, pg.UniqueViolation):
        return errors.Conflict(getattr(failed.diag, "constraint_name", None) or "unique")
    if isinstance(failed, pg.ForeignKeyViolation):
        return errors.NotFound("no such model project or artifact for this provider")
    if isinstance(failed, (pg.CheckViolation, pg.NotNullViolation)):
        return errors.InvalidRequest(getattr(failed.diag, "constraint_name", None) or "check")
    return domain_error(failed)


class PgArtifactStore:
    """0061's tables through the control login (`infrx_lab_control`: select/insert, update on
    the two intake tables) and `model_project_bind`; one connection per call (`rpc`)."""

    def __init__(self, connect) -> None:
        self._connect = connect

    async def _rows(self, sql: str, params: Any = ()) -> list[tuple]:
        from ...state import rpc
        return await rpc.rows(self._connect, sql, params, error=_refusal)

    @staticmethod
    def _load(kind: type[R], row: tuple) -> R:
        doc = {name: (str(v) if isinstance(v, uuid.UUID) else v)
               for name, v in zip(kind.model_fields, row)}
        return kind.model_validate(doc)

    @staticmethod
    def _select(kind: type[Row]) -> str:
        return f"select {', '.join(kind.model_fields)} from infrx.{kind.TABLE}"

    async def db_now(self) -> datetime:
        return (await self._rows("select infrx.now()"))[0][0]

    async def insert(self, row: R) -> R:
        kind, doc = type(row), row.model_dump(mode="json")
        columns = [c for c in kind.model_fields if doc[c] is not None]
        await self._rows(
            f"insert into infrx.{kind.TABLE} ({', '.join(columns)}) values "
            f"({', '.join(['%s'] * len(columns))}) on conflict ({kind.KEY}) do nothing "
            "returning 1",
            [_value(c, doc[c]) for c in columns])
        found = await self._rows(f"{self._select(kind)} where {kind.KEY} = %s", (row.key,))
        return self._load(kind, found[0])

    async def get(self, kind: type[R], provider_org_id: str, key: str) -> R | None:
        try:
            uuid.UUID(key)
        except ValueError:
            return None
        found = await self._rows(f"{self._select(kind)} where {kind.KEY} = %s "
                                 "and provider_org_id = %s", (key, provider_org_id))
        return self._load(kind, found[0]) if found else None

    async def rows(self, kind: type[R], provider_org_id: str, **equal: str) -> list[R]:
        where = "".join(f" and {name} = %s" for name in equal)
        found = await self._rows(f"{self._select(kind)} where provider_org_id = %s{where} "
                                 f"order by created_at, {kind.KEY}",
                                 (provider_org_id, *equal.values()))
        return [self._load(kind, r) for r in found]

    async def update(self, row: R, *, expected_state: str) -> R:
        kind, doc = type(row), row.model_dump(mode="json")
        columns = [c for c in MUTABLE if c in kind.model_fields and c != "updated_at"]
        found = await self._rows(
            f"update infrx.{kind.TABLE} set "
            f"{', '.join(f'{c} = %s' for c in columns)}, updated_at = infrx.now() "
            f"where {kind.KEY} = %s and state = %s returning {', '.join(kind.model_fields)}",
            [*(_value(c, doc[c]) for c in columns), row.key, expected_state])
        if not found:
            raise errors.StateConflict(f"{kind.TABLE} {row.key} is not {expected_state}")
        return self._load(kind, found[0])

    async def pending(self, kind: type[R], states: tuple[str, ...]) -> list[R]:
        found = await self._rows(f"{self._select(kind)} where state = any(%s) "
                                 f"order by created_at, {kind.KEY}", (list(states),))
        return [self._load(kind, r) for r in found]

    async def bind(self, project: Project) -> Project:
        from ...state import rpc
        await rpc.call(self._connect, "model_project_bind",
                       {"project_id": project.project_id,
                        "provider_org_id": project.provider_org_id}, error=_refusal)
        bound = await self.get(Project, project.provider_org_id, project.project_id)
        assert bound is not None
        return bound


# ===================================================================== operations ===
@dataclasses.dataclass
class Operation:
    """One control operation as the store holds it: the wire document plus who started it
    (the provider scope a reader must share) and the lease."""

    doc: api.OperationDoc
    actor: api.Actor
    resource_kind: str
    scope: str
    key: str
    input_hash: str
    fence: int = 0
    lease_owner: str | None = None
    lease_until: datetime | None = None


class ControlOps(Protocol):
    async def start(self, *, kind: str, resource_kind: str, resource_id: str,
                    actor: api.Actor, scope: str, key: str,
                    input_hash: str) -> tuple[Operation, bool]:
        """(the operation, replayed). Same scope+key+hash replays; another hash is
        `IdempotencyConflict`."""
        ...
    async def get(self, operation_id: str) -> Operation | None: ...
    async def lease(self, operation_id: str, owner: str, ttl_s: int) -> int | None:
        """A new fence when the operation is not terminal and no live lease holds it."""
    async def advance(self, operation_id: str, fence: int, phase: str) -> Operation: ...
    async def finish(self, operation_id: str, fence: int, state: str,
                     error: api.ErrorBody | None = None) -> Operation: ...
    async def cancel(self, operation_id: str) -> Operation: ...


# ======================================================================== objects ===
class Objects(Protocol):
    async def presign_put(self, key: str, *, expires_s: int) -> str: ...
    async def keys(self, prefix: str) -> list[str]: ...
    async def digest(self, key: str) -> tuple[int, str] | None:
        """(bytes, `sha256:<hex>`) of the stored object, hashed from its bytes, or None."""
    async def read(self, key: str, limit: int) -> bytes | None:
        """A small object's bytes; one larger than `limit` is `RequestTooLarge`."""
    async def put_file(self, key: str, path: str) -> None: ...
    async def delete(self, key: str) -> None: ...


@dataclasses.dataclass
class MemoryObjects:
    data: dict[str, bytes] = dataclasses.field(default_factory=dict)

    async def presign_put(self, key, *, expires_s):
        return f"memory://{key}?expires_s={expires_s}"

    async def keys(self, prefix):
        return sorted(k for k in self.data if k.startswith(prefix))

    async def digest(self, key):
        blob = self.data.get(key)
        return None if blob is None else (len(blob), "sha256:" + hashlib.sha256(blob).hexdigest())

    async def read(self, key, limit):
        blob = self.data.get(key)
        if blob is not None and len(blob) > limit:
            raise errors.RequestTooLarge(f"{key} exceeds {limit} bytes")
        return blob

    async def put_file(self, key, path):
        with open(path, "rb") as handle:
            self.data[key] = handle.read()

    async def delete(self, key):
        self.data.pop(key, None)


class S3Objects:
    """Artifact bytes on S3 (MinIO in tests) under the media store's prefix discipline."""

    def __init__(self, store) -> None:          # an `infrx.media.s3.S3ObjectStore`
        self.store = store

    def _key(self, key: str) -> str:
        return self.store.prefix + key

    async def presign_put(self, key, *, expires_s):
        return await self.store._s3(lambda: self.store.client.generate_presigned_url(
            "put_object", Params={"Bucket": self.store.bucket, "Key": self._key(key)},
            ExpiresIn=expires_s))

    async def keys(self, prefix):
        return await self.store.keys(prefix)

    def _digest(self, key: str) -> tuple[int, str]:
        body = self.store.client.get_object(Bucket=self.store.bucket, Key=self._key(key))["Body"]
        digest, size = hashlib.sha256(), 0
        for chunk in body.iter_chunks(1 << 20):
            digest.update(chunk)
            size += len(chunk)
        return size, "sha256:" + digest.hexdigest()

    async def digest(self, key):
        return await self.store._s3(self._digest, key, absent_ok=True)

    async def read(self, key, limit):
        found = await self.store.describe(key)
        if found is not None and found[0] > limit:
            raise errors.RequestTooLarge(f"{key} exceeds {limit} bytes")
        return None if found is None else await self.store.get(key)

    def _put_file(self, key: str, path: str) -> None:
        with open(path, "rb") as handle:
            self.store.client.put_object(Bucket=self.store.bucket, Key=self._key(key),
                                         Body=handle)

    async def put_file(self, key, path):
        await self.store._s3(self._put_file, key, path)

    async def delete(self, key):
        await self.store.delete(key)
