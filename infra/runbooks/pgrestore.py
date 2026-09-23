#!/usr/bin/env python3
"""I3B.c: back up, restore and CHECK a Supabase project's database.

The tool `restore.md` runs against hosted Supabase and `tests/integration/backend/recovery/
test_restore.py` drills on E2's stack - one implementation, so the rehearsed procedure is
the one an operator runs.

    python infra/runbooks/pgrestore.py dump    --conninfo SOURCE --out DIR
    python infra/runbooks/pgrestore.py restore --conninfo TARGET --from DIR
    python infra/runbooks/pgrestore.py check   --source SOURCE --target TARGET

CONNINFO is a libpq string without the password (`host=... port=5432 user=... dbname=postgres
sslmode=require`); the password comes from `PGPASSWORD` in the environment, never argv.
`pg_dump`/`pg_restore` run in the pinned `supabase/postgres` image (hosted is 17.6 and the
host's own client is 16, which cannot dump a 17 server), in a throwaway `infrx-i3b-pgclient-*`
container on the host network, with the backup directory mounted. psycopg (the API venv)
reads the catalog.

What a plain `pg_dump`/`pg_restore` of a Supabase project gets wrong, measured on the pinned
image and handled here (each one is a drill: bk01, bk01b, bk01c):

1. The target's template owns `public` - restoring `CREATE SCHEMA public` fails - and the
   image's own roles' default privileges, which `postgres` may not re-issue: those table-of-
   contents entries are skipped (`restorable`).
2. The template gives role `postgres` DEFAULT privileges in `public` that grant ALL on every
   new table, sequence and function to anon/authenticated/service_role. pg_dump writes grants
   as a difference from the built-in default, so a plain restore leaves **anon with full
   access to every tenant table** (R59's revokes are per object and are not replayed). The
   target's defaults are emptied first; the dump's DEFAULT ACL entries restore the source's.
3. A schema-scoped dump cannot carry what the project attached to other schemas: its trigger
   on `auth.users` (0001's signup trigger) and 0004's GLOBAL function default (EXECUTE revoked
   from PUBLIC). Both are read from the source at dump time into `meta.json` and replayed.

A backup is `project.dump`, `auth.dump`, `meta.json` and `SHA256SUMS`, mode 0600 in a 0700
directory; `restore` refuses a directory whose checksums do not match, and refuses - before
writing anything - a target that is the backup's own source or is not empty (bk01e).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# E2's pinned PostgreSQL (tests/integration/compose.yaml; test_restore asserts they agree).
IMAGE = ("supabase/postgres@sha256:"
         "7768d0d1d377250b718a9ad07f4661d008ebe6c96ecbbc4c08f3c5e53553e8fd")
ROLE = "postgres"                      # the database role behind hosted's pooler login
SCHEMAS = ("public", "infrx")
AUTH_TABLES = ("auth.users", "auth.identities")   # whichever exist
FILES = ("project.dump", "auth.dump", "meta.json")
TEMPLATE_OWNED = ("SCHEMA - public ", "COMMENT - SCHEMA public ")
NEUTRALIZE_DEFAULTS = tuple(
    f"alter default privileges for role {ROLE} in schema public "
    f"revoke all on {kind} from anon, authenticated, service_role"
    for kind in ("tables", "sequences", "functions"))
GLOBAL_FUNCTION_DEFAULT = ("select defaclacl::text from pg_default_acl where defaclrole = "
                           "%s::regrole and defaclnamespace = 0 and defaclobjtype = 'f'")
AUTH_TRIGGERS = (
    "select pg_get_triggerdef(t.oid) from pg_trigger t "
    "join pg_proc p on p.oid = t.tgfoid join pg_namespace fn on fn.oid = p.pronamespace "
    "join pg_class c on c.oid = t.tgrelid join pg_namespace tn on tn.oid = c.relnamespace "
    "where not t.tgisinternal and tn.nspname = 'auth' and fn.nspname = any(%s) order by 1")


# --------------------------------------------------------------------- the client

def client(backup: Path) -> list[str]:
    """`pg_dump`/`pg_restore` of the pinned image, as the invoking user, backup mounted."""
    return ["docker", "run", "--rm", "-i", "--network", "host",
            "--name", f"infrx-i3b-pgclient-{uuid.uuid4().hex[:12]}",
            "--user", f"{os.getuid()}:{os.getgid()}", "-e", "PGPASSWORD",
            "-v", f"{backup.resolve()}:/backup", IMAGE]


def run(backup: Path, *argv: str) -> str:
    try:
        # errors="replace": a client reading a damaged archive can print non-UTF-8 bytes,
        # which must end in the RuntimeError below, not in a UnicodeDecodeError.
        result = subprocess.run([*client(backup), *argv], capture_output=True, text=True,
                                errors="replace", timeout=1800)
    except subprocess.TimeoutExpired:
        # Its text is the whole argv, conninfo included: names only, and not chained.
        raise RuntimeError(f"{argv[0]} timed out after 1800 s") from None
    if result.returncode != 0:
        # DETAIL/CONTEXT quote key values or a whole row (auth.users e-mails): never re-raised.
        said = "\n".join(line for line in result.stderr.splitlines()
                         if not line.lstrip().startswith(("DETAIL:", "CONTEXT:")))
        raise RuntimeError(f"{argv[0]} failed ({result.returncode}): {said[-800:]}")
    return result.stdout


def connect(conninfo: str):
    import psycopg
    return psycopg.connect(conninfo, autocommit=True)


def existing(conn, names, kind: str) -> list[str]:
    probe = "select to_regnamespace(%s)" if kind == "schema" else "select to_regclass(%s)"
    return [name for name in names if conn.execute(probe, (name,)).fetchone()[0]]


# --------------------------------------------------------------------- dump

def dump(conninfo: str, out: Path) -> dict:
    """The two dumps plus what a schema-scoped dump cannot carry, checksummed."""
    out.mkdir(parents=True, exist_ok=True)
    os.chmod(out, 0o700)
    with connect(conninfo) as conn:
        schemas = existing(conn, SCHEMAS, "schema")
        auth = existing(conn, AUTH_TABLES, "table")
        acl = conn.execute(GLOBAL_FUNCTION_DEFAULT, (ROLE,)).fetchone()
        meta = {"dumped_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "source": identity(conninfo), "image": IMAGE,
                "schemas": schemas, "auth_tables": auth,
                "auth_triggers": [row[0] for row in
                                  conn.execute(AUTH_TRIGGERS, (list(SCHEMAS),)).fetchall()],
                "global_function_default": acl[0] if acl else None,
                "server_version": conn.execute("show server_version").fetchone()[0]}
    run(out, "pg_dump", "-d", conninfo, "--format=custom", "-f", "/backup/project.dump",
        *[arg for schema in schemas for arg in ("--schema", schema)])
    run(out, "pg_dump", "-d", conninfo, "--format=custom", "--data-only",
        "-f", "/backup/auth.dump", *[arg for table in auth for arg in ("--table", table)])
    (out / "meta.json").write_text(json.dumps(meta, indent=2))
    sums = "".join(f"{hashlib.sha256((out / name).read_bytes()).hexdigest()}  {name}\n"
                   for name in FILES)
    (out / "SHA256SUMS").write_text(sums)
    for name in (*FILES, "SHA256SUMS"):
        os.chmod(out / name, 0o600)
    return meta


def verify(backup: Path) -> None:
    for line in (backup / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split("  ", 1)
        if hashlib.sha256((backup / name).read_bytes()).hexdigest() != digest:
            raise RuntimeError(f"{name} does not match SHA256SUMS: not restoring a damaged backup")


# --------------------------------------------------------------------- restore

LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})


def identity(conninfo: str) -> dict:
    """Which database a conninfo names, resolved the way libpq resolves it: key, else
    `hostaddr` for the host, else the PG* environment, else libpq's default (port 5432, the
    OS user, dbname = user); every loopback spelling is 127.0.0.1. `user` is part of it:
    every hosted project behind the same regional pooler shares host and dbname and differs
    only in `postgres.<ref>`. Best effort - two DNS names for one server still differ - so
    the empty-target guard, not this, is the operative protection (restore.md A5)."""
    import getpass
    from psycopg.conninfo import conninfo_to_dict
    parsed = conninfo_to_dict(conninfo)

    def value(key: str, env: str) -> str:
        return str(parsed.get(key) or os.environ.get(env) or "")
    host = value("host", "PGHOST") or value("hostaddr", "PGHOSTADDR")
    host = "127.0.0.1" if host in LOOPBACK else host
    user = value("user", "PGUSER") or getpass.getuser()
    return {"host": host, "port": value("port", "PGPORT") or "5432", "user": user,
            "dbname": value("dbname", "PGDATABASE") or user}


def refuse_live_target(conninfo: str, meta: dict) -> None:
    """Before any write: the target is not the backup's source, and it is empty - no infrx
    schema, no table in public, no auth row. A restore that would fail on a live database
    must fail here, not after it has changed that database's default privileges."""
    if meta.get("source") == identity(conninfo):
        raise RuntimeError("refusing to restore: the target is the backup's own source")
    with connect(conninfo) as conn:
        occupied = conn.execute(
            "select to_regnamespace('infrx') is not null or exists (select 1 from pg_class c "
            "join pg_namespace n on n.oid = c.relnamespace "
            "where n.nspname = 'public' and c.relkind in ('r', 'p'))").fetchone()[0]
        occupied = occupied or any(conn.execute(f"select exists (select 1 from {table})")
                                   .fetchone()[0] for table in meta["auth_tables"])
    if occupied:
        raise RuntimeError("refusing to restore: the target is not empty (restore only into a "
                           "fresh database from the Supabase template)")


