#!/usr/bin/env python3
"""R32/R40 for lab-sql LW9: single-edit defects of `0058_lab_variant_identities.sql`, each
killed by the named check of `tests/l3sql/test_lw9.py` on a database built from the mutated
set (needs Docker; skips visibly without it). The runner is D7's (`code_mutants_d7.kill`).

    INFRX_D_TASK=l3 uv run --frozen pytest -q tests/d/test_code_mutants_lw9.py
"""
from __future__ import annotations

import pytest

from ..l3sql import test_lw9 as t
from . import code_mutants_d7 as d7
from . import migration_mutants as _d
from . import pgharness

FILE = "0058_lab_variant_identities.sql"
DB = f"{pgharness.DATABASE}_lw9mut"
ROLES = "check_browser_roles_reach_nothing"
IDENT = "check_a_variant_lists_the_identities_r3_stored"
TALLY = "check_the_tally_counts_each_servings_terminal_requests_by_pin"
GRANT = ("grant execute on function infrx.lab_optimization_variant_listing(jsonb),\n"
         "  infrx.lab_release_tally(jsonb), infrx.lab_release_live(jsonb) to infrx_lab_control;")


def _s(name, old, new, check, why, **kw):
    return _d.Mutant(name, FILE, old, new, "lab", check, why, **kw)


MUTANTS = (
    # --- R251: the control login's route reads (a dropped grant: the page answers 503)
    _s("lw9_tally_ungranted", GRANT, GRANT.replace("  infrx.lab_release_tally(jsonb), ", "  "),
       ROLES, "the releases page answers 503 on the unit once a job is assigned"),
    _s("lw9_live_ungranted", GRANT, GRANT.replace(", infrx.lab_release_live(jsonb)", ""), ROLES,
       "the releases page answers 503 on the unit (WR-LIVE-PAGE reads 0054's Live)"),
    _s("lw9_listing_ungranted", GRANT,
       GRANT.replace("infrx.lab_optimization_variant_listing(jsonb),\n  ", ""), ROLES,
       "the optimizations page answers 503 on the unit"),
    _s("lw9_write_granted_to_unit", GRANT,
       GRANT.replace("infrx.lab_release_live(jsonb)",
                     "infrx.lab_release_live(jsonb), infrx.lab_put_variant_identities(jsonb)"),
       ROLES, "the route login rewrites R3's identities (R251: a route never writes them)"),
    _s("lw9_listing_browser", GRANT,
       GRANT + "\ngrant usage on schema infrx to authenticated;\ngrant execute on function "
       "infrx.lab_optimization_variant_listing(jsonb) to authenticated;", ROLES,
       "a browser session lists every provider's variants and identities"),
    # --- WR-LW7-3a: the identities
    _s("lw9_identities_any_provider",
       "\n                  and r.provider_org_id = (p_args->>'provider_org_id')::uuid) then",
       ") then", IDENT, "a provider writes another provider's variant's identities (R227)"),
    _s("lw9_identities_any_record", "where r.ref = v_ref and r.kind = 'variant'",
       "where r.ref = v_ref", IDENT, "a policy or dataset record carries serving identities"),
    _s("lw9_identities_not_idempotent", "    on conflict (variant_ref) do nothing;", "    ;",
       IDENT, "R3 storing a variant twice is a database error"),
    _s("lw9_identities_overwrite_answered", "  if (v_row.base, v_row.variant) is distinct from",
       "  if false and (v_row.base, v_row.variant) is distinct from", IDENT,
       "a second, different identity pair is answered as stored"),
    _s("lw9_rows_mutable", "create or replace trigger lab_variant_identities_immutable before "
       "update or delete", "create or replace trigger lab_variant_identities_immutable before "
       "delete", IDENT, "a stored identity is rewritten after the comparison rested on it"),
    _s("lw9_shape_unchecked",
       "    check (infrx.lab_identity_shaped(base) and infrx.lab_identity_shaped(variant))",
       "    check (true)", IDENT, "the Lab reads an identity it cannot parse: page unavailable"),
    _s("lw9_shape_missing_passes", "!= \"string\")'), false)", "!= \"string\")'), true)",
       IDENT,
       "an identity missing a field (a null check) is stored"),
    _s("lw9_shape_capabilities_any",
       "\n    and not jsonb_path_exists(p, '$.capabilities[*] ? (@.type() != \"string\")')", "",
       IDENT, "a capability that is not a name is stored"),
    _s("lw9_actor_unnamed", "  exception when check_violation or not_null_violation then",
       "  exception when check_violation then", IDENT,
       "an unnamed writer is a database error, not a refusal"),
    _s("lw9_listing_any_identities", "on i.variant_ref = e.doc->>'variant_ref'", "on true",
       IDENT, "a variant shows another variant's identities"),
    _s("lw9_listing_swapped", "'base', i.base, 'variant', i.variant",
       "'base', i.variant, 'variant', i.base", IDENT,
       "the page shows the optimized revision as its own base"),
    _s("lw9_listing_ref_order", "      order by e.n), '[]')", "      order by e.doc->>'variant_ref'),"
       " '[]')", IDENT, "the listing is sorted by ref, not 0055's publication order"),
    # --- WR-C7-TALLY
    _s("lw9_tally_queued_counted",
       "\n             and j.state in ('succeeded', 'failed', 'cancelled', 'expired')", "", TALLY,
       "a queued job is shown as a request (the tally exceeds Live's arms)"),
    _s("lw9_tally_any_revision",
       "           where a.policy_ref = p_args->>'policy_ref'\n             and",
       "           where", TALLY, "another revision's traffic is shown as this release's"),
    _s("lw9_tally_pin_lost", "'pinned_by', s.pinned_by,", "'pinned_by', 'cohort',", TALLY,
       "an explicit pin is shown as cohort traffic"),
    _s("lw9_tally_wrong_count", "count(*) requests", "count(distinct a.serving_ref) requests",
       TALLY, "each serving shows one request whatever it served"),
)
_reason = pgharness.unavailable()


def kill(mutant) -> tuple[str, str]:
    return d7.kill(mutant, DB, t)


def test_the_list_is_well_formed() -> None:
    names = [m.name for m in MUTANTS]
    assert len(set(names)) == len(names), "duplicate mutant names"
    stale = [f"{m.name}: {_d.anchor_count(m)}" for m in MUTANTS
             if _d.anchor_count(m) != m.occurrences]
    assert not stale, f"misdeclared SQL anchors: {stale}"
    assert all(m.check in t.CHECKS for m in MUTANTS)


def test_no_mutant_anchors_in_a_superseded_function_body() -> None:
    found = _d.superseded(MUTANTS)
    assert not found, found


def test_every_case_is_covered_by_a_mutant() -> None:
    """The stores' round trip is the Python ports'; its mutants are tests/l3sql/mutants.py's."""
    uncovered = sorted(set(t.CHECKS) - {m.check for m in MUTANTS}
                       - {"check_the_stores_compose"})
    assert not uncovered, f"cases no mutant can break: {uncovered}"


@pytest.mark.skipif(_reason is not None, reason=f"task-local PostgreSQL unavailable: {_reason}")
@pytest.mark.parametrize("mutant", MUTANTS, ids=lambda m: m.name)
def test_sql_mutant_is_killed(mutant) -> None:
    outcome, detail = kill(mutant)
    assert outcome == _d.KILLED, (f"{mutant.name} was {outcome} by {mutant.check}: {detail}. "
                                  f"In production: {mutant.why}")
    print(f"{mutant.name}: {outcome} -> {detail}")
