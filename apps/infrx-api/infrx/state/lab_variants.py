"""WR-C6-VARIANTS: R3's optimization-variant listing - the read behind `/lab/v1/optimizations`
(`ReleaseRecords.variants`): each of the provider's `lab.optimization_variant.1` records with
both serving refs, what changed, its newest `infrx.variant_comparison.1` (null: not compared;
`0055_lab_variants_requeue.sql`) and both revision identities R3 stored (`base`/`variant`,
null until stored; `0058_lab_variant_identities.sql`, WR-LW7-3a). Another provider's
variants never appear (R227); none is `[]`, never a 503. The Lab reads base and variant as
required keys that may be null: null is a legacy row ("identity not recorded"); an omitted key
is unavailable (R267, supersedes R252 (b)). The launched composition serves it through
`infrx.lab.compose.ReleaseRecords.variants` (WR-LW7-1)."""
from __future__ import annotations

from typing import Any

from .jobstore import Connect
from .lab_data import PgLabDataStore


class PgLabVariants:
    _call = PgLabDataStore._call

    def __init__(self, connect: Connect) -> None:
        self._connect = connect

    async def variants(self, provider_org_id: str) -> list[dict[str, Any]]:
        return await self._call("lab_optimization_variant_listing",
                                {"provider_org_id": provider_org_id})

    async def put_identities(self, variant_ref: str, *, base: dict[str, Any],
                             variant: dict[str, Any], provider_org_id: str,
                             actor: str) -> dict[str, Any]:
        """R3's two `Identity` dumps for the provider's variant, written once (0058)."""
        args = {"variant_ref": variant_ref, "base": base, "variant": variant,
                "provider_org_id": provider_org_id, "actor": actor}
        return await self._call("lab_put_variant_identities", args)