def restorable(listing: str) -> str:
    """`pg_restore -l` minus what the target's template already provides (item 1)."""
    keep = []
    for line in listing.splitlines():
        if any(marker in line for marker in TEMPLATE_OWNED):
            continue
        if " DEFAULT ACL " in line and not line.rstrip().endswith(f" {ROLE}"):
            continue
        keep.append(line)
    return "\n".join(keep) + "\n"


def restore(conninfo: str, backup: Path, *, neutralize: bool = True) -> None:
    """Auth rows first (the tenant tables' foreign keys point at them), then the project
    through the filtered table of contents, then the replayed extras (items 2 and 3).
    `--exit-on-error`: a partial restore is a failed restore, not a warning. Into an EMPTY
    database created from the Supabase template - never onto live data."""
    verify(backup)
    meta = json.loads((backup / "meta.json").read_text())
    refuse_live_target(conninfo, meta)
    run(backup, "pg_restore", "-d", conninfo, "--data-only", "--exit-on-error",
        "/backup/auth.dump")
    if neutralize:
        with connect(conninfo) as conn:
            for statement in NEUTRALIZE_DEFAULTS:
                conn.execute(statement)
    listing = run(backup, "pg_restore", "-l", "/backup/project.dump")
    list_file = backup / "project.list"
    list_file.write_text(restorable(listing))
    try:
        run(backup, "pg_restore", "-d", conninfo, "--exit-on-error",
            "-L", "/backup/project.list", "/backup/project.dump")
    finally:
        list_file.unlink(missing_ok=True)
    with connect(conninfo) as conn:
        for definition in meta["auth_triggers"]:
            conn.execute(definition)
        wanted = meta["global_function_default"]
        # An aclitem granted to PUBLIC has an empty grantee: `=X/postgres`.
        public_executes = wanted is None or any(
            item.startswith("=") for item in wanted.strip("{}").split(","))
        if not public_executes:
            conn.execute(f"alter default privileges for role {ROLE} "
                         f"revoke execute on functions from public")


