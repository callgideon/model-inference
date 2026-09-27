#!/usr/bin/env python3
"""R32/R83 for L2: one single-edit defect per decision `tests/l/access` claims, through the
shared runner (`tests/contracts/mutants.py`).

`MUTANTS` run the fake world only (`-m "not pg"`, no Docker). `PG_MUTANTS` are the same edits
of `infrx/lab/access/__init__.py` killed on PostgreSQL (`-m pg`, on the D harness of
`INFRX_D_TASK`, in a copy that carries the migrations); they need L2-SQL merged and Docker.

    INFRX_MUTANTS=all uv run --frozen pytest -q tests/l/access/test_mutants.py
    INFRX_MUTANTS=all INFRX_D_TASK=l2 uv run --frozen pytest -q tests/l/access/test_mutants.py
    uv run --frozen python tests/l/access/mutants.py --list

Left out on purpose: the viewer/developer split and the grant predicate themselves are
`contracts.v2` (F2P's list kills them); L2 only has to call them with the current rows and the
store clock. Reading the clock after the rows is not mutated: its failure is a race.
"""
from __future__ import annotations

import dataclasses
import pathlib
import re
import shutil
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
VIEWER = "test_lab_access__a_viewer_reads_aggregates_and_never_content_or_grants"
REDACTED = "test_lab_access__default_aggregates_carry_no_customer_identity"
PURPOSE = "test_lab_access__each_purpose_is_its_own_permission"
MID_QUEUE = "test_lab_access__revocation_denies_queued_work_on_its_next_check"
EXPIRED = "test_lab_access__an_expired_grant_denies"
STORE_CLOCK = "test_lab_access__revocation_and_grants_are_judged_on_the_store_clock"
HISTORY = "test_lab_access__grant_history_keeps_every_version_for_the_recipient_only"
PG_DOORS = "test_lab_access_pg__a_foreign_member_is_refused_at_both_doors"
FAKE_ONLY, PG_ONLY = (REDACTED,), (PG_DOORS,)

AGG_GUARD = ("        await self._member(user_id, provider_org_id, "
             "ProviderCapability.read_aggregate_health)\n")
HISTORY_GUARD = ("        await self._member(user_id, provider_org_id, "
                 "ProviderCapability.manage_dev_deployment)\n")

