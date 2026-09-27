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
      SELECT, EXPIRY),
    m("n3_content_bound_wrong", "the lineage keeps T3's content bound",
      "(started + timedelta(days=retention.content_days))",
      "(started + timedelta(days=retention.content_days + 1))", SELECT, EXPIRY),
    # --- N3.b/c: denial reaches every version and export, bounded
    m("n3_gate_ignores_tombstones", "a tombstone is permanent at every gate",
      "    return allowed - set(await blocked(", "    return allowed or set(await blocked(",
      REVOKE, EXPIRY),
    m("n3_expiry_not_immediate", "content past its bound is denied at once",
      '        if now >= datetime.fromisoformat(entry["content_until"]):', "        if False:",
      EXPIRY),
    m("n3_reason_lost", "a restricted sample keeps its tombstone's reason",
      '["reason"]\n    for sid in sorted(traced - stones):', '["reason"] and "tombstoned"\n'
      '    for sid in sorted(traced - stones):', EXPIRY),
    m("n3_push_unpaged", "a push tombstones at most `limit` samples per call",
      "    for key in todo[:limit]:", "    for key in todo:", FANOUT),
    m("n3_push_whole_grantor", "a request's push reaches only that request",
      '(f"{request_id}/" if request_id else "")', '""', EXPIRY),
    m("n3_reconcile_grant_unchecked", "reconcile tombstones a grant no longer current",
      "or not grant.is_current(now)) else None", "or False) else None", REVOKE),
    m("n3_reconcile_deletion_unchecked", "reconcile sees a T3 deletion",
      'reason = "deleted" if not rows else', 'reason = "deleted" if False else', EXPIRY),
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
