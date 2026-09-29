#!/usr/bin/env python3
"""R32/R40 for lab-sql LW7: single-edit defects of `0055_lab_variants_requeue.sql`, each
killed by the named check of `tests/l3sql/test_lw7.py` on a database built from the mutated
set (needs Docker; skips visibly without it). The runner is D7's (`code_mutants_d7.kill`).

    INFRX_D_TASK=l3 uv run --frozen pytest -q tests/d/test_code_mutants_lw7.py
"""
from __future__ import annotations

import pytest

from ..l3sql import test_lw7 as t
from . import code_mutants_d7 as d7
from . import migration_mutants as _d
from . import pgharness

FILE = "0055_lab_variants_requeue.sql"
DB = f"{pgharness.DATABASE}_lw7mut"
ROLES = "check_browser_roles_reach_nothing"
VARIANTS = "check_variants_are_the_providers_own_with_their_latest_comparison"
REQUEUE = "check_a_failed_import_is_requeued_as_a_new_job_naming_its_predecessor"
ONLY_FAILED = "check_only_a_failed_job_is_requeued"
BROWSER = "grant usage on schema infrx to authenticated;\ngrant execute on function infrx.{}(jsonb) " \
    "to authenticated;\n"


def _s(name, old, new, check, why, **kw):
    return _d.Mutant(name, FILE, old, new, "lab", check, why, **kw)


