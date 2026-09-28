"""The Lab's datasets (track N): provider imports (N1, `imports`), reproducible versions,
splits and exports (N2, `versions`), and trace-derived datasets with lineage and revocation
tombstones (N3, `lineage`). Everything publishes through D7's store
(`infrx.state.lab_data.PgLabDataStore`) and keeps content in the M object port; nothing here
is composed into the launched App or API yet (composition is coordinator wiring).

Every entry point takes a server-derived `provider_org_id`: the one its caller got from
`acting_provider` (WR-N-2), never a request field."""
from __future__ import annotations

from ..contracts import errors
from ..contracts.v2.records import ROLE_CAPABILITIES, ProviderCapability

#: Dataset work reads and writes provider content, so it needs developer or above - the
#: membership half of R160's gates (the grant half is D7's, read at every gate).
ACTS = ProviderCapability.manage_dev_deployment


async def acting_provider(access, user_id: str, provider_org_id: str) -> str:
    """WR-N-2: `provider_org_id` when the L2 port (`LabAccess.workspaces`, R156's one
    membership read, current on the store clock) says `user_id` is a current developer+
    member of it. Anything else is refused before the caller touches storage: no current
    membership of that provider is `NotFound` (a forged or foreign workspace id confirms
    nothing), a role without `ACTS` is `Forbidden`."""
    for workspace in await access.workspaces(user_id):
        membership = workspace.membership
        if membership.provider_org_id == provider_org_id:
            if ACTS not in ROLE_CAPABILITIES[membership.role]:
                raise errors.Forbidden(f"this provider role does not hold {ACTS}")
            return provider_org_id
    raise errors.NotFound("no such provider workspace")
