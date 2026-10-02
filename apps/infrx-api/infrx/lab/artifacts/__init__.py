"""AP-04: model projects and verified artifacts (research/plan/api-lifecycle/contracts.md §4).

`LabArtifacts` is what the two route modules (`routes/lab_model_projects.py`,
`routes/lab_artifacts.py`) take from `rt.lab_artifacts`; `ArtifactWorker.run_once` is the
out-of-request half (the verification and import operations, the expiry sweep): an HTTP
request only commits a session and an operation, it never hashes or fetches weights.
"""
from __future__ import annotations

import dataclasses
import uuid

from ..access import LabAccess
from ..control import LabControl
from .imports import HubSource, Imports
from .projects import Projects
from .store import ArtifactStore, ControlOps, Import, Objects, Upload
from .uploads import Uploads

LEASE_S = 600


@dataclasses.dataclass
class LabArtifacts:
    projects: Projects
    uploads: Uploads
    imports: Imports
    ops: ControlOps

    @classmethod
    def compose(cls, access: LabAccess, control: LabControl, store: ArtifactStore,
                ops: ControlOps, objects: Objects, source: HubSource | None = None,
                **imports) -> LabArtifacts:
        return cls(Projects(access, store, control), Uploads(access, store, ops, objects),
                   Imports(access, store, ops, objects, source, **imports), ops)


class ArtifactWorker:
    """One pass: sweep expired sessions, then lease and run every pending operation. An
    operation a live lease holds is skipped; a worker killed mid-hash leaves its lease to
    expire and the next pass re-runs it under a new fence (the old one's writes refused).
    ponytail: no attempt cap - a store that keeps failing keeps the operation `running`
    until it answers; add a deadline with 0060's reconciler."""

    def __init__(self, artifacts: LabArtifacts, owner: str | None = None,
                 lease_s: int = LEASE_S) -> None:
        self.a, self.owner, self.lease_s = artifacts, owner or str(uuid.uuid4()), lease_s

    async def run_once(self) -> int:
        store = self.a.uploads.store
        done = await self.a.uploads.expire()
        for upload in await store.pending(Upload, ("verifying",)):
            fence = await self.a.ops.lease(upload.operation_id or "", self.owner, self.lease_s)
            if fence is not None:
                await self.a.uploads.verify(upload, fence)
                done += 1
        for row in await store.pending(Import, ("queued",)):
            fence = await self.a.ops.lease(row.operation_id, self.owner, self.lease_s)
            if fence is not None:
                await self.a.imports.run(row, fence)
                done += 1
        return done


__all__ = ["ArtifactWorker", "LabArtifacts"]
