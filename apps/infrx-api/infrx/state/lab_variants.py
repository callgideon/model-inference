"""WR-C6-VARIANTS: R3's optimization-variant listing (`0055_lab_variants_requeue.sql`) - the
read behind `/lab/v1/optimizations` (`ReleaseRecords.variants`): each of the provider's
`lab.optimization_variant.1` records with both serving refs, what changed, and its newest
`infrx.variant_comparison.1` (null: not compared). Another provider's variants never appear
(R227); none is `[]`, never a 503.

NOT yet port.ts's `Variant`: apps/lab's http.ts VARIANT decoder requires `base`/`variant`
Identity objects (engine, engineVersion, hardware, quantization, capabilities), which R3 never
stores, so a non-empty listing reads 'unavailable' in the Lab until WR-LW7-3 is ruled ((b) the
Lab reads them as nul(IDENTITY), or (a) R3 persists them and 0055's listing returns them).
The launched composition serves this only once WR-LW7-1 (pilot.ReleaseRecords.variants) lands."""
from __future__ import annotations

from typing import Any

from .jobstore import Connect
from .lab_data import PgLabDataStore


class PgLabVariants:
    _call = PgLabDataStore._call

    def __init__(self, connect: Connect) -> None:
        self._connect = connect

    async def variants(self, provider_org_id: str) -> list[dict[str, Any]]:
        return await self._call("lab_optimization_variants",
                                {"provider_org_id": provider_org_id})
