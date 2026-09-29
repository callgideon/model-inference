"""R32/R40 for composition batch 6's reads: single-edit defects of
`0053_lab_composition_reads.sql`, each killed by the named check of `test_c6_reads.py` on a
database built from the mutated set (needs Docker). The runner is D7's (`code_mutants_d7.kill`).

    INFRX_D_TASK=r2 uv run --frozen pytest -q tests/d/test_code_mutants_c6.py
"""
from __future__ import annotations

from . import code_mutants_d7 as d7
from . import migration_mutants as _d
from . import pgharness
from . import test_c6_reads as t

FILE = "0053_lab_composition_reads.sql"
DB = f"{pgharness.DATABASE}_c6mut"
ROLES = "check_browser_roles_reach_nothing"
DECISIONS = "check_decisions_are_the_providers_own_oldest_first"


def _s(name, old, new, check, why, **kw):
    return _d.Mutant(name, FILE, old, new, "lab", check, why, **kw)


SQL_MUTANTS = (
    _s("c6_browser_reads_decisions", "     and e.decision is not null\n$$;\n",
       "     and e.decision is not null\n$$;\ngrant usage on schema infrx to authenticated;\n"
       "grant execute on function infrx.lab_release_decisions(jsonb) to authenticated;\n",
       ROLES, "a browser session reads every provider's rollout decisions"),
    _s("c6_decisions_any_provider",
       "   where o.provider_org_id = (p_args->>'provider_org_id')::uuid\n",
       "   where true\n", DECISIONS,
       "the release page shows another provider's decisions"),
    _s("c6_decisions_with_starts", "     and e.decision is not null\n", "", DECISIONS,
       "a launch reads as a decision on the release page"),
    _s("c6_decisions_newest_first", "order by e.at, e.policy_id, e.fence",
       "order by e.at desc, e.policy_id, e.fence", DECISIONS,
       "the decision history reads backwards"),
    _s("c6_decisions_reasons_lost", "'reasons', to_jsonb(e.reasons),", "'reasons', '[]'::jsonb,",
       DECISIONS, "a rollback shows no reason"),
)


def kill(mutant) -> tuple[str, str]:
    return d7.kill(mutant, DB, t)