MUTANTS: tuple[Mutant, ...] = (
    _m("revoked_member_served", "a revoked membership is no membership",
       A, "        if membership is None or not membership.permits(",
       "        if membership is None or not membership.model_copy(update={'revoked_at': None})"
       ".permits(", REVOKED_MEMBER),
    _m("aggregates_need_developer", "every current role, the viewer too, reads aggregates",
       A, "provider_org_id, ProviderCapability.read_aggregate_health)",
       "provider_org_id, ProviderCapability.manage_dev_deployment)", VIEWER),
    _m("history_open_to_viewer", "0-F2: grant history (grantor org, scope) needs developer+",
       A, "provider_org_id, ProviderCapability.manage_dev_deployment)",
       "provider_org_id, ProviderCapability.read_aggregate_health)", VIEWER),
    _m("aggregates_unguarded", "aggregates are read only by a current member",
       A, AGG_GUARD, "", SEAM, REVOKED_MEMBER),
    _m("history_unguarded", "grant history is read only by a current developer+ member",
       A, HISTORY_GUARD, "", SEAM, REVOKED_MEMBER, VIEWER),
    _m("history_hides_revocations", "every version is history, the revoked ones too",
       A, "        return tuple(await self.store.grant_history(grantor_org_id, provider_org_id))",
       "        return tuple(g for g in await self.store.grant_history(grantor_org_id, "
       "provider_org_id) if g.revoked_at is None)", HISTORY),
    _m("revoked_workspace_listed", "a revoked membership is not a selectable workspace",
       A, "        return tuple(w for w in found if w.membership.is_current(now))",
       "        return tuple(found)", REVOKED_MEMBER, STORE_CLOCK),
    _m("workspace_named_by_id", "R156: a workspace carries its provider's display name",
       A, 'provider_name=row["provider_name"]', 'provider_name=row["provider_org_id"]',
       WORKSPACES),
    _m("aggregate_rows_unvalidated", "aggregate rows are parsed through the closed record",
       A, "DeploymentAggregate.model_validate(row)", "DeploymentAggregate.model_construct(**row)",
       REDACTED),
    _m("aggregate_record_open", "the aggregate record refuses unknown (identity) columns",
       A, 'extra="forbid")\n\n    deployment_revision_id: str',
       'extra="ignore")\n\n    deployment_revision_id: str', REDACTED),
    _m("grant_lookup_swapped", "the grant is looked up as grantor -> this provider",
       A, "self.store.current_grant(grantor_org_id, provider_org_id)",
       "self.store.current_grant(provider_org_id, grantor_org_id)", SEAM),
    _m("purpose_ignored", "the requested purpose is the one checked",
       A, "category=category, purpose=purpose)",
       "category=category, purpose=grant.purposes[0] if grant else purpose)", PURPOSE),
    _m("clock_frozen_at_grant", "currency is judged on the clock of the call, not the grant's",
       A, "grant=grant, now=now,", "grant=grant, now=grant.effective_at if grant else now,",
       MID_QUEUE, EXPIRED),
    _m("clock_is_the_process_clock", "0-F1 (R7/R79): the store's clock, never the process's",
       A, "await self.store.db_now()", "datetime.now().astimezone()", STORE_CLOCK,
       occurrences=3),
    _m("history_drops_revocations", "a revocation is a new history version (C/J/T audit)",
       F, "        self.history.append(self.grants[(grantor_org_id, provider_org_id)])\n", "",
       HISTORY),
    _m("fake_lists_every_member", "the fake answers the user's own memberships only",
       F, "if user == user_id]", "if True]", WORKSPACES),
)

#: The same edits of the service, killed by the same cases on PostgreSQL (the fake-only
#: redaction case aside); the two guards also by the direct-DB-role case.
PG_MUTANTS: tuple[Mutant, ...] = tuple(
    dataclasses.replace(m, name=f"pg_{m.name}", cases=tuple(
        c for c in m.cases if c not in FAKE_ONLY) + (
        PG_ONLY if m.name in ("aggregates_unguarded", "history_unguarded") else ()))
    for m in MUTANTS if m.file == A and set(m.cases) - set(FAKE_ONLY))


def case_names() -> set[str]:
    return set(re.findall(r"^def (test_\w+)\(", (API_DIR / SUITE_FILE).read_text(), re.M))


RUNNER = Runner(name="l2", targets=(SUITE_FILE,), extra_args=("-m", "not pg"))


def _pg_layout(root: pathlib.Path) -> pathlib.Path:
    """The default copy one level down as `apps/infrx-api`, beside a copy of the migrations
    `infrx.state.migrations` reads from `<repo>/apps/app/supabase/migrations`."""
    api = root / "apps" / "infrx-api"
    api.mkdir(parents=True)
    shared._copy(api, RUNNER)
    migrations = pathlib.Path("apps", "app", "supabase", "migrations")
    shutil.copytree(API_DIR.parents[1] / migrations, root / migrations)
    return api


PG_RUNNER = Runner(name="l2-pg", targets=(SUITE_FILE,), extra_args=("-m", "pg"),
                   env=("INFRX_D_TASK",), layout=_pg_layout)


def run_mutant(mutant) -> Result:
    """The PostgreSQL list is not a module's `MUTANTS`, so the shared runner takes no
    baseline for it: its cases run unmutated first, once per process (R83 (b))."""
    if mutant not in PG_MUTANTS:
        return shared.run_mutant(mutant, RUNNER)
    cases = tuple(sorted({case for m in PG_MUTANTS for case in m.cases}))
    return shared.pristine(cases, PG_RUNNER) or shared.run_mutant(mutant, PG_RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run L2's mutation list"))
