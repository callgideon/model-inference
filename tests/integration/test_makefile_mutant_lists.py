"""DT-11 (W6 makefile-pins): the Makefile's mutant targets are hand-maintained lists, so an
omitted or duplicated list was invisible (DT-10 found tests/h/test_mutants.py run twice and
tests/integration/lab_local/test_mutants.py run nowhere). This pins them to the tree: every
Python mutant runner (`test_*mutants*.py` under apps/infrx-api/tests and tests/integration) is
named exactly once by `api-mutants`, every Lab runner (`run-*mutants.mjs` under apps/lab/tests)
exactly once by `lab-mutants`, and every path either target names exists - each resolved
against the directory its recipe segment `cd`s into.
"""
from __future__ import annotations

import os
import re
from collections import Counter
from pathlib import Path

# The real checkout, also when mutants.py runs this file from a copy (harness.REPO_ROOT's rule).
REPO = Path(os.environ.get("INFRX_E2_REPO_ROOT") or Path(__file__).resolve().parents[2])
CD = {"$(API)": "apps/infrx-api", "$(CURDIR)": ".", "apps/lab": "apps/lab"}
RUNNERS = {
    "api-mutants": [(base, "test_*mutants*.py") for base in ("apps/infrx-api/tests",
                                                             "tests/integration")],
    "lab-mutants": [("apps/lab/tests", "run-*mutants.mjs")],
}


def named(makefile: str, target: str) -> list[str]:
    """Repo-relative paths of the test files the target's recipe names, one per mention."""
    recipe = re.search(rf"^{re.escape(target)}:.*\n((?:\t.*\n?)*)", makefile, re.M)
    assert recipe, f"no {target} recipe"
    paths = []
    for line in recipe.group(1).splitlines():
        if line.strip().startswith("#"):
            continue
        cwd = "."
        for segment in line.split("&&"):
            move = re.match(r"\s*cd (\S+)\s*$", segment)
            if move:
                cwd = CD[move.group(1)]
            paths += [os.path.normpath(f"{cwd}/{p}")
                      for p in re.findall(r"(?<!\S)(tests/\S+\.(?:py|mjs))", segment)]
    return paths


def problems(makefile: str) -> list[str]:
    found = []
    for target, globs in RUNNERS.items():
        mentions = Counter(named(makefile, target))
        found += [f"{target} names {p}, which does not exist"
                  for p in mentions if not (REPO / p).is_file()]
        for base, pattern in globs:
            for runner in sorted((REPO / base).rglob(pattern)):
                rel = runner.relative_to(REPO).as_posix()
                if mentions[rel] != 1:
                    found.append(f"{target} names {rel} {mentions[rel]} times, not once")
    return found


def makefile() -> str:
    return (REPO / "Makefile").read_text()


def test_every_mutant_runner_is_named_exactly_once():
    """Red on 2add8e0a: tests/h/test_mutants.py twice, lab_local's list never."""
    assert problems(makefile()) == []


def test_a_duplicated_runner_is_reported():
    text = makefile().replace("tests/q/test_mutants.py", "tests/q/test_mutants.py "
                              "tests/q/test_mutants.py", 1)
    assert problems(text) == ["api-mutants names apps/infrx-api/tests/q/test_mutants.py "
                              "2 times, not once"]


def test_an_omitted_runner_is_reported_for_both_targets():
    text = makefile().replace(" tests/q/test_mutants.py", "", 1).replace(
        " && node tests/l/ui/run-mutants.mjs", "", 1)
    assert problems(text) == [
        "api-mutants names apps/infrx-api/tests/q/test_mutants.py 0 times, not once",
        "lab-mutants names apps/lab/tests/l/ui/run-mutants.mjs 0 times, not once"]


def test_a_path_is_resolved_against_its_segments_cd():
    """An integration list named after `cd $(API)` would resolve under apps/infrx-api and
    run nothing: it is reported missing, and the real one omitted."""
    text = makefile().replace("cd $(CURDIR) && INFRX_MUTANTS=all $(API)/.venv/bin/python -m "
                              "pytest -q -p no:cacheprovider tests/integration/lab_improve",
                              "cd $(API) && INFRX_MUTANTS=all .venv/bin/python -m "
                              "pytest -q -p no:cacheprovider tests/integration/lab_improve", 1)
    assert problems(text) == [
        "api-mutants names apps/infrx-api/tests/integration/lab_improve/test_mutants.py, "
        "which does not exist",
        "api-mutants names tests/integration/lab_improve/test_mutants.py 0 times, not once"]


def test_a_recipe_comment_names_nothing():
    text = makefile().replace("\tcd apps/lab && node tests/b/run-mutants.mjs\n",
                              "\t# cd apps/lab && node tests/b/run-mutants.mjs\n", 1)
    assert problems(text) == ["lab-mutants names apps/lab/tests/b/run-mutants.mjs 0 times, "
                              "not once"]
