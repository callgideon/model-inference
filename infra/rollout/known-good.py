#!/usr/bin/env python3
"""Is this release a rollback target that can serve real work? (I8 slice 6; read-only,
coordinator host.) A candidate is judged on its own tree and its recorded evidence - never
on a backup directory's presence or a 5-second readiness (RV-10, F7).

    apps/infrx-api/.venv/bin/python infra/rollout/known-good.py <40-hex sha> \
        --applied 0018 [--set NAME ...] [--bundles s3://llm-bootcamp-641134885443/releases/]
    apps/infrx-api/.venv/bin/python infra/rollout/known-good.py --list --applied 0018

Checks, each printed with its evidence:
  commit        the commit exists here (a full id)
  preparation   the tree has the preparation loop: infrx/worker/preparation.py and the worker
                composition root runs a PreparationRunner (27af05a has neither)
  migrations    every migration the tree carries is applied on hosted (<= --applied, the
                version `migrate.py plan` reports); applied ones it does not know are listed:
                the additive-compatibility claim for them is D's (0001-0018 are additive)
  config        every tunable name the current install passes (--set, INFRX_SET's names)
                exists in the candidate's preflight schema - else its install refuses
  record        infra/rollout/known-good.json lists it known_good with evidence files that
                exist in this checkout
  bundle        with --bundles: <sha>.bundle and <sha>.sha256 exist in the release prefix
                (the route W1 fetches from; `aws s3 ls`, read-only)
Exit 0 KNOWN-GOOD, 1 NOT-KNOWN-GOOD, 2 usage.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parents[2]
REGISTRY = HERE.parent / "known-good.json"
MIGRATIONS = "apps/app/supabase/migrations"


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def show(repo: Path, sha: str, path: str) -> str | None:
    done = git(repo, "show", f"{sha}:{path}")
    return done.stdout if done.returncode == 0 else None


def schema_names(preflight_source: str) -> set[str]:
    """MANIFEST env names + TUNABLE, read from the candidate's preflight.py text (not run)."""
    tunable = re.search(r"^TUNABLE = \((.*?)^\)", preflight_source, re.S | re.M)
    names = set(re.findall(r'"([A-Z][A-Z0-9_]+)"', tunable.group(1))) if tunable else set()
    names |= set(re.findall(r'^\s*Key\("([A-Z][A-Z0-9_]+)"', preflight_source, re.M))
    return names


def judge(sha: str, applied: str, sets: list[str], bundles: str | None, registry: dict,
          repo: Path = REPO) -> dict:
    checks = []

    def check(name, ok, detail):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    exists = bool(re.fullmatch(r"[0-9a-f]{40}", sha)) and git(repo, "cat-file", "-e", f"{sha}^{{commit}}").returncode == 0
    check("commit", exists, sha if exists else "not a full commit id in this repository")
    if not exists:
        return {"sha": sha, "verdict": "NOT-KNOWN-GOOD", "checks": checks}
    main = show(repo, sha, "apps/infrx-api/infrx/worker/__main__.py") or ""
    prep = show(repo, sha, "apps/infrx-api/infrx/worker/preparation.py") is not None
    check("preparation", prep and "PreparationRunner(" in main,
          "infrx/worker/preparation.py and PreparationRunner in the worker root"
          if prep else "no preparation loop: video jobs would never be prepared")
    files = git(repo, "ls-tree", "--name-only", sha, f"{MIGRATIONS}/").stdout.split()
    versions = sorted(Path(f).name[:4] for f in files if re.match(r"\d{4}_", Path(f).name))
    newest = versions[-1] if versions else "0000"
    check("migrations", newest <= applied,
          f"tree carries up to {newest}; hosted has {applied}"
          + (f"; applied beyond the tree: {int(newest) + 1:04d}-{applied} (additive: D's claim)"
             if newest < applied else ""))
    names = schema_names(show(repo, sha, "apps/infrx-api/deploy/preflight.py") or "")
    missing = sorted(set(sets) - names)
    check("config", not missing, "every --set name is in its schema" if not missing
          else f"its install would refuse --set {', '.join(missing)}")
    entry = next((r for r in registry["releases"] if r["sha"] == sha), None)
    absent = [p for p in (entry or {}).get("evidence", []) if not (repo / p).exists()]
    check("record", entry is not None and entry.get("known_good") is True and not absent,
          "not in known-good.json" if entry is None else
          (entry.get("reason") or entry.get("served", "")) + (f"; missing evidence {absent}" if absent else ""))
    if bundles:
        # the coordinator host's shell exports stale AWS_* keys (CLAUDE.md): the profile's are used
        listed = subprocess.run(["env", "-u", "AWS_ACCESS_KEY_ID", "-u", "AWS_SECRET_ACCESS_KEY",
                                 "-u", "AWS_SESSION_TOKEN", "aws", "--region", "us-east-1", "s3",
                                 "ls", f"{bundles}{sha}."], capture_output=True, text=True)
        have = {line.split()[-1] for line in listed.stdout.splitlines() if line.strip()}
        check("bundle", {f"{sha}.bundle", f"{sha}.sha256"} <= have,
              f"{bundles}{sha}.bundle and .sha256" if listed.returncode == 0 else "the prefix could not be listed")
    verdict = "KNOWN-GOOD" if all(c["ok"] for c in checks) else "NOT-KNOWN-GOOD"
    return {"sha": sha, "verdict": verdict, "checks": checks}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("sha", nargs="?")
    ap.add_argument("--list", action="store_true", help="judge every release in the record")
    ap.add_argument("--applied", required=True, help="hosted's newest applied migration, e.g. 0018")
    ap.add_argument("--set", action="append", default=[], metavar="NAME",
                    help="a tunable name the install passes (INFRX_SET's names)")
    ap.add_argument("--bundles", help="s3://bucket/releases/ to confirm the bundle exists")
    ap.add_argument("--repo", type=Path, default=REPO, help=argparse.SUPPRESS)
    ap.add_argument("--registry", type=Path, default=REGISTRY, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if not re.fullmatch(r"\d{4}", a.applied) or not (a.list or a.sha):
        ap.error("--applied NNNN and a sha (or --list)")
    registry = json.loads(a.registry.read_text())
    shas = [r["sha"] for r in registry["releases"]] if a.list else [a.sha]
    results = [judge(sha, a.applied, [s.partition("=")[0] for s in a.set], a.bundles, registry,
                     a.repo) for sha in shas]
    for result in results:
        print(json.dumps(result, indent=1))
    return 0 if all(r["verdict"] == "KNOWN-GOOD" for r in results) or \
        (a.list and any(r["verdict"] == "KNOWN-GOOD" for r in results)) else 1


if __name__ == "__main__":
    sys.exit(main())
