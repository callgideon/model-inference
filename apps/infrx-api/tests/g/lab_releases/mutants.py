#!/usr/bin/env python3
"""R32/R40/R83 for WR-R4-1: one single-edit defect per invariant `/lab/v1/releases` and
`/lab/v1/optimizations` claim.

Mutants live in the router and, for the two seam decisions its route cases prove over HTTP
(consumer-only 403, foreign provider 404), in `lab_auth` (lab-api, batch #4).

    uv run --frozen pytest -q tests/g/lab_releases/test_mutants.py
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/g/lab_releases/test_mutants.py
    uv run --frozen python -m tests.g.lab_releases.mutants --list
"""
from __future__ import annotations

from ...contracts import mutants as shared
from ...contracts.mutants import Mutant, Result
from ..lab_auth import mutants as auth

SUITE_FILES = ("tests/g/lab_releases/test_lab_releases.py",)
F = "gateway/routes/lab_releases.py"
FILES = (F, auth.F)
C = "test_lab_releases__"
MOUNT, SESSION = C + "nothing_is_mounted_without_the_switch", \
    C + "every_route_needs_the_session_before_anything_else"
ACCESS, ROLES = C + "a_consumer_only_user_is_denied_and_another_provider_is_not_found", \
    C + "every_role_reads_and_only_an_administrator_proposes"
RECORDS, FENCE = C + "the_records_are_the_providers_own_as_stored", \
    C + "a_proposal_names_the_revision_d9_holds_now"
VERDICT = C + "only_an_expand_verdict_on_a_running_release_can_be_proposed"
FOREIGN = C + "another_providers_policy_is_not_found_and_d9_is_not_read"
UNWIRED, BODY = C + "an_unwired_port_is_unavailable_after_the_access_checks", \
    C + "a_body_is_json_and_exactly_a_proposal"
ADMIN = ("        who = await lab_actor(request, x.sessions, x.access,\n"
         "                              Cap.read_aggregate_health)"
         "          # the role: `propose`\n")
UNKNOWN = C + "an_unknown_policy_is_not_found_whatever_the_role"
FOUND = ('    if shown is None:\n'
         '        raise errors.NotFound("no such release for this provider")\n')
REQUIRE = "    require(who, Cap.propose_publication)\n"
BODY_READ = "        wanted = await lab_body(request, rt, ProposalRequest)\n"


def _m(name, invariant, old, new, *cases, file=F) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=file, old=old, new=new, cases=cases)


MUTANTS: tuple[Mutant, ...] = (
    # --- mounting ----------------------------------------------------------------------------
    _m("mounted_without_the_switch", "LAB_RELEASES off (no rt.lab_releases): no route",
       "    if x is None:\n        return None", "    if False:\n        return None", MOUNT),
    _m("runtime_releases_ignored", "register(app, rt) mounts over rt.lab_releases",
       'getattr(rt, "lab_releases", None)', "None", MOUNT),
    # --- identity, access, capability --------------------------------------------------------
    _m("body_before_identity", "a proposal body is read after the session and membership",
       ADMIN + BODY_READ, BODY_READ + ADMIN, SESSION),
    _m("consumer_only_not_denied", "a consumer-only user is a 403 on every route",
       "    if not workspaces:\n", "    if False:\n", ACCESS, file=auth.F),
    _m("foreign_provider_is_forbidden", "another provider's id is a 404 on every route",
       'raise errors.NotFound("no such provider workspace")',
       'raise errors.Forbidden("no such provider workspace")', ACCESS, file=auth.F),
    _m("reads_need_administration", "every role reads releases and optimizations",
       "x.sessions, x.access, Cap.read_aggregate_health)\n",
       "x.sessions, x.access, Cap.propose_publication)\n", ROLES),
    _m("developer_proposes", "only an administrator proposes",
       REQUIRE, REQUIRE.replace("propose_publication", "manage_dev_deployment"), ROLES),
    _m("role_before_the_policy", "an unknown or foreign policy is a 404 whatever the role",
       FOUND + REQUIRE, REQUIRE + FOUND, UNKNOWN),
    # --- the records ---------------------------------------------------------------------------
    _m("optimizations_are_releases", "each surface reads its own records",
       "listing(variants)", "listing(releases)", RECORDS),
    _m("decisions_withheld", "D9's decisions are served as stored",
       '"decisions": list(await records.decisions(provider)),', '"decisions": [],', RECORDS),
    _m("proposals_withheld", "the provider's proposals are served",
       '"proposals": list(await x.port("proposals").proposals(provider))}',
       '"proposals": []}', RECORDS),
    # --- ROLLOUT-PIN: the proposal -----------------------------------------------------------
    _m("fence_from_the_read_model", "the fence is D9's now, not the listing's",
       "    if live.fence != wanted.fence:\n", '    if shown["fence"] != wanted.fence:\n', FENCE),
    _m("fence_unchecked", "a stale fence is a 409",
       "    if live.fence != wanted.fence:\n", "    if False:\n", FENCE),
    _m("proposed_at_unset", "a proposal is timed on the store's clock",
       '"proposed_at": now.strftime("%Y-%m-%dT%H:%M:%SZ")', '"proposed_at": None', FENCE),
    _m("proposal_born_approved", "a proposal is only proposed: an operator decides",
       '"fence": wanted.fence, "state": "proposed",', '"fence": wanted.fence, "state": "approved",',
       FENCE),
    _m("proposed_with_200", "a proposal is a 201",
       "return lab_auth.ok(await propose(x, who, wanted), 201)",
       "return lab_auth.ok(await propose(x, who, wanted))", FENCE),
    _m("rolled_back_reopened", "nothing is proposed on a rolled-back release",
       '    if live.state == "rolled_back":\n', "    if False:\n", VERDICT),
    _m("expand_without_an_expand_verdict", "an inconclusive (hold) verdict cannot be expanded",
       'or (shown["verdict"] or {}).get("action") != "expand"):', "or False):", VERDICT),
    _m("expand_on_a_settled_release", "only a running release is expanded",
       'if wanted.kind == "expand" and (live.state != "running"',
       'if wanted.kind == "expand" and (False', VERDICT),
    _m("foreign_policy_read_from_d9", "D9 is read only for the provider's own release",
       "    if shown is None:\n        raise", "    if False:\n        raise", FOREIGN),
    # --- the ports and the body ----------------------------------------------------------------
    _m("unwired_is_a_bug", "a port not merged yet is a typed 503, not an AttributeError",
       "        if value is None:                   # expected until its table merges: a 503\n",
       "        if False:\n", UNWIRED),
    _m("negative_fence", "a fence is a non-negative integer",
       "    fence: int = Field(ge=0)", "    fence: int", BODY),
    _m("kind_open", "a proposal is expand or rollback",
       '    kind: Literal["expand", "rollback"]', "    kind: str", BODY),
    # WR-R4-2 (composition-6): 0043 records the proposer
    _m("proposer_not_the_session", "a proposal's proposer is the session's user",
       '        "proposed_by": who.user_id})', '        "proposed_by": None})', FENCE),
)


def case_names() -> set[str]:
    return auth.case_names_in(SUITE_FILES)


RUNNER = auth.runner("lab-releases", SUITE_FILES)


def run_mutant(mutant) -> Result:
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run WR-R4-1's releases mutation list"))
