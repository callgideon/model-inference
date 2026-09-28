"""P1's fake world: N's `FakeLabStore` (0029's rules) publishing every Lab record kind, D8's
append-only label log in memory (`FakeLabelLog`, the port lab-sql implements), and L2's
`FakeAccessStore` with one member of each kind. `test_annotations_pg.py` reruns the core on
the real `PgLabDataStore` and `PgAccessStore` (the log stays fake until D8 merges).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from infrx.contracts import errors
from infrx.contracts.lab import records
from infrx.contracts.v2 import records as v2
from infrx.datasets import lineage
from infrx.lab.access.fakes import FakeAccessStore
from infrx.media.store import InMemoryObjectStore

from ...n.imports.world import GRANT_ID, NEMO, FakeLabStore, grant_ref, run
from ...n.versions.test_versions import imported, uid

NOW = datetime(2026, 9, 27, 12, tzinfo=UTC)
GRANT_2 = "90000000-0000-4000-8000-000000000002"
GRANT_3 = "90000000-0000-4000-8000-000000000003"            # provider_sharing only
ADMIN, DEV, DEV2, VIEWER, GONE = (f"a{n:07x}-0000-4000-8000-00000000000{n}" for n in range(1, 6))
RUBRIC = f"lab:rubric:{NEMO}:{uid(1, 0xcb)}@sha256:{'c' * 64}"
RUBRIC_2 = f"lab:rubric:{NEMO}:{uid(1, 0xcb)}@sha256:{'d' * 64}"


class Store(FakeLabStore):
    """D7 for every record kind: one set of bytes per (kind, object id, version); every
    Lab ref it cites resolves for the provider."""

    async def publish(self, payload, *, provider_org_id, actor) -> str:
        if payload.get("schema") == "lab.dataset_manifest.1":
            return await super().publish(payload, provider_org_id=provider_org_id, actor=actor)
        record = records.parse(payload)
        if record.provider_org_id != provider_org_id:
            raise errors.Forbidden("a provider publishes only its own records")
        for ref in records._refs(payload):
            kind = records.REF_RE.fullmatch(ref).group(1)
            if kind == "source":
                if ref not in dict(self.sources.values()):
                    raise errors.NotFound(f"no such source {ref}")
            elif kind not in ("rubric", "payer", "serving", "evaluator", "grant"):
                self.catalog.resolve(ref, provider_org_id=provider_org_id)
        kind, id_field = records.REFERABLE[record.schema_id]
        ref = records.ref_of(payload)
        if self.versions.setdefault((kind, payload[id_field]), ref) != ref:
            raise errors.StateConflict(f"{kind} {payload[id_field]} is already other bytes")
        return self.catalog.publish(payload)


class FakeLabelLog:
    """D8's label/review log: one event per (provider, key) for ever."""

    def __init__(self) -> None:
        self.rows: list[tuple[str, dict]] = []

    async def append(self, event, *, provider_org_id):
        for provider, stored in self.rows:
            if (provider, stored["key"]) == (provider_org_id, event["key"]):
                if stored != event:
                    raise errors.IdempotencyConflict(f"{event['key']} holds another event")
                return dict(stored)
        self.rows.append((provider_org_id, dict(event)))
        return dict(event)

    async def events(self, dataset_ref, *, provider_org_id):
        return [dict(e) for p, e in self.rows
                if p == provider_org_id and e["dataset_ref"] == dataset_ref]


def members() -> FakeAccessStore:
    access = FakeAccessStore(now=NOW)
    for user, role, revoked in ((ADMIN, "administrator", None), (DEV, "developer", None),
                                (DEV2, "developer", None), (VIEWER, "viewer", None),
                                (GONE, "developer", NOW - timedelta(hours=1))):
        access.memberships[(NEMO, user)] = v2.ProviderMembership(
            provider_org_id=NEMO, user_id=user, role=role, granted_by="ops",
            granted_at=NOW - timedelta(days=1), revoked_at=revoked)
    return access


def rows(n: int, prefix: str = "q", splits=("train",)) -> list[dict]:
    """`n` text rows, each its own group, splits declared round-robin."""
    return [{"q": f"{prefix} {i}?", "g": f"{prefix}-{i}", "split": splits[i % len(splits)]}
            for i in range(n)]


def world(items=None, grant: str = GRANT_ID):
    """(store, log, objects, access, dataset ref) over an N1 import of `items`."""
    store, objects = Store(), InMemoryObjectStore()
    store.add_grant()
    store.add_grant(GRANT_2)
    store.add_grant(GRANT_3, purposes=("provider_sharing",))
    ref = imported(store, objects, items or rows(6, splits=("train", "holdout", "validation")),
                   1, grant=grant, split=True)
    return store, FakeLabelLog(), objects, members(), ref


def manifest(store, ref):
    return run(store.resolve(ref, provider_org_id=NEMO))


def split_ids(store, ref) -> dict[str, list[str]]:
    m = manifest(store, ref)
    return {n: list(getattr(m.splits, n)) for n in ("train", "validation", "holdout")}


def tombstone_regranted(store, objects, *sample_ids, grant: str = GRANT_ID) -> None:
    """E7L i04's re-grant (0-E7L-1, R193): `grant` is revoked, N3 tombstones `sample_ids`
    (what `lineage.reconcile` writes), then the grantor grants again - D7 reads them again,
    N3's gate never does."""
    ref = grant_ref(grant)
    store.revoke(ref)
    for sid in sample_ids:
        run(lineage._stone(objects, NEMO, {"sample_id": sid, "grantor_org_id": NEMO,
                                           "request_id": sid}, "grant_not_current", NOW))
    store.grants[ref]["current"] = True
