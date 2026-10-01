"""WR-B3-2: `POST /lab/v1/checkpoints`, where an external training loop posts a checkpoint event.

    POST /lab/v1/checkpoints   <raw body>   X-Infrx-Signature: sha256=<hmac>   -> 202 receipt

The raw body and the signature go to B3's `receive` untouched (the HMAC is over the exact
bytes): the event is authenticated by the key it names from the checkpoint key directory,
bound to that key's provider, fresh within B3's window, and recorded once (D8's ledger, 0042;
D7's receipt and ONE `checkpoint_received` outbox event). A replay is the same receipt.

No session and no API key: the signing key IS the caller's identity, and the directory is
read from the secret store by name (`LAB_CHECKPOINT_KEYS`, a credential: never logged,
never in a repr). Lab-only: mounted only when the composition put a `LabCheckpoints` on
`rt.lab_checkpoints` (LAB_CHECKPOINTS, off), and never part of the consumer App's surface.
Refusals are the Lab's fixed reasons (`lab_auth.refusal`): a bad signature or key 401.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import FastAPI, Request

from ...contracts import errors
from ...contracts.ids import UUID_RE
from ...evaluation import checkpoints
from .. import lab_auth
from . import intake

CHECKPOINTS_PATH = "/lab/v1/checkpoints"
SIGNATURE = "x-infrx-signature"
MAX_BODY_BYTES = 16_384                 # one event: ids, a URI and a digest


@dataclass(frozen=True)
class LabCheckpoints:
    keys: Callable[[str], tuple[str, bytes] | None]   # key id -> (provider, secret)
    ledger: checkpoints.CheckpointLedger              # D8: PgCheckpointLedger (0042)
    store: object                                     # D7: PgLabDataStore


def key_directory(text: str) -> Callable[[str], tuple[str, bytes] | None]:
    """`LAB_CHECKPOINT_KEYS`: `{"<key id>": {"provider_org_id": <uuid>, "secret": <hex>}}`.
    A malformed directory is a `ValueError` that names nothing in it."""
    try:
        found = {key_id: (entry["provider_org_id"], bytes.fromhex(entry["secret"]))
                 for key_id, entry in json.loads(text).items()}
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ValueError("LAB_CHECKPOINT_KEYS is not a key directory") from None
    if not found or any(not UUID_RE.fullmatch(provider) or len(secret) < 32
                        for provider, secret in found.values()):
        raise ValueError("LAB_CHECKPOINT_KEYS: each key names a provider and a secret of "
                         "at least 32 bytes")
    return found.get


def register(app: FastAPI, rt: Any, lab_checkpoints: LabCheckpoints | None = None
             ) -> LabCheckpoints | None:
    """Mount the receiver over `lab_checkpoints` (default `rt.lab_checkpoints`); without one
    nothing is mounted and `None` is returned."""
    x = lab_checkpoints if lab_checkpoints is not None \
        else getattr(rt, "lab_checkpoints", None)
    if x is None:
        return None
    limits = rt.settings.pilot

    @app.post(CHECKPOINTS_PATH)
    @lab_auth.guarded
    async def receive(request: Request):
        raw = await intake.read_body(request, max_bytes=MAX_BODY_BYTES,
                                     timeout_s=limits.intake_timeout_s, clock=rt.clock)
        signature = request.headers.get(SIGNATURE)
        if not signature:
            raise errors.InvalidApiKey("the checkpoint event is not signed")
        receipt = await checkpoints.receive(raw, signature, keys=x.keys, ledger=x.ledger,
                                            store=x.store)
        return lab_auth.ok(receipt, 202)

    return x
