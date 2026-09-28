#!/usr/bin/env python3
"""R32/R40/R83 for N3: one single-edit defect per decision `test_lineage.py` claims, through
the shared runner with `require_every_case` (every named case must notice, nothing else may
fail). The PostgreSQL rerun (`test_lineage_pg.py`) is outside the runner (T2I/G8's pattern).

    uv run --frozen pytest -q tests/n/lineage/test_mutants.py                     # subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/n/lineage/test_mutants.py   # all
"""
from __future__ import annotations

import ast
import pathlib
import sys

API_DIR = pathlib.Path(__file__).resolve().parents[3]
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from tests.contracts import mutants as shared  # noqa: E402
from tests.contracts.mutants import Mutant, Outcome, Result, Runner  # noqa: E402,F401

SUITE = "tests/n/lineage/test_lineage.py"
P = "datasets/lineage/__init__.py"
V = "datasets/versions/__init__.py"

SELECT = "test_n3_permitted_traces_become_a_dataset_with_lineage_per_sample"
FEEDBACK = "test_n3_feedback_joins_only_under_a_feedback_grant"
PORTS = "test_n3_selection_goes_through_l2_t3_and_c2_only"
FANOUT = "test_n3_the_fan_out_is_bounded"
REVOKE = "test_n3_revocation_tombstones_every_derived_version_and_export"
EXPIRY = "test_n3_deletion_and_expiry_deny_at_once_and_purge_after_retention"
MODEL_SCOPE = "test_n3_a_trace_of_another_model_is_omitted_before_c2"
NARROW = "test_n3_a_narrowed_grant_version_tombstones_its_trace_samples"
CLOCK = "test_n3_a_bound_passed_on_either_clock_denies"
BACKFILL = "test_n3_backfill_moves_the_object_restrictions_into_d7_once"
COMPAT = "test_n3_callers_without_the_port_still_deny_for_good"


def m(name, invariant, old, new, *cases, file=P, dies_by=(), occurrences=1):
    return Mutant(name=name, invariant=invariant, file=file, old=old, new=new, cases=cases,
                  dies_by=dies_by, occurrences=occurrences)