# --------------------------------------------------------------------- check

# R92: an ACL is compared by the privileges it grants, not by how the catalog spells them. A
# NULL ACL is the owner's implicit default, and pg_dump writes only the difference from that
# default, so an explicit owner-only ACL (0014's `revoke all ... from public, anon,
# authenticated, service_role` on infrx.job_results) is restored as NULL. It is the same
# privilege set: both sides are normalised with acldefault() before the diff. An ACL emptied
# by a REVOKE from the owner is '{}', not NULL, and still differs.
CATALOG = {
    "relations": "select n.nspname || '.' || c.relname, c.relkind::text, c.relrowsecurity, "
                 "c.relforcerowsecurity, array(select unnest(coalesce(c.relacl, acldefault("
                 "case c.relkind when 'S' then 's' else 'r' end::\"char\", c.relowner)))::text "
                 "order by 1)::text "
                 "from pg_class c join pg_namespace n on n.oid = c.relnamespace "
                 "where n.nspname = any(%(s)s) and c.relkind in ('r','p','v','m','S') order by 1",
    "columns": "select table_schema || '.' || table_name, column_name, data_type, is_nullable, "
               "coalesce(column_default, '') from information_schema.columns "
               "where table_schema = any(%(s)s) order by 1, 2",
    # RST-3: column privileges (0001's `grant update (full_name, avatar_url) on
    # public.profiles to authenticated`). A column has no owner default: NULL is no grant.
    "column_acls": "select n.nspname || '.' || c.relname, a.attname, "
                   "array(select unnest(a.attacl)::text order by 1)::text from pg_attribute a "
                   "join pg_class c on c.oid = a.attrelid "
                   "join pg_namespace n on n.oid = c.relnamespace where n.nspname = any(%(s)s) "
                   "and a.attnum > 0 and not a.attisdropped and a.attacl is not null "
                   "order by 1, 2",
    "policies": "select schemaname || '.' || tablename, policyname, cmd, roles::text, "
                "coalesce(qual, ''), coalesce(with_check, '') from pg_policies "
                "where schemaname = any(%(s)s) order by 1, 2",
    "functions": "select n.nspname || '.' || p.proname, pg_get_function_identity_arguments(p.oid), "
                 "p.prosecdef, array(select unnest(coalesce(p.proacl, acldefault('f', "
                 "p.proowner)))::text order by 1)::text, "
                 "md5(p.prosrc), coalesce(p.proconfig::text, '') from pg_proc p "
                 "join pg_namespace n on n.oid = p.pronamespace "
                 "where n.nspname = any(%(s)s) order by 1, 2",
    "triggers": "select c.relname, t.tgname, pg_get_triggerdef(t.oid), t.tgenabled::text "
                "from pg_trigger t "
                "join pg_class c on c.oid = t.tgrelid join pg_proc p on p.oid = t.tgfoid "
                "join pg_namespace fn on fn.oid = p.pronamespace "
                "where not t.tgisinternal and fn.nspname = any(%(s)s) order by 1, 2",
    "constraints": "select conrelid::regclass::text, conname, pg_get_constraintdef(oid) "
                   "from pg_constraint where connamespace in "
                   "(select oid from pg_namespace where nspname = any(%(s)s)) order by 1, 2",
    "indexes": "select schemaname || '.' || indexname, indexdef from pg_indexes "
               "where schemaname = any(%(s)s) order by 1",
    "views": "select schemaname || '.' || viewname, md5(definition) from pg_views "
             "where schemaname = any(%(s)s) order by 1",
    "schemas": "select nspname, array(select unnest(coalesce(nspacl, acldefault('n', "
               "nspowner)))::text order by 1)::text "
               "from pg_namespace where nspname = any(%(s)s) order by 1",
    # What every FUTURE object gets: the defaults 0004 narrowed, per schema and global.
    "default_acls": "select defaclrole::regrole::text, "
                    "coalesce(nullif(defaclnamespace, 0)::regnamespace::text, '-'), "
                    "defaclobjtype::text, array(select unnest(defaclacl)::text order by 1)::text "
                    "from pg_default_acl order by 1, 2, 3",
}


