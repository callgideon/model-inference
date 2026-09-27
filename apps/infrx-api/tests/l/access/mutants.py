#!/usr/bin/env python3
"""R32/R83 for L2: one single-edit defect per decision `tests/l/access` claims, through the
shared runner (`tests/contracts/mutants.py`).

    INFRX_MUTANTS=all uv run --frozen pytest -q tests/l/access/test_mutants.py
    uv run --frozen python tests/l/access/mutants.py --list

Left out on purpose: the viewer/developer split and the grant predicate themselves are
`contracts.v2` (F2P's list kills them); L2 only has to call them with the current rows.
"""
from __future__ import annotations

import pathlib
import re
import sys

API_DIR = pathlib.Path(__file__).resolve().parents[3]
SUITE_FILE = "tests/l/access/test_access.py"
if str(API_DIR) not in sys.path:        # `python tests/l/access/mutants.py`
    sys.path.insert(0, str(API_DIR))

from tests.contracts import mutants as shared  # noqa: E402
from tests.contracts.mutants import Mutant, Outcome, Result, Runner, _m  # noqa: E402,F401

A = "lab/access/__init__.py"
F = "lab/access/fakes.py"

SEAM = "test_lab_access__a_provider_member_never_reads_another_providers_data"
WORKSPACES = "test_lab_access__workspaces_are_explicit_provider_memberships_only"
REVOKED_MEMBER = "test_lab_access__a_revoked_membership_loses_its_workspace_at_once"
VIEWER = "test_lab_access__a_viewer_reads_aggregates_and_never_content"
REDACTED = "test_lab_access__default_aggregates_carry_no_customer_identity"
PURPOSE = "test_lab_access__each_purpose_is_its_own_permission"
MID_QUEUE = "test_lab_access__revocation_denies_queued_work_on_its_next_check"
EXPIRED = "test_lab_access__an_expired_grant_denies"
HISTORY = "test_lab_access__grant_history_keeps_every_version_for_the_recipient_only"

MUTANTS: tuple[Mutant, ...] = (
    _m("member_check_skipped", "a non-member of the named provider is a 404, not served",
       A, "        if membership is None or not membership.permits(",
       "        if False and not membership.permits(", SEAM),
    _m("revoked_member_served", "a revoked membership is no membership",
       A, "        if membership is None or not membership.permits(",
       "        if membership is None or not membership.model_copy(update={'revoked_at': None})"
       ".permits(", REVOKED_MEMBER),
    _m("aggregates_need_developer", "every current role, the viewer too, reads aggregates",
       A, "                ProviderCapability.read_aggregate_health, self.clock(), provider_org_id)",
       "                ProviderCapability.manage_dev_deployment, self.clock(), provider_org_id)",
       VIEWER),
    _m("aggregates_unguarded", "aggregates are read only by a current member",
       A, "        await self._member(user_id, provider_org_id)\n        return tuple(Deploy",
       "        return tuple(Deploy", SEAM, REVOKED_MEMBER),
    _m("history_unguarded", "grant history is read only by a current member",
       A, "        await self._member(user_id, provider_org_id)\n        return tuple(await",
       "        return tuple(await", SEAM, REVOKED_MEMBER),
    _m("revoked_workspace_listed", "a revoked membership is not a selectable workspace",
       A, "                     if m.is_current(now))", "                     if m)",
       REVOKED_MEMBER),
    _m("aggregate_rows_unvalidated", "aggregate rows are parsed through the closed record",
       A, "DeploymentAggregate.model_validate(row)", "DeploymentAggregate.model_construct(**row)",
       REDACTED),
    _m("aggregate_record_open", "the aggregate record refuses unknown (identity) columns",
       A, 'model_config = ConfigDict(frozen=True, extra="forbid")',
       'model_config = ConfigDict(frozen=True, extra="ignore")', REDACTED),
    _m("grant_lookup_swapped", "the grant is looked up as grantor -> this provider",
       A, "self.store.current_grant(grantor_org_id, provider_org_id)",
       "self.store.current_grant(provider_org_id, grantor_org_id)", SEAM),
    _m("purpose_ignored", "the requested purpose is the one checked",
       A, "model_id=model_id, category=category, purpose=purpose)",
       "model_id=model_id, category=category, purpose=grant.purposes[0] if grant else purpose)",
       PURPOSE),
    _m("clock_frozen_at_grant", "currency is judged on the service clock, not the grant's",
       A, "grant=grant, now=self.clock(),", "grant=grant, now=grant.effective_at if grant "
       "else self.clock(),", MID_QUEUE, EXPIRED),
    _m("history_drops_revocations", "a revocation is a new history version (C/J/T audit)",
       F, "        self.history.append(self.grants[(grantor_org_id, provider_org_id)])\n", "",
       HISTORY),
    _m("fake_lists_every_member", "the fake answers the user's own memberships only",
       F, "if user == user_id]", "if True]", WORKSPACES),
)


def case_names() -> set[str]:
    return set(re.findall(r"^def (test_\w+)\(", (API_DIR / SUITE_FILE).read_text(), re.M))


RUNNER = Runner(name="l2", targets=(SUITE_FILE,))


def run_mutant(mutant) -> Result:
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run L2's mutation list"))
