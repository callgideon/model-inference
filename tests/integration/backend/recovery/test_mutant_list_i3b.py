"""R32 for I3B: the mutant list itself is well formed (runs in every layer, no copy made).

A mutant whose text no longer occurs describes code that is gone, and a selector that names
no case can never kill anything; both are caught here, before a long mutation run."""
from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import mutants_i3b                                      # noqa: E402

ROOT = HERE.parents[3]


def test_i3b_mutant_list_is_well_formed():
    ids = [mutant.id for mutant in mutants_i3b.MUTANTS]
    assert len(ids) == len(set(ids))
    assert sum(mutant.must_survive for mutant in mutants_i3b.MUTANTS) >= 1
    for mutant in mutants_i3b.MUTANTS:
        source = (ROOT / mutant.path).read_text()
        assert source.count(mutant.before) == mutant.occurrences, mutant.id
        assert mutant.before != mutant.after, mutant.id
        cases = re.findall(r"^def (test_\w+)", (ROOT / mutant.suite).read_text(), re.M)
        # `bk01f and policies`: the case is the first word, the rest a parametrization id.
        case_name = mutant.select.split(" and ")[0]
        assert any(case_name in case for case in cases), (mutant.id, mutant.select)