def fingerprint(conninfo: str) -> dict:
    """Every row (count + md5 of the sorted row texts) of every project table and the auth
    rows, and the catalog facts the tenant boundary rests on. Read-only."""
    with connect(conninfo) as conn:
        conn.execute("set default_transaction_read_only = on")
        # A6 points this at LIVE hosted: prove the session is read-only before reading.
        if conn.execute("show transaction_read_only").fetchone()[0] != "on":
            raise RuntimeError("refusing to fingerprint: the session is not read-only")
        tables = [row[0] for row in conn.execute(
            "select quote_ident(n.nspname) || '.' || quote_ident(c.relname) from pg_class c "
            "join pg_namespace n on n.oid = c.relnamespace "
            "where n.nspname = any(%s) and c.relkind in ('r', 'p') order by 1",
            (list(SCHEMAS),)).fetchall()] + existing(conn, AUTH_TABLES, "table")
        rows = {table: list(conn.execute(
            f"select count(*), md5(coalesce(string_agg(t::text, E'\\n' order by t::text), '')) "
            f"from {table} t").fetchone()) for table in tables}
        catalog = {name: [list(row) for row in conn.execute(sql, {"s": list(SCHEMAS)})]
                   for name, sql in CATALOG.items()}
    # MEASURED: a CHECK re-parsed on restore deparses its AND chain flattened -
    # `((a AND b) AND c)` comes back as `(a AND b AND c)`. Same constraint, different text.
    for row in catalog["constraints"]:
        row[2] = re.sub(r"[()]", "", row[2])
    return {"rows": rows, "catalog": catalog}


