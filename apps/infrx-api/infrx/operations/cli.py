"""Operator CLI over `service.Operations`. Runs with both Next.js apps stopped.

    export INFRX_OPERATOR_KEY            # or answer the no-echo prompt
    python -m infrx.operations.cli grant --user <uuid> --idempotency-key g-<uuid> --reason "..."
    python -m infrx.operations.cli issue-key --user <uuid> --name sweep \\
        --secret-file ./sweep.key --idempotency-key k-1 --reason "..."

Secrets never travel through argv or output: the operator secret comes from
`$INFRX_OPERATOR_KEY` or `getpass`, any argv token shaped like a key is refused, and an
issued secret is written once into `--secret-file` (created 0600, never overwritten).
stdout carries the JSON result without the secret.

The composition root is `build_operations()`: the D5/A1 PostgreSQL adapters over
`$OPERATIONS_DATABASE_URL` (the owner or broad login, read like `$INFRX_OPERATOR_KEY`),
else the deployment's `DATABASE_URL` (`config.from_env`), one fresh `service_role`
connection per operation. Without either the tool refuses (the fakes live in tests only),
so it cannot run against a store that would silently accept writes nobody persists. A
dedicated login (`infrx_runtime`/`infrx_monitor`, R127) is refused before anything is
dialled: it cannot `set role`, and the runtime login must never rewrite money.
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import sys
from datetime import datetime, timezone

from ..contracts import errors
from ..contracts.v2 import fixtures as v2fix
from . import service, transition

OPERATOR_KEY_ENV = "INFRX_OPERATOR_KEY"
OPERATIONS_DSN_ENV = "OPERATIONS_DATABASE_URL"


def build_operations(settings=None, *, environ=os.environ) -> service.Operations:
    """D5 request 4 / E4B request 4: the operator ports on PostgreSQL, from
    `$OPERATIONS_DATABASE_URL`, else `settings` (default: the process environment, as the
    gateway reads it). Messages name the variable, never the DSN."""
    from ..config import from_env
    from ..gateway.pilot import dedicated_login
    dsn = environ.get(OPERATIONS_DSN_ENV, "").strip()
    source = OPERATIONS_DSN_ENV if dsn else "DATABASE_URL"
    dsn = dsn or (from_env() if settings is None else settings).pilot.database_url.strip()
    if not dsn:
        raise SystemExit(f"DATABASE_URL is not set (nor {OPERATIONS_DSN_ENV}): this tool runs "
                         "only against the PostgreSQL store, never an in-memory one")
    try:
        dedicated = dedicated_login(dsn)
    except Exception:
        raise SystemExit(f"{source} is not a valid connection string") from None
    if dedicated:
        raise SystemExit(f"{source} logs in as a dedicated login (runtime/monitor), which "
                         f"the operator tool refuses: export {OPERATIONS_DSN_ENV} with the "
                         "owner or broad login's DSN")
    from ..state import operations as pg
    from ..state.catalog import PgCatalogDirectory
    from ..state.jobstore import PgJobStore, connector
    connect = connector(dsn)
    return service.Operations(
        identities=pg.PgSignup(pg._Db(connect)), tenants=pg.PgTenantStore(connect),
        ledger=pg.PgLedger(connect), audit=pg.PgAuditLog(connect),
        registry=pg.PgRegistry(connect), wallets=pg.PgWalletDirectory(connect),
        catalog=PgCatalogDirectory(connect), jobs=PgJobStore(connect),
        accounts=pg.PgAccountView(connect), clock=lambda: datetime.now(timezone.utc),
        transitions=transition.PgTransition(connect))


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
    # G8 / P-01: an operator-approved card for the deployment the model's listing serves.
    cmd("publish-card", "--model", "--card-version", "--input-rate", "--output-rate",
        "--approved-by", "--effective-at")
    # G8: the regime transition. `--dry-run` writes nothing and needs no operator key.
    t = sub.add_parser("credit-transition")
    t.add_argument("--to", choices=transition.TARGETS, default=transition.CREDIT)
    t.add_argument("--model", default=v2fix.PUBLIC_MODEL_ID)
    for flag in ("--card", "--input-rate", "--output-rate", "--idempotency-key", "--reason"):
        t.add_argument(flag)
    t.add_argument("--dry-run", action="store_true")
    t.add_argument("--freeze-only", action="store_true")   # pause both regimes, drain, stop
    t.add_argument("--drain-timeout-s", type=float, default=0.0)
    t.add_argument("--poll-s", type=float, default=2.0)
    # G8 reads: no idempotency key and no reason, since nothing is written.
    sub.add_parser("account").add_argument("--user", required=True)
    # A consumer's own statement, authenticated by the key file `issue-key` wrote.
    sub.add_parser("statement").add_argument("--key-file", required=True)
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


def _read_secret(path: str) -> str:
    with open(path) as f:
        return f.read().strip()


def _moment(value: str) -> datetime:
    moment = datetime.fromisoformat(value)
    if moment.utcoffset() is None:
        raise SystemExit("times need an explicit UTC offset")
    return moment


async def dispatch(ops: service.Operations, secret: str, a) -> dict:
    if a.cmd == "statement":
        return await (await ops.tenant(secret)).statement()
    rates = {}
    if a.cmd == "credit-transition":
        if ops.transitions is None:
            raise SystemExit("the transition runs only on the PostgreSQL store")
        rates = {"card": a.card, "input_rate": a.input_rate, "output_rate": a.output_rate}
        if a.dry_run:           # read-only: the inventory and the plan, no credential
            return transition.plan(await ops.transitions.inventory(a.model), target=a.to,
                                   **rates)
        if not a.idempotency_key or not a.reason:
            raise SystemExit("--idempotency-key and --reason are required unless --dry-run")
    op = await ops.operator(secret)
    if a.cmd == "account":
        return await op.account(a.user)
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
    if a.cmd == "publish-card":
        return await op.publish_card(a.model, rate_card_version=a.card_version,
                                     input_rate=a.input_rate, output_rate=a.output_rate,
                                     approved_by=a.approved_by,
                                     effective_at=_moment(a.effective_at), **k)
    if a.cmd == "credit-transition":
        return await transition.apply(op, ops.transitions, target=a.to, **rates, **k,
                                      drain_timeout_s=a.drain_timeout_s, poll_s=a.poll_s,
                                      freeze_only=a.freeze_only)
    if a.cmd == "cancel":
        return await op.cancel_job(a.org, a.job, **k)
    if a.cmd == "reconcile":
        return await op.reconcile(a.org, a.request, **k)
    raise SystemExit(f"unknown command {a.cmd}")


def main(argv=None, *, ops=None, environ=os.environ, prompt=getpass.getpass) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    refuse_secret_argv(argv)
    a = parser().parse_args(argv)
    if a.cmd == "statement":
        secret = _read_secret(a.key_file)
    elif a.cmd == "credit-transition" and a.dry_run:
        secret = ""
    else:
        secret = environ.get(OPERATOR_KEY_ENV, "").strip() or prompt("operator key: ").strip()
    try:
        result = asyncio.run(dispatch(ops or build_operations(environ=environ), secret, a))
    except transition.TransitionBlocked as e:
        print(json.dumps(e.report, sort_keys=True))         # what blocked it, what changed
        print(json.dumps({"error": e.code, "message": str(e)}), file=sys.stderr)
        return 1
    except errors.DomainError as e:
        print(json.dumps({"error": e.code, "message": str(e)}), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    # A dry run that finds blockers says so in its exit status too (0 = ready now).
    return 1 if a.cmd == "credit-transition" and result.get("blockers") else 0


if __name__ == "__main__":
    raise SystemExit(main())