MUTANTS: tuple[Mutant, ...] = (
    # --- N3.a: selection through L2, T3 and C2 only (DATA-RIGHTS)
    m("n3_fanout_unbounded", "a selection is 1..MAX_SELECT distinct requests",
      "if not 1 <= len(request_ids) <= MAX_SELECT or len(set(request_ids)) != len(request_ids):",
      "if not request_ids:", FANOUT),
    m("n3_not_acting", "the caller acts for the provider (WR-N-2) before anything",
      "    await acting_provider(access, user_id, provider_org_id)\n", "", PORTS),
    m("n3_response_unchecked", "the grant must name the response content too",
      "    await access.authorize_content(category=DataCategory.response_content, **gate)\n",
      "", PORTS),
    m("n3_feedback_ignores_category", "feedback joins only under a feedback grant",
      "    except errors.Forbidden:\n        joined = False",
      "    except errors.Forbidden:\n        joined = True", FEEDBACK),
    m("n3_projection_optional", "a missing projection never substitutes for permission",
      "        if not rows:\n            omitted.append", "        if False:\n            omitted.append",
      PORTS, dies_by=("ValueError",)),
    m("n3_model_unchecked", "a trace of a model the grant does not name is omitted before C2",
      "        if {await model_of(r) for r in rows} != {model_id}:", "        if False:",
      MODEL_SCOPE),
    m("n3_expiry_left_to_c2", "expired content is omitted before C2 is asked",
      "        if not retention.content_live(started, now):", "        if False:", PORTS),
    m("n3_c2_outage_swallowed", "a C2 outage publishes nothing (it is not a refusal)",
      "        except (errors.NotFound, errors.Forbidden, errors.Gone):",
      "        except Exception:", PORTS),
    m("n3_empty_selection_registers", "nothing permitted registers and publishes nothing",
      "    if not samples:\n        raise", "    if False:\n        raise", PORTS),
    m("n3_corrections_dropped", "feedback is appended as corrections",
      '            "corrections": corrections})', '            "corrections": []})', SELECT),
    m("n3_author_identity_kept", "corrections carry no author identity",
      '                        if k in ("feedback_id", "name", "value", "comment", "author_role",',
      '                        if k or ("feedback_id", "name", "value", "comment", "author_role",',
      SELECT),
    m("n3_span_lost", "the sample keeps the trace's own time span",
      '"started_at": started.isoformat(),', '"started_at": now.isoformat(),', SELECT),
    m("n3_group_not_request", "a trace is its own family (group = request)",
      '"group_key": request})', '"group_key": digest})', SELECT),
    m("n3_no_lineage_entry", "every sample has a trace lineage entry",
      'f"{base}/samples/{sid}.json", lab.canonical({', 'f"{base}/unindexed/{sid}.json", lab.canonical({',
      SELECT, EXPIRY, dies_by=("TypeError",)),   # the push stones from the entry (fix round)
    m("n3_content_bound_wrong", "the lineage keeps T3's content bound",
      "bounds[sid] = started + timedelta(days=retention.content_days)",
      "bounds[sid] = started + timedelta(days=retention.content_days + 1)", SELECT, EXPIRY),
    m("n3_bound_unrecorded", "the content bound is D7's (0041, WR-N3-5)",
      "        await restrictions.bound(bounds, provider_org_id=provider_org_id)\n",
      "        pass\n",
      SELECT, CLOCK),
    # --- N3.b/c: denial reaches every version and export, bounded
    m("n3_gate_ignores_tombstones", "the gate reads 0041: a tombstone is permanent there",
      "else restrictions.permitted)(", "else store.accessible_samples)(", CLOCK),
    m("n3_gate_skips_denial", "the gate subtracts every denial, the caller's clock included",
      "    return allowed - set(await blocked(objects, provider_org_id=provider_org_id,\n"
      "                                       sample_ids=allowed, now=now))",
      "    return allowed", CLOCK, BACKFILL, COMPAT),
    m("n3_expiry_not_immediate", "content past its bound is denied at once",
      '        if now >= datetime.fromisoformat(entry["content_until"]):', "        if False:",
      CLOCK),
    m("n3_status_ignores_caller_clock", "the status view explains a bound the caller passed",
      "    return {**await _expired(objects, provider_org_id, wanted - set(out), now), **out}",
      "    return out", CLOCK),
    m("n3_reason_lost", "a restricted sample keeps its tombstone's reason",
      "        s: r for s, r in (await restrictions.blocked(",
      '        s: "tombstoned" for s, r in (await restrictions.blocked(', CLOCK),
    m("n3_evidence_unshipped", "export evidence names only the items that export delivered",
      "dataset_ref, provider_org_id=provider_org_id)).items() if s in wanted}",
      "dataset_ref, provider_org_id=provider_org_id)).items()}", REVOKE),
    m("n3_push_unpaged", "a push tombstones at most `limit` samples per call",
      "    for key in todo[:limit]:", "    for key in todo:", FANOUT),
    m("n3_push_stops_at_done", "a push skips the samples already tombstoned",
      "if _id(k) not in done]", "]", FANOUT),
    m("n3_push_port_dropped", "a push given the port writes 0041 too",
      "            restrictions=restrictions)\n    return {\"tombstoned\"",
      ")\n    return {\"tombstoned\"", EXPIRY),
    m("n3_push_whole_grantor", "a request's push reaches only that request",
      '(f"{request_id}/" if request_id else "")', '""', EXPIRY),
    m("n3_reconcile_grant_unchecked", "reconcile tombstones a grant no longer current",
      "or not grant.is_current(now)) else", "or False) else", REVOKE),
    m("n3_narrow_model_ignored", "a version dropping the model tombstones its samples",
      'entry["model_id"] not in grant.model_ids\n                    or ', "", NARROW),
    m("n3_narrow_categories_ignored", "a version dropping a category tombstones its samples",
      'or not set(entry["categories"]) <= set(grant.categories)) else None',
      "or False) else None", NARROW),
    m("n3_feedback_category_always", "only a sample carrying corrections needs feedback",
      '+ (["feedback"] if corrections else [])', '+ ["feedback"]', NARROW),
    m("n3_reconcile_deletion_unchecked", "reconcile sees a T3 deletion",
      'reason = "deleted" if not rows else', 'reason = "deleted" if False else', EXPIRY),
    m("n3_stone_skips_0041", "a tombstone given the port is written to 0041",
      "    return bool(restrictions is not None and await restrictions.tombstone(",
      "    return bool(False and await restrictions.tombstone(", REVOKE, COMPAT),
    m("n3_stone_skips_object", "every tombstone keeps its write-once object record",
      "    wrote = await objects.put_if_absent(", "    wrote = False and await objects.put_if_absent(",
      COMPAT),
    m("n3_reconcile_port_dropped", "reconcile writes its tombstones to 0041",
      "        if reason and await _stone(objects, provider_org_id, entry, reason, clock,\n"
      "                                   restrictions=restrictions):",
      "        if reason and await _stone(objects, provider_org_id, entry, reason, clock):",
      REVOKE, COMPAT),
    m("n3_reconcile_port_unresolved", "reconcile without the port reaches 0041 by its directory",
      "        restrictions = restrictions_of(directory)\n", "        pass\n", COMPAT),
    m("n3_portless_store_refused", "a store with neither port nor connection gates by objects",
      "    return None if connect is None else PgSampleRestrictions(connect)",
      "    return PgSampleRestrictions(connect)", COMPAT, dies_by=("TypeError",)),
    m("n3_gate_ignores_object_stones", "an object tombstone denies (a caller without the port)",
      "    out.update(await _stones(objects, provider_org_id, wanted))\n", "", COMPAT),
    m("n3_reconcile_reports_old_stones", "reconcile reports only the samples it stoned",
      "        [entry[\"sample_id\"]], provider_org_id=provider, reason=reason)) or wrote",
      "        [entry[\"sample_id\"]], provider_org_id=provider, reason=reason)) or True",
      NARROW),
    m("n3_purge_on_revocation", "a revocation is logical; only retention purges copies",
      "if reason in PURGED_BY_RETENTION and", "if reason and", REVOKE),
    m("n3_no_purge", "deletion and expiry purge the sample copy after the tombstone",
      "            await objects.delete(copy)\n", "            pass\n", EXPIRY),
    m("n3_reconcile_unpaged", "reconcile works one page of `limit`",
      "page, report = keys[:limit],", "page, report = keys,", FANOUT),
    m("n3_cursor_ignored", "reconcile resumes after its cursor",
      "            if after is None or k > after]", "            ]", FANOUT),
    m("n3_status_grant_unchecked", "the status view explains a revoked grant",
      'None if s.sample_id in readable else "grant_not_current")', "None)", REVOKE),
    m("n3_recall_claimed", "evidence never claims a recall",
      '"recalled": False,', '"recalled": True,', REVOKE),
    # --- WR-N3-5: the one-shot move of the object-era restrictions
    m("n3_backfill_skips_stones", "backfill moves every object tombstone",
      "    moved = [s for reason, ids in sorted(reasons.items())",
      "    moved = [s for reason, ids in sorted({}.items())", BACKFILL),
    m("n3_backfill_reason_lost", "a moved tombstone keeps its object's reason",
      "        ids, provider_org_id=provider_org_id, reason=reason)]",
      '        ids, provider_org_id=provider_org_id, reason="deleted")]', BACKFILL),
    m("n3_backfill_skips_bounds", "backfill moves every trace entry's bound",
      "    await restrictions.bound(bounds, provider_org_id=provider_org_id)\n    return",
      "    return", BACKFILL),
    # --- the N2 reads go through the N3 gate
    m("n3_derive_ungated", "derivation omits tombstoned samples",
      "        readable = await lineage.permitted(store, objects, ref, provider_org_id=provider_org_id,\n"
      '                                           purpose="provider_sharing",\n'
      "                                           now=now or datetime.now(UTC))\n",
      "        readable = set(await store.accessible_samples(ref, provider_org_id=provider_org_id,\n"
      '                                                      purpose="provider_sharing"))\n',
      REVOKE, file=V),
    m("n3_export_ungated", "an export omits tombstoned samples",
      '    allowed = await lineage.permitted(store, objects, dataset_ref,\n'
      '                                      provider_org_id=provider_org_id, purpose="training", now=now)\n'
      "    where =",
      '    allowed = set(await store.accessible_samples(dataset_ref, provider_org_id=provider_org_id,\n'
      '                                                 purpose="training"))\n    where =',
      REVOKE, file=V),
    m("n3_part_ungated", "a part read drops tombstoned samples",
      '    allowed = await lineage.permitted(store, objects, record["dataset_ref"],\n'
      '                                      provider_org_id=provider_org_id, purpose="training", now=now)\n'
      "    return",
      '    allowed = set(await store.accessible_samples(record["dataset_ref"],\n'
      '                                                 provider_org_id=provider_org_id,\n'
      '                                                 purpose="training"))\n    return',
      REVOKE, file=V),
)


def case_names() -> set[str]:
    tree = ast.parse((API_DIR / SUITE).read_text())
    return {node.name for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")}


RUNNER = Runner(name="n3", targets=(SUITE,), require_every_case=True)


def run_mutant(mutant: Mutant) -> Result:
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run the N3 lineage mutation list"))
