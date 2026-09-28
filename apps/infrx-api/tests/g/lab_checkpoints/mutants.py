#!/usr/bin/env python3
"""R32/R40/R83 for WR-B3-2: one single-edit defect per invariant the checkpoint receiver
(`lab_checkpoints`) claims.

    uv run --frozen pytest -q tests/g/lab_checkpoints/test_mutants.py
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/g/lab_checkpoints/test_mutants.py
    uv run --frozen python -m tests.g.lab_checkpoints.mutants --list
"""
from __future__ import annotations

from ...contracts import mutants as shared
from ...contracts.mutants import Mutant, Result
from ..lab_auth import mutants as auth

SUITE_FILES = ("tests/g/lab_checkpoints/test_lab_checkpoints.py",)
F = "gateway/routes/lab_checkpoints.py"
FILES = (F,)
C = "test_lab_checkpoints__"
MOUNT = C + "nothing_is_mounted_without_the_switch"
ONCE = C + "a_signed_event_is_one_receipt_and_a_replay_the_same"
REFUSED = C + "unsigned_forged_and_foreign_events_are_refused_unrecorded"
BOUND = C + "an_oversized_body_is_refused_before_b3"
KEYS = C + "the_key_directory_binds_each_key_to_a_provider_and_a_secret"


def _m(name, invariant, old, new, *cases) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=F, old=old, new=new, cases=cases)


MUTANTS: tuple[Mutant, ...] = (
    _m("mounted_without_the_switch", "LAB_CHECKPOINTS off (no rt.lab_checkpoints): no route",
       "    if x is None:\n        return None\n    limits",
       "    if False:\n        return None\n    limits", MOUNT),
    _m("runtime_checkpoints_ignored", "register(app, rt) mounts over rt.lab_checkpoints",
       'getattr(rt, "lab_checkpoints", None)', "None", MOUNT),
    _m("body_reencoded", "the HMAC is over the caller's exact bytes",
       "        receipt = await checkpoints.receive(raw, signature,",
       "        receipt = await checkpoints.receive(json.dumps(json.loads(raw)).encode(), "
       "signature,", ONCE),
    _m("accepted_as_200", "a receipt is a 202 (accepted for evaluation, not evaluated)",
       "        return lab_auth.ok(receipt, 202)", "        return lab_auth.ok(receipt)", ONCE),
    _m("signature_from_the_query", "the signature is the X-Infrx-Signature header only",
       "        signature = request.headers.get(SIGNATURE)\n",
       '        signature = request.headers.get(SIGNATURE) or '
       'request.query_params.get("signature")\n', REFUSED),
    _m("unsigned_not_unauthenticated", "an unsigned event is a 401",
       "        if not signature:\n            raise errors.InvalidApiKey",
       "        if False:\n            raise errors.InvalidApiKey", REFUSED),
    _m("unbounded_body", "one event is a small bounded body",
       "max_bytes=MAX_BODY_BYTES,", "max_bytes=MAX_BODY_BYTES * 100,", BOUND),
    _m("short_secret_accepted", "a signing key is at least 32 bytes",
       "len(secret) < 32", "len(secret) < 1", KEYS),
    _m("key_without_a_provider", "each key is bound to a provider uuid",
       "    if not found or any(not UUID_RE.fullmatch(provider) or len(secret) < 32",
       "    if not found or any(len(secret) < 32", KEYS),
    _m("empty_directory_accepted", "an empty directory is not a directory",
       "    if not found or any(", "    if any(", KEYS),
)


def case_names() -> set[str]:
    return auth.case_names_in(SUITE_FILES)


RUNNER = auth.runner("lab-checkpoints", SUITE_FILES)


def run_mutant(mutant) -> Result:
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run WR-B3-2's receiver mutation list"))
