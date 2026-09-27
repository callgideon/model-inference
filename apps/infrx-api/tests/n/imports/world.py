"""The N lanes' fake world: D7's catalog rules in memory, an object store that can crash,
and the fixture specs. `test_import_pg.py` / `test_versions_pg.py` run the same scenarios on
the real `PgLabDataStore` (0029).

`FakeLabStore` keeps 0029's rules the importer relies on: a source registers only under a
current grant to its own provider, and the same source id is one digest for ever; a record
is content-addressed (F3's `FakeLabCatalog`), one set of bytes per (object, version); a
sample cites a registered source and that source's grant; resolve is provider-scoped;
`accessible_samples` reads the grant NOW, for the purpose.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import pathlib

from infrx.contracts import errors
from infrx.contracts.lab import records
from infrx.contracts.lab.fakes import FakeLabCatalog
from infrx.media.store import InMemoryObjectStore

NEMO = "b0000001-0000-4000-8000-000000000001"
OTHER = "b0000009-0000-4000-8000-000000000009"
GRANT_ID = "90000000-0000-4000-8000-000000000001"
FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def run(coro):
    return asyncio.run(coro)


def grant_ref(grant_id: str = GRANT_ID, provider: str = NEMO, version: int = 1) -> str:
    digest = hashlib.sha256(records.canonical(
        {"grant_id": grant_id, "version": version})).hexdigest()
    return f"lab:grant:{provider}:{grant_id}@sha256:{digest}"


def fixture(name: str) -> tuple[dict, bytes]:
    """(the import spec, the JSONL bytes) of a provider pipeline export example."""
    return (json.loads((FIXTURES / f"{name}.spec.json").read_text()),
            (FIXTURES / f"{name}.jsonl").read_bytes())


async def chunks(data: bytes, size: int = 7):
    """The body as a client streams it: small pieces that split lines anywhere."""
    for i in range(0, len(data), size):
        yield data[i:i + size]


class FakeLabStore:
    def __init__(self) -> None:
        self.catalog = FakeLabCatalog()
        self.grants: dict[str, dict] = {}
        self.sources: dict[str, tuple[str, str]] = {}     # source id -> (ref, grant ref)
        self.versions: dict[tuple[str, int], str] = {}
        self.published: list[str] = []

    def add_grant(self, grant_id: str = GRANT_ID, provider: str = NEMO,
                  purposes=("provider_sharing", "training")) -> str:
        ref = grant_ref(grant_id, provider)
        self.grants[ref] = {"provider": provider, "purposes": set(purposes), "current": True}
        return ref

    def revoke(self, ref: str) -> None:
        self.grants[ref]["current"] = False

    async def register_source(self, *, provider_org_id, source_id, content_digest, grant_ref,
                              actor) -> str:
        grant = self.grants.get(grant_ref)
        if grant is None or grant["provider"] != provider_org_id:
            raise errors.NotFound("no such grant for this provider")
        if not grant["current"]:
            raise errors.Forbidden("the grant is revoked or expired")
        ref = f"lab:source:{provider_org_id}:{source_id}@sha256:{content_digest[7:]}"
        if self.sources.setdefault(source_id, (ref, grant_ref)) != (ref, grant_ref):
            raise errors.StateConflict(f"source {source_id} names other content")
        return ref

    async def publish(self, payload, *, provider_org_id, actor) -> str:
        record = records.parse(payload)
        if record.provider_org_id != provider_org_id:
            raise errors.Forbidden("a provider publishes only its own records")
        bound = dict(self.sources.values())
        for sample in record.samples:
            if bound.get(sample.source_ref) != sample.grant_ref:
                raise errors.NotFound(f"unbound sample {sample.sample_id}")
        for parent in record.parent_refs:
            self.catalog.resolve(parent, provider_org_id=provider_org_id)
        ref = records.ref_of(payload)
        key = (record.dataset_id, record.version)
        if self.versions.setdefault(key, ref) != ref:
            raise errors.StateConflict("this version is already other bytes")
        if ref not in self.published:
            self.published.append(ref)
        return self.catalog.publish(payload)

    async def resolve(self, ref, *, provider_org_id):
        return self.catalog.resolve(ref, provider_org_id=provider_org_id)

    async def accessible_samples(self, dataset_ref, *, provider_org_id, purpose) -> list[str]:
        manifest = self.catalog.resolve(dataset_ref, provider_org_id=provider_org_id)
        live = {ref for ref, g in self.grants.items() if g["current"] and purpose in g["purposes"]}
        return sorted(s.sample_id for s in manifest.samples if s.grant_ref in live)


class Crash(BaseException):
    """A process dying: not an error anything handles."""


class CrashingObjects(InMemoryObjectStore):
    """An object store whose process dies after `puts` writes (then stays up for a resume
    that shares its objects)."""

    def __init__(self, puts: int) -> None:
        super().__init__()
        self.left = puts
        self.reads: list[str] = []
        self.writes: list[str] = []

    async def put_if_absent(self, key, data, content_type):
        self.writes.append(key)
        if self.left == 0:
            raise Crash(key)
        self.left -= 1
        return await super().put_if_absent(key, data, content_type)

    async def get(self, key):
        self.reads.append(key)
        return await super().get(key)
