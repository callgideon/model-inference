"""I3: infra/app/rollback.py - the App rollback-target rule, offline. Layer 1 (no docker).

Each case builds a throwaway git repository whose commits are App trees (migrations,
identity files, the pinned contract) and a gateway tree, then runs the tool as the operator
does. The rule: the candidate's newest migration == hosted's applied one (below it only with a
schema proof reaching it, as infra/rollout/known-good.py) AND its pinned contract <= the running
gateway's AND it has I2A's release identity.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
TOOL = REPO / "infra" / "app" / "rollback.py"
APP_CONTRACT = "apps/app/lib/contracts/v2/money-units.ts"
GATEWAY_CONTRACT = "apps/infrx-api/infrx/contracts/v2/__init__.py"


def sh(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


def commit(repo: Path, files: dict[str, str | None], message: str) -> str:
    for path, text in files.items():
        target = repo / path
        if text is None:
            target.unlink()
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    sh(repo, "add", "-A")
    sh(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", message)
    return sh(repo, "rev-parse", "HEAD")


def migrations(*numbers: str) -> dict[str, str]:
    return {f"apps/app/supabase/migrations/{n}_m.sql": "select 1;\n" for n in numbers}


IDENTITY = {"apps/app/instrumentation.ts": "export async function register() {}\n",
            "apps/app/app/api/version/route.ts": "export function GET() {}\n"}


def app_contract(minor: int) -> dict[str, str]:
    return {APP_CONTRACT: f'export const SURFACE_VERSION = "contracts-v2.{minor}";\n'}


def gateway_contract(minor: int) -> dict[str, str]:
    return {GATEWAY_CONTRACT: f'SURFACE_VERSION = "contracts-v2.{minor}"\n'}


@pytest.fixture(scope="module")
def trees(tmp_path_factory):
    repo = tmp_path_factory.mktemp("apprepo")
    sh(repo, "init", "-q")
    t = {"pre": commit(repo, {**migrations("0001", "0002"), **app_contract(1)}, "pre-I2A App")}
    t["ok"] = commit(repo, {**migrations("0003"), **IDENTITY, **gateway_contract(1)}, "App at 0003")
    t["gw_old"] = commit(repo, gateway_contract(0), "gateway serving v2.0")
    t["gw"] = commit(repo, gateway_contract(1), "gateway serving v2.1")
    t["new"] = commit(repo, migrations("0004"), "App at 0004")
    t["v22"] = commit(repo, app_contract(2), "App pinning v2.2")
    return repo, t


def run(repo: Path, *args: str, tool: Path = TOOL) -> tuple[int, dict | None, str]:
    done = subprocess.run([sys.executable, str(tool), *args, "--repo", str(repo)],
                          capture_output=True, text=True)
    try:
        return done.returncode, json.loads(done.stdout), done.stderr
    except json.JSONDecodeError:
        return done.returncode, None, done.stderr


def failed(result: dict) -> set[str]:
    return {c["check"] for c in result["checks"] if not c["ok"]}


def test_i3_rb01_compatible_at_the_applied_migration(trees):
    """Catches: a tool that refuses the exact applied number, and one that loses the identity it
    prints."""
    repo, t = trees
    code, result, _ = run(repo, t["ok"], "--applied", "0003", "--gateway", t["gw"])
    assert code == 0 and result["verdict"] == "COMPATIBLE", result
    assert result["expected_version_commit"] == t["ok"] and len(t["ok"]) == 40
    assert result["migrations"] == ["0001", "0002", "0003"]


def test_i3_rb02_a_newer_migration_than_hosted_is_refused(trees):
    """Catches: rolling back (or deploying) an App whose queries need a migration hosted lacks."""
    repo, t = trees
    code, result, _ = run(repo, t["new"], "--applied", "0003", "--gateway", t["gw"])
    assert code == 1 and failed(result) == {"migrations"}, result
    assert "0004 not applied" in json.dumps(result)


def test_i3_rb03_a_pre_identity_tree_is_refused(trees):
    """Catches: a target that cannot report its commit on /api/version (pre-I2A)."""
    repo, t = trees
    code, result, _ = run(repo, t["pre"], "--applied", "0009", "--gateway", t["gw"])
    assert code == 1 and "identity" in failed(result), result


def test_i3_rb04_a_contract_newer_than_the_running_gateway_is_refused(trees):
    """Catches: an App pinned to a contract surface the running gateway does not serve (after a
    backend rollback), and a gateway declaring none; an equal surface passes (rb01)."""
    repo, t = trees
    code, result, _ = run(repo, t["ok"], "--applied", "0003", "--gateway", t["gw_old"])
    assert code == 1 and failed(result) == {"contract"}, result
    code, result, _ = run(repo, t["v22"], "--applied", "0004", "--gateway", t["gw"])
    assert code == 1 and failed(result) == {"contract"}, result
    code, result, _ = run(repo, t["ok"], "--applied", "0003", "--gateway", t["pre"])
    assert code == 1 and "none" in json.dumps(result), result


@pytest.mark.parametrize("args, code", [
    (["{ok}", "--applied", "18", "--gateway", "{gw}"], 2),
    (["{ok}", "--applied", "0009", "--gateway", "{gw}", "--schema-proof", "9"], 2),
    (["{ok}", "--applied", "abcd", "--gateway", "{gw}"], 2),
    (["{ok}", "--gateway", "{gw}"], 2),
    (["{ok}", "--applied", "0003"], 2),
    (["no-such-ref", "--applied", "0003", "--gateway", "{gw}"], 1),
    (["{ok}", "--applied", "0003", "--gateway", "no-such-ref"], 1),
])
def test_i3_rb05_malformed_inputs_are_refused(trees, args, code):
    """Catches: a guessed applied number or an unknown ref read as compatible."""
    repo, t = trees
    got, result, _ = run(repo, *[a.format(**t) for a in args])
    assert got == code, (args, result)
    if result is not None:
        assert result["verdict"] == "REFUSED"


@pytest.mark.parametrize("line, mutant, applied, correct", [
    # the equality dropped: rb01's exact-applied case would refuse
    ("newest == applied or (newest < applied and proven)", "newest < applied and proven", "0003", 0),
    # additivity assumed again (review 1-I3R-4): rb08's hosted-ahead case would pass unproven
    ("newest == applied or (newest < applied and proven)", "newest <= applied", "0009", 1),
])
def test_i3_rb06_the_comparison_mutants_are_caught(trees, tmp_path, line, mutant, applied, correct):
    """The mutations of the migration rule: under each, the case that pins it flips, i.e. the
    suite (rb01, rb08) kills the mutant."""
    source = TOOL.read_text()
    assert source.count(line) == 1
    patched = tmp_path / "rollback.py"
    patched.write_text(source.replace(line, mutant))
    repo, t = trees
    code, _, _ = run(repo, t["ok"], "--applied", applied, "--gateway", t["gw"], tool=patched)
    got, _, _ = run(repo, t["ok"], "--applied", applied, "--gateway", t["gw"])
    assert got == correct and code != correct, "the mutant survived"

@pytest.mark.parametrize("app, gateway, ok", [
    ((2, 1), (2, 1), True), ((2, 0), (2, 1), True), ((2, 1), (2, 0), False),
    ((2, 1), (3, 0), False),   # review I3R-5: (2, 1) <= (3, 0) as a tuple passed
    ((3, 0), (2, 9), False), ((2, 9), (3, 1), False),
])
def test_i3_rb09_the_contract_needs_the_same_major(tmp_path, app, gateway, ok):
    """Catches (I3R-5): a (MAJOR, MINOR) tuple compare, which passes an App pinned to v2.1 against
    a gateway serving v3.0; the MAJOR must match and the App's MINOR be <= the gateway's."""
    repo = tmp_path
    sh(repo, "init", "-q")
    target = commit(repo, {**migrations("0001"), **IDENTITY,
                           APP_CONTRACT: f'export const SURFACE_VERSION = "contracts-v{app[0]}.{app[1]}";\n'}, "App")
    serving = commit(repo, {GATEWAY_CONTRACT: f'SURFACE_VERSION = "contracts-v{gateway[0]}.{gateway[1]}"\n'}, "gateway")
    code, result, _ = run(repo, target, "--applied", "0001", "--gateway", serving)
    assert (code, result["verdict"]) == ((0, "COMPATIBLE") if ok else (1, "REFUSED")), result
    assert failed(result) == (set() if ok else {"contract"}), result



def test_i3_rb07_this_repository_judges_itself():
    """The tool on the real tree: HEAD against its own newest migration and its own gateway."""
    newest = sorted(p.name[:4] for p in (REPO / "apps/app/supabase/migrations").glob("[0-9]*.sql"))[-1]
    code, result, _ = run(REPO, "HEAD", "--applied", newest, "--gateway", "HEAD")
    assert code == 0, result


def test_i3_rb08_hosted_ahead_of_the_tree_needs_a_schema_proof(trees):
    """Catches (review 1-I3R-4): a hosted schema ahead of the target accepted on the assumption
    that migrations are additive. They are not all additive (0021_read_authority.sql revokes
    column grants and drops a policy), so, as infra/rollout/known-good.py, hosted ahead passes
    only with a proof reaching --applied whose evidence exists in this checkout."""
    repo, t = trees
    (repo / "proof.md").write_text("the tree's console tests on a database migrated through 0009\n")
    base = [t["ok"], "--applied", "0009", "--gateway", t["gw"]]
    code, result, _ = run(repo, *base)
    assert code == 1 and failed(result) == {"migrations"}, result
    assert "0004-0009" in json.dumps(result) and "--schema-proof" in json.dumps(result)
    code, result, _ = run(repo, *base, "--schema-proof", "0009", "--evidence", "proof.md")
    assert code == 0 and result["verdict"] == "COMPATIBLE", result
    assert "proof.md" in json.dumps(result)
    for proof in (["--schema-proof", "0008", "--evidence", "proof.md"],      # does not reach 0009
                  ["--schema-proof", "0009"],                                # no evidence
                  ["--schema-proof", "0009", "--evidence", "missing.md"],    # evidence absent
                  ["--schema-proof", "0009", "--evidence", str(TOOL)],       # not in this checkout
                  ["--evidence", "proof.md"]):                               # no proof number
        code, result, _ = run(repo, *base, *proof)
        assert code == 1 and failed(result) == {"migrations"}, (proof, result)
