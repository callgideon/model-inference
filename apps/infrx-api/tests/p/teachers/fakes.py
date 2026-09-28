"""P2's fakes: an N2 dataset store (the two reads P2 makes: `resolve`, `accessible_samples`),
P1's `import_labels` (codex/w5-pipelines, not on the base) and a teacher provider that can
revoke a grant the moment a chunk leaves. The D6J ledger and the provider are J2's fakes.
"""
from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

from infrx.contracts import errors
from infrx.datasets.imports import sample_key  # noqa: F401 - the tests read it here
from infrx.media.store import InMemoryObjectStore

from tests.j.submit import fakes as j2

PURPOSES = ("external_judging", "training")
DATASET_REF = f"lab:dataset_manifest:{'a' * 8}-0000-4000-8000-{'a' * 12}:" \
              f"{'d' * 8}-0000-4000-8000-{'d' * 12}@sha256:{'e' * 64}"
RUBRIC_REF = f"lab:rubric:{'a' * 8}-0000-4000-8000-{'a' * 12}:" \
             f"{'f' * 8}-0000-4000-8000-{'f' * 12}@sha256:{'0' * 64}"


def sid(n: int) -> str:
    return str(uuid.UUID(int=0x5a << 64 | n, version=4))


class Store:
    """`resolve` and `accessible_samples` of the D7/N2 store for one provider's dataset."""

    def __init__(self, provider_org_id: str, splits: dict[str, list[str]]) -> None:
        self.provider = provider_org_id
        self.splits = splits
        every = [s for ids in splits.values() for s in ids]
        self.allowed = {p: set(every) for p in PURPOSES}
        self.reads, self.revoke_at = 0, {}          # read number -> ids revoked before it
        self.samples = [SimpleNamespace(sample_id=s, content_digest="sha256:" + s.replace("-", "")
                                        .ljust(64, "0")) for s in every]

    async def resolve(self, ref: str, *, provider_org_id: str):
        if ref != DATASET_REF or provider_org_id != self.provider:
            raise errors.NotFound("no such dataset version")
        return SimpleNamespace(samples=self.samples,
                               splits=SimpleNamespace(**{k: list(v) for k, v in
                                                         self.splits.items()}))

    async def accessible_samples(self, ref: str, *, provider_org_id: str, purpose: str):
        await self.resolve(ref, provider_org_id=provider_org_id)
        self.reads += 1
        if self.reads in self.revoke_at:
            self.revoke(*self.revoke_at[self.reads])
        return sorted(self.allowed[purpose])

    def revoke(self, *ids: str, purposes=PURPOSES) -> None:
        for purpose in purposes:
            self.allowed[purpose] -= set(ids)


async def objects_for(store: Store) -> InMemoryObjectStore:
    objects = InMemoryObjectStore()
    for sample in store.samples:
        body = {"content": f"q a@b.example {sample.sample_id}"}
        await objects.put_if_absent(sample_key(store.provider, sample.content_digest),
                                    json.dumps(body).encode(),
                                    "application/json")
    return objects


class Labels:
    """P1's `import_labels(store, log, *, provider_org_id, actor, dataset_ref, rubric_ref, rows)`:
    records every call; one ref per (dataset, row), as P1's content-addressed publish."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def __call__(self, store, log, *, provider_org_id, actor, dataset_ref, rubric_ref, rows):
        rows = list(rows)
        self.calls.append({"provider_org_id": provider_org_id, "actor": actor,
                           "dataset_ref": dataset_ref, "rubric_ref": rubric_ref, "rows": rows})
        return SimpleNamespace(accepted=[f"ref:{r['sample_id']}" for r in rows], rejected=[])


class TeacherLedger(j2.FakeJudgeLedger):
    """J2's ledger plus D8's per-item failure log (`PgTeacherLedger.record_failures/failures`,
    0042 `lab_teacher_failures`): append-only, one row per (run, sample, reason)."""

    def __init__(self, *args, **kw) -> None:
        super().__init__(*args, **kw)
        self.failure_log: list[tuple[str, str, str]] = []

    async def record_failures(self, run_id: str, failures) -> int:
        new = [(run_id, s, r) for s, r in dict.fromkeys(failures)
               if (run_id, s, r) not in self.failure_log]
        self.failure_log += new
        return len(new)

    async def failures(self, run_id: str) -> list[tuple[str, str]]:
        return [(s, r) for run, s, r in self.failure_log if run == run_id]


class Provider(j2.FakeProvider):
    """J2's provider; `on_submit` runs after a batch is accepted (e.g. a revocation)."""

    def __init__(self, mode: str = "ok", on_submit=None) -> None:
        super().__init__(mode)
        self.on_submit = on_submit

    async def submit(self, key: str, items: list[dict]) -> str:
        batch = await super().submit(key, items)
        if self.on_submit:
            self.on_submit(len(self.calls))
        return batch


def redact(text: str) -> str:
    """Stands in for N2's redaction helper (WR-P2-4): masks the one PII shape the fixtures carry."""
    return text.replace("a@b.example", "[redacted]")
