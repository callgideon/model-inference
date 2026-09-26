#!/usr/bin/env python3
"""Can this App release be a rollback (or deploy) target right now? (I3; offline, read-only.)

    python3 infra/app/rollback.py <candidate ref> --applied NNNN --gateway <backend release ref>
        [--schema-proof NNNN --evidence <file in this checkout> ...]

The App half of infra/rollout/known-good.py: judged on the candidate's own tree in this
repository, never on hosted state. The operator supplies the two hosted facts as arguments:
`--applied`, hosted's newest applied migration (from `migrate.py plan` or the session record),
and `--gateway`, the backend release the edge is running (its `infrx_build_info` / the
session record). The tool never connects to anything.

Checks, each printed with its evidence:
  commit      the ref resolves to a commit here; its full id is what `/api/version` must report
  identity    the tree has I2A's release identity (apps/app/instrumentation.ts and
              apps/app/app/api/version/route.ts); a pre-I2A tree cannot be told apart after
              a deploy, so it is never a target
  migrations  the tree's newest apps/app/supabase/migrations/NNNN_*.sql == --applied (the
              App's queries and RPCs exist on hosted). Hosted AHEAD of the tree passes only
              with --schema-proof reaching --applied and --evidence files that exist here: as
              infra/rollout/known-good.py (brief section I8.6), additive compatibility is
              proven (this tree's console tests on the newer schema), never assumed - not
              every migration is additive (0021 revokes column grants and drops a policy)
  contract    the tree's pinned contract surface (SURFACE_VERSION, contracts-vMAJOR.MINOR in
              apps/app/lib/contracts/v2/money-units.ts): same MAJOR, MINOR <= the gateway release's
              (apps/infrx-api/infrx/contracts/v2/__init__.py)
Exit 0 COMPATIBLE, 1 REFUSED, 2 usage.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MIGRATIONS = "apps/app/supabase/migrations"
IDENTITY = ("apps/app/instrumentation.ts", "apps/app/app/api/version/route.ts")
APP_CONTRACT = "apps/app/lib/contracts/v2/money-units.ts"
GATEWAY_CONTRACT = "apps/infrx-api/infrx/contracts/v2/__init__.py"
SURFACE = re.compile(r'SURFACE_VERSION\s*=\s*"contracts-v(\d+)\.(\d+)"')


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def resolve(repo: Path, ref: str) -> str | None:
    done = git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    return done.stdout.strip() if done.returncode == 0 else None


def show(repo: Path, sha: str, path: str) -> str | None:
    done = git(repo, "show", f"{sha}:{path}")
    return done.stdout if done.returncode == 0 else None


def surface(repo: Path, sha: str, path: str) -> tuple[int, int] | None:
    found = SURFACE.search(show(repo, sha, path) or "")
    return (int(found.group(1)), int(found.group(2))) if found else None


def judge(ref: str, applied: str, gateway_ref: str, repo: Path = REPO,
          schema_proof: str | None = None, evidence: tuple[str, ...] = ()) -> dict:
    checks = []

    def check(name, ok, detail):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    sha = resolve(repo, ref)
    gateway = resolve(repo, gateway_ref)
    check("commit", sha, f"/api/version must report commit {sha}" if sha
          else f"{ref!r} is not a commit in this repository")
    if gateway is None:
        check("contract", False, f"--gateway {gateway_ref!r} is not a commit in this repository")
    if not sha or not gateway:
        return {"candidate": ref, "verdict": "REFUSED", "checks": checks}
    missing = [path for path in IDENTITY if show(repo, sha, path) is None]
    check("identity", not missing, "release identity present (/api/version, startup check)"
          if not missing else f"pre-I2A tree, no release identity: missing {', '.join(missing)}")
    files = git(repo, "ls-tree", "--name-only", sha, f"{MIGRATIONS}/").stdout.split()
    numbers = sorted(Path(f).name[:4] for f in files if re.match(r"\d{4}_.*\.sql$", Path(f).name))
    newest = numbers[-1] if numbers else "0000"
    proven = (schema_proof is not None and schema_proof >= applied and bool(evidence)
              and all((repo / path).is_file() and (repo / path).resolve().is_relative_to(repo.resolve())
                      for path in evidence))
    compatible = newest == applied or (newest < applied and proven)
    if newest > applied:
        why = f": {', '.join(n for n in numbers if n > applied)} not applied"
    elif newest < applied:
        why = (f"; applied beyond the tree: {int(newest) + 1:04d}-{applied} "
               + (f"(proven through {schema_proof}: {', '.join(evidence)})" if proven else
                  "with no --schema-proof reaching it and existing --evidence: prove this tree on the newer schema"))
    else:
        why = ""
    check("migrations", compatible, f"tree carries {len(numbers)} migrations, newest {newest}; "
          f"hosted has {applied}{why}")
    app, backend = surface(repo, sha, APP_CONTRACT), surface(repo, gateway, GATEWAY_CONTRACT)
    fmt = lambda v: f"contracts-v{v[0]}.{v[1]}" if v else "none"   # noqa: E731
    # Same MAJOR (a MAJOR change is not compatible either way), MINOR <= the gateway's (I3R-5).
    check("contract", app is not None and backend is not None and app[0] == backend[0] and app[1] <= backend[1],
          f"App pins {fmt(app)}; gateway {gateway[:12]} serves {fmt(backend)}")
    return {"candidate": ref, "commit": sha, "gateway": gateway,
            "verdict": "COMPATIBLE" if all(c["ok"] for c in checks) else "REFUSED",
            "expected_version_commit": sha, "migrations": numbers, "applied": applied,
            "checks": checks}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("candidate", help="the App release to judge (any ref in this repository)")
    ap.add_argument("--applied", required=True, help="hosted's newest applied migration, e.g. 0023")
    ap.add_argument("--gateway", required=True, help="the backend release the edge runs (a ref)")
    ap.add_argument("--schema-proof", metavar="NNNN",
                    help="hosted ahead of the tree: the newest migration this tree is proven on")
    ap.add_argument("--evidence", action="append", default=[], metavar="PATH",
                    help="the proof's evidence file(s), relative to the repository root")
    ap.add_argument("--repo", type=Path, default=REPO, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    for name, value in (("--applied", a.applied), ("--schema-proof", a.schema_proof)):
        if value is not None and not re.fullmatch(r"\d{4}", value):
            ap.error(f"{name} is four digits, e.g. 0023")
    result = judge(a.candidate, a.applied, a.gateway, a.repo, a.schema_proof, tuple(a.evidence))
    print(json.dumps(result, indent=1))
    return 0 if result["verdict"] == "COMPATIBLE" else 1


if __name__ == "__main__":
    sys.exit(main())
