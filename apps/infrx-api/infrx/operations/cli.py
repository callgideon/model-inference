"""Operator CLI over `service.Operations`. Runs with both Next.js apps stopped.

    export INFRX_OPERATOR_KEY            # or answer the no-echo prompt
    python -m infrx.operations.cli grant --user <uuid> --idempotency-key g-<uuid> --reason "..."
    python -m infrx.operations.cli issue-key --user <uuid> --name sweep \\
        --secret-file ./sweep.key --idempotency-key k-1 --reason "..."

Secrets never travel through argv or output: the operator secret comes from
`$INFRX_OPERATOR_KEY` or `getpass`, any argv token shaped like a key is refused, and an
issued secret is written once into `--secret-file` (created 0600, never overwritten).
stdout carries the JSON result without the secret.

The composition root is `build_operations()`. It refuses until D1R/D5/A1 provide the
PostgreSQL adapters (the fakes live in tests only), so this tool cannot run against a
store that would silently accept writes nobody persists.
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import sys
from datetime import datetime

from ..contracts import errors
from . import service

OPERATOR_KEY_ENV = "INFRX_OPERATOR_KEY"


def build_operations() -> service.Operations:
    raise SystemExit("no state adapter is wired: the D1R/D5/A1 PostgreSQL adapters are "
                     "pending, and this tool does not run against an in-memory store")


def refuse_secret_argv(argv: list[str]) -> None:
    for token in argv:
        if token.startswith("sk-") or service.KEY_PREFIX in token:
            raise SystemExit(f"refusing a key on the command line; export {OPERATOR_KEY_ENV}")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="infrx.operations.cli")
    sub = p.add_subparsers(dest="cmd", required=True)

    def cmd(name, *args):
        c = sub.add_parser(name)
        for flag in args:
            c.add_argument(flag, required=flag not in ("--code",))
        c.add_argument("--idempotency-key", required=True)
        c.add_argument("--reason", required=True)
        return c

    cmd("grant", "--user")
    cmd("adjust", "--user", "--amount")
    cmd("issue-key", "--user", "--name", "--secret-file")
    cmd("rotate-key", "--org", "--key-id", "--name", "--secret-file")
    cmd("revoke-key", "--org", "--key-id")
    cmd("suspend", "--org", "--code")                 # no --code lifts the suspension
    # RFC 3339 with offset. `--created-at` names the serving/deployment rows and stays
    # fixed across rate changes; a new `--effective-at` is a new rate card version.
    cmd("publish-marlin", "--provider-org", "--created-at", "--effective-at")
    cmd("cancel", "--org", "--job")
    cmd("reconcile", "--org", "--request")
    return p


def _write_secret_once(path: str, secret: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(secret + "\n")


def _issued(a, issued: service.IssuedKey) -> dict:
    if issued.secret is not None:
        _write_secret_once(a.secret_file, issued.secret)
    return {"key_id": issued.key_id, "org_id": issued.org_id, "prefix": issued.prefix,
            "replayed": issued.replayed,
            "secret_file": a.secret_file if issued.secret is not None else None}


async def dispatch(ops: service.Operations, secret: str, a) -> dict:
    op = await ops.operator(secret)
    k = {"idempotency_key": a.idempotency_key, "reason": a.reason}
    if a.cmd == "grant":
        return await op.grant_initial(a.user, **k)
    if a.cmd == "adjust":
        return await op.adjust(a.user, a.amount, **k)
    if a.cmd == "issue-key":
        if os.path.exists(a.secret_file):     # refuse before a key exists, not after
            raise SystemExit(f"{a.secret_file} exists; choose a new path")
        return _issued(a, await op.issue_key(a.user, a.name, **k))
    if a.cmd == "rotate-key":
        if os.path.exists(a.secret_file):
            raise SystemExit(f"{a.secret_file} exists; choose a new path")
        return _issued(a, await op.rotate_key(a.org, a.key_id, a.name, **k))
    if a.cmd == "revoke-key":
        return await op.revoke_key(a.org, a.key_id, **k)
    if a.cmd == "suspend":
        return await op.set_suspension(a.org, a.code, **k)
    if a.cmd == "publish-marlin":
        created, effective = (datetime.fromisoformat(v) for v in (a.created_at, a.effective_at))
        if created.utcoffset() is None or effective.utcoffset() is None:
            raise SystemExit("--created-at/--effective-at need an explicit UTC offset")
        serving, deployment, card, model = service.marlin_release(
            provider_org_id=a.provider_org, created_at=created, effective_at=effective)
        return await op.publish(serving, deployment, card, model, **k)
    if a.cmd == "cancel":
        return await op.cancel_job(a.org, a.job, **k)
    if a.cmd == "reconcile":
        return await op.reconcile(a.org, a.request, **k)
    raise SystemExit(f"unknown command {a.cmd}")


def main(argv=None, *, ops=None, environ=os.environ, prompt=getpass.getpass) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    refuse_secret_argv(argv)
    a = parser().parse_args(argv)
    secret = environ.get(OPERATOR_KEY_ENV, "").strip() or prompt("operator key: ").strip()
    try:
        result = asyncio.run(dispatch(ops or build_operations(), secret, a))
    except errors.DomainError as e:
        print(json.dumps({"error": e.code, "message": str(e)}), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