MUTANTS = (
    # --- WR-C6-VARIANTS
    _s("lw7_variants_browser", "     and r.provider_org_id = (p_args->>'provider_org_id')::uuid\n$$;\n",
       "     and r.provider_org_id = (p_args->>'provider_org_id')::uuid\n$$;\n"
       + BROWSER.format("lab_optimization_variants"), ROLES,
       "a browser session lists every provider's optimization variants"),
    _s("lw7_variants_any_provider",
       "   where r.kind = 'variant'\n     and r.provider_org_id = (p_args->>'provider_org_id')::uuid\n",
       "   where r.kind = 'variant'\n", VARIANTS,
       "the optimizations page shows another provider's variants (R227)"),
    _s("lw7_variants_other_records", "   where r.kind = 'variant'\n", "   where true\n", VARIANTS,
       "datasets and runs are listed as optimization variants"),
    _s("lw7_variants_newest_first", "order by r.published_at, r.ref)",
       "order by r.published_at desc, r.ref)", VARIANTS, "the variant list reads backwards"),
    _s("lw7_variants_ref_order", "order by r.published_at, r.ref)", "order by r.ref)", VARIANTS,
       "the variant list is sorted by ref, not oldest first (T2)"),
    _s("lw7_variants_identities_swapped",
       "'base_serving_ref', r.body::jsonb->>'base_serving_ref',",
       "'base_serving_ref', r.body::jsonb->>'variant_serving_ref',", VARIANTS,
       "the page shows the optimized revision as its own source"),
    _s("lw7_variants_oldest_comparison", "order by v.stored_at desc,", "order by v.stored_at,",
       VARIANTS, "a superseded comparison's verdict is shown as the variant's"),
    _s("lw7_variants_any_comparison", "       where v.variant_ref = r.ref\n",
       "       where true\n", VARIANTS, "a variant shows another variant's comparison"),
    _s("lw7_variants_digest_lost",
       " || jsonb_build_object('comparison_digest', v.comparison_digest) doc", " doc", VARIANTS,
       "a comparison is shown without the digest that names its evidence"),
    # --- WR-C6-REQUEUE
    _s("lw7_requeue_browser", "  return infrx.lab_import_job_json(j) || jsonb_build_object("
       "'requeued_from', j.requeued_from);\nend $$;\n",
       "  return infrx.lab_import_job_json(j) || jsonb_build_object("
       "'requeued_from', j.requeued_from);\nend $$;\n" + BROWSER.format("lab_import_requeue"),
       ROLES, "a browser session requeues any provider's imports"),
    _s("lw7_control_unit_ungranted",
       "\n\ngrant execute on function infrx.lab_optimization_variants(jsonb),\n"
       "  infrx.lab_import_requeue(jsonb) to infrx_lab_control;\n", "\n", ROLES,
       "the box's /lab/v1/* unit answers 503 on optimizations and 'import again' "
       "(LW7-SCOPE-1)"),
    _s("lw7_requeue_any_provider",
       "  select * into j from infrx.lab_import_jobs where job_id = (p_args->>'job_id')::uuid\n"
       "     and provider_org_id = (p_args->>'provider_org_id')::uuid;\n",
       "  select * into j from infrx.lab_import_jobs where job_id = (p_args->>'job_id')::uuid;\n",
       REQUEUE, "a provider requeues another provider's import"),
    _s("lw7_requeue_any_state", "  if j.state <> 'failed' then\n", "  if false then\n",
       ONLY_FAILED, "a running or published import is imported a second time"),
    _s("lw7_requeue_state_unnamed", "'is ' || j.state);", "'is not failed');", ONLY_FAILED,
       "the refusal does not say what the import is doing"),
    _s("lw7_requeue_only_queued_named", "'is ' || j.state);",
       "'is ' || case j.state when 'queued' then j.state else 'not failed' end);", ONLY_FAILED,
       "a running or published import's refusal does not name its state (T1)"),
    _s("lw7_requeue_rejected_requeued", "  if j.error = 'rejected' then ", "  if false then ",
       ONLY_FAILED, "a rejected import is requeued and its same rows reject again (R3)"),
    _s("lw7_requeue_predecessor_lost", "            j.job_id)\n    on conflict",
       "            null)\n    on conflict", REQUEUE,
       "the new job does not record the failed one it replaces"),
    _s("lw7_requeue_actor_kept", "jsonb_set(j.spec, '{actor}', to_jsonb(p_args->>'actor'))",
       "j.spec", REQUEUE, "the import is published as the original uploader, not the requeuer"),
    _s("lw7_requeue_creator_kept", "to_jsonb(p_args->>'actor')), p_args->>'actor',",
       "to_jsonb(p_args->>'actor')), j.created_by,", REQUEUE,
       "the audit names the original uploader as the requeuer"),
    _s("lw7_requeue_not_idempotent", "    on conflict do nothing;", "    ;", REQUEUE,
       "a double click on 'import again' is a database error"),
    _s("lw7_requeue_taken_id_answered",
       "  if not found then\n    perform infrx.refuse('state_conflict', 'that new job id",
       "  if false then\n    perform infrx.refuse('state_conflict', 'that new job id",
       ONLY_FAILED, "a requeue answers an unrelated job as its successor"),
    _s("lw7_requeue_actor_unnamed",
       "exception when check_violation or not_null_violation or invalid_text_representation "
       "then\n    perform infrx.refuse('invalid_request', 'a requeue",
       "exception when check_violation or invalid_text_representation "
       "then\n    perform infrx.refuse('invalid_request', 'a requeue",
       ONLY_FAILED, "a requeue without a named actor is a database error, not a refusal"),
    _s("lw7_requeue_successor_unmarked",
       " || jsonb_build_object('requeued_from', j.requeued_from);", ";", REQUEUE,
       "the answer does not say which import it replaces"),
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
    """The store round trip is the Python port's; its mutants are the route lists'."""
    uncovered = sorted(set(t.CHECKS) - {m.check for m in MUTANTS}
                       - {"check_the_requeue_store_composes"})
    assert not uncovered, f"cases no mutant can break: {uncovered}"


@pytest.mark.skipif(_reason is not None, reason=f"task-local PostgreSQL unavailable: {_reason}")
@pytest.mark.parametrize("mutant", MUTANTS, ids=lambda m: m.name)
def test_sql_mutant_is_killed(mutant) -> None:
    outcome, detail = kill(mutant)
    assert outcome == _d.KILLED, (f"{mutant.name} was {outcome} by {mutant.check}: {detail}. "
                                  f"In production: {mutant.why}")
    print(f"{mutant.name}: {outcome} -> {detail}")