def drift(conninfo: str) -> list:
    """The two detectors (R59-7): a wallet total that disagrees with its ledger or holds."""
    found = []
    with connect(conninfo) as conn:
        for view in existing(conn, ("infrx.wallet_reconciliation",
                                    "infrx.credit_wallet_reconciliation"), "table"):
            found += conn.execute(f"select * from {view} where ledger_drift <> 0 "
                                  f"or reserved_drift <> 0").fetchall()
    return found


def compare(source: dict, target: dict) -> list[str]:
    """Every difference, named; empty means the restore is the source."""
    problems = [f"rows of {table} differ" for table in sorted(set(source["rows"])
                                                              | set(target["rows"]))
                if source["rows"].get(table) != target["rows"].get(table)]
    for name, rows in source["catalog"].items():
        have = {tuple(row) for row in target["catalog"].get(name, [])}
        want = {tuple(row) for row in rows}
        if have != want:
            problems.append(f"{name} differ: missing {sorted(want - have)[:3]}, "
                            f"extra {sorted(have - want)[:3]}")
    return problems


# --------------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    one = commands.add_parser("dump")
    one.add_argument("--conninfo", required=True)
    one.add_argument("--out", type=Path, required=True)
    two = commands.add_parser("restore")
    two.add_argument("--conninfo", required=True)
    two.add_argument("--from", dest="backup", type=Path, required=True)
    three = commands.add_parser("check")
    three.add_argument("--source", required=True)
    three.add_argument("--target", required=True)
    args = parser.parse_args(argv)
    if args.command == "dump":
        meta = dump(args.conninfo, args.out)
        print(json.dumps({key: meta[key] for key in ("dumped_at", "schemas", "auth_tables",
                                                     "server_version")}))
        print((args.out / "SHA256SUMS").read_text(), end="")
        return 0
    if args.command == "restore":
        restore(args.conninfo, args.backup)
        print("restored")
        return 0
    problems = compare(fingerprint(args.source), fingerprint(args.target))
    problems += [f"drift in the target: {row}" for row in drift(args.target)]
    print(json.dumps({"equal": not problems, "problems": problems}, indent=2, default=str))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
