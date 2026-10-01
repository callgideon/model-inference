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
from ...contracts.mutants import Mutant, Result, Runner
from ..feedback.mutants import _layout
from ..lab_auth import mutants as auth

SUITE_FILES = ("tests/g/lab_releases/test_lab_releases.py",)
F = "gateway/routes/lab_releases.py"
P = "lab/compose.py"                    # WR-LIVE-PAGE: the composed records' progress (A1)
FILES = (F, auth.F, P)
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
PROGRESS = C + "a_releases_progress_is_d9s_live_null_only_before_one_is_observed"
UNIT_REFUSED = C + "a_unit_refused_live_nulls_its_own_row_and_the_others_list"
V_NOW = C + "a_running_releases_verdict_is_r2s_evaluate_now_when_d9_holds_none"
V_NULL = C + "nothing_assigned_or_a_refused_unit_reads_no_verdict"
V_D9 = C + "a_decided_release_reads_d9s_decision_never_a_fresh_evaluation"
V_POOL = C + "the_composed_records_read_b4s_experiments_on_the_pool"
V_UNWIRED = C + "unwired_b4_is_a_typed_refusal_on_an_undecided_running_release"
V_BAD_ROW = C + "an_unreadable_b2_report_nulls_only_its_rows_verdict"
BAD_ROW = "        except (errors.NotFound, KeyError, TypeError, ValueError) as bad:   # R260"
NOT_RUNNING = '        if item.release.state != "running" or live is None:\n            return None\n'
ADMIN = ("        who = await lab_actor(request, x.sessions, x.access,\n"
         "                              Cap.read_aggregate_health)"
         "          # the role: `propose`\n")
UNKNOWN = C + "an_unknown_policy_is_not_found_whatever_the_role"
FOUND = ('    if shown is None:\n'
         '        raise errors.NotFound("no such release for this provider")\n')
REQUIRE = "    require(who, Cap.propose_publication)\n"
BODY_READ = "        wanted = await lab_body(request, rt, ProposalRequest)\n"


def _m(name, invariant, old, new, *cases, file=F, dies_by=()) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=file, old=old, new=new, cases=cases,
                  dies_by=dies_by)


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
    # WR-LIVE-PAGE (composition-7): progress is D9's Live of the revision (0054, R244)
    _m("page_progress_withheld", "progress is D9's Live, not null",
       '"progress": _progress(live, None if live is None else',
       '"progress": None and _progress(live, None if live is None else',
       PROGRESS, file=P),
    # C7-RV-6 (R248): one unit-refused Live nulls its own row; the listing still answers
    _m("page_unit_refusal_fails_listing", "a unit-refused Live never fails the listing",
       "            except errors.InvalidRequest:     # R248", "            except errors.Conflict:"
       "     # R248", UNIT_REFUSED, file=P),
    _m("page_any_failure_is_a_unit_refusal", "only R248's refusal degrades a row; an outage "
       "still fails the listing", "            except errors.InvalidRequest:     # R248",
       "            except errors.DomainError:     # R248", UNIT_REFUSED, file=P),
    _m("page_not_found_is_a_unit_refusal", "a NotFound from Live still fails the listing "
       "(0-F1)", "            except errors.InvalidRequest:     # R248",
       "            except (errors.InvalidRequest, errors.NotFound):     # R248", UNIT_REFUSED,
       file=P),
    _m("page_unit_refused_verdict_shown", "a unit-refused row shows no verdict",
       "out[-1].update(verdict=None, refused=refused)", "out[-1].update(refused=refused)",
       UNIT_REFUSED, file=P),
    _m("page_unit_refused_untyped", "a unit-refused row names its typed reason",
       'live, refused = None, "unit_refused"', "live, refused = None, None", UNIT_REFUSED,
       file=P),
    _m("page_unit_refused_everywhere", "a row not refused carries no refusal",
       "            if refused:\n                out[-1].update(", "            if True:\n"
       "                out[-1].update(", UNIT_REFUSED, file=P),
    _m("page_live_of_another", "each release's Live is read for its own revision",
       "await self.d9.live(item.policy_ref)", "await self.d9.live(item.policy_id)", PROGRESS,
       file=P),
    _m("page_progress_invented", "nothing observed is null, never an invented progress",
       "    if live is None:\n        return None\n\n    def arm",
       '    if live is None:\n        return {"assignments": []}\n\n    def arm', PROGRESS,
       file=P),
    _m("page_arms_swapped", "each arm is shown as its own", '"baseline": arm(live.baseline),',
       '"baseline": arm(live.candidate),', PROGRESS, file=P),
    _m("page_errors_lost", "an arm's errors are shown", '"errors": a.errors,', '"errors": 0,',
       PROGRESS, file=P),
    _m("page_p99_lost", "an arm's p99 is shown", '"p99_ms": a.p99_ms}', '"p99_ms": None}',
       PROGRESS, file=P),
    _m("page_coverage_lost", "quality coverage is shown", '"quality_covered": live.quality_covered,',
       '"quality_covered": 0,', PROGRESS, file=P),
    _m("page_spend_unit", "the spend keeps its unit (never converted, R246)",
       '"unit": live.spent.unit},', '"unit": "CREDIT"},', PROGRESS, file=P),
    _m("page_health_invented", "the candidate's health is D9's (R247)",
       '"candidate_healthy": live.candidate_healthy,', '"candidate_healthy": True,', PROGRESS,
       file=P),
    _m("page_clock_not_utc", "the observation time is the database clock as UTC",
       '"observed_until": _z(live.observed_until),', '"observed_until": str(live.observed_until),',
       PROGRESS, file=P),
    # WR-C7-TALLY (lab-sql LW9): assignments are D9's per-serving tally (0058)
    _m("page_tally_dropped", "the per-serving tally is shown, not emptied",
       '"assignments": assignments}', '"assignments": []}', PROGRESS, file=P),
    _m("page_tally_of_another", "each release's tally is read for its own revision",
       "await self.d9.tally(item.policy_ref)", "await self.d9.tally(item.policy_id)", PROGRESS,
       file=P),
    _m("page_tally_unobserved", "no tally is read before anything is observed",
       "None if live is None else\n", "\n", PROGRESS, file=P),
    # WR-LR6-VERDICT (lab-rollout-7): R2's verdict at read time when D9 holds no decision
    _m("verdict_never_evaluated", "a running release D9 holds no decision for reads R2's "
       "verdict now, not null", NOT_RUNNING, "        if True:\n            return None\n",
       V_NOW, file=P),
    _m("verdict_of_nothing_assigned", "nothing assigned is null (R244), never an evaluation "
       "of no Live (R2 would read the arms of None)", NOT_RUNNING,
       NOT_RUNNING.replace(" or live is None", ""), V_NULL, file=P,
       dies_by=("AttributeError",)),
    _m("verdict_of_a_settled_release", "a release that is not running is never evaluated",
       NOT_RUNNING, NOT_RUNNING.replace('item.release.state != "running" or ', ""), V_D9,
       file=P),
    _m("verdict_ignores_d9s_decision", "D9's decision is the verdict once it holds one",
       '        if d is not None:\n            return {"action": d.decision,',
       '        if False:\n            return {"action": d.decision,', V_D9, file=P),
    _m("verdict_unit_refusal_fails_the_listing", "R2's unit refusal is a null verdict (R248), "
       "never a failed listing", "        except errors.InvalidRequest:\n            return None\n",
       "        except errors.NotFound:\n            return None\n", V_NULL, file=P),
    _m("verdict_at_the_wall_clock", "judged at the database clock of the Live it shows",
       "now=live.observed_until, report=report",
       'now=__import__("datetime").datetime.now(__import__("datetime").timezone.utc), '
       "report=report", V_NOW, file=P),
    _m("verdict_without_its_report", "B2's report is the verdict's second input",
       "now=live.observed_until, report=report", "now=live.observed_until, report=None",
       V_NOW, file=P),
    _m("verdict_reasons_dropped", "R2's reasons are shown",
       'return {"action": v.action, "reasons": list(v.reasons),',
       'return {"action": v.action, "reasons": [],', V_NOW, file=P),
    _m("verdict_evidence_dropped", "an expand verdict carries the report's runs as evidence",
       '"evidence_refs": list(v.evidence_refs), "evaluated_at": _z(live.observed_until)}',
       '"evidence_refs": [], "evaluated_at": _z(live.observed_until)}', V_NOW, file=P),
    _m("verdict_time_not_the_lives", "evaluated_at is the Live's observation time",
       '"evidence_refs": list(v.evidence_refs), "evaluated_at": _z(live.observed_until)}',
       '"evidence_refs": list(v.evidence_refs), "evaluated_at": _z(item.release.started_at)}',
       V_NOW, file=P),
    _m("verdict_reads_another_live", "the verdict is judged on the Live the progress shows",
       "verdict = await self.verdict(provider_org_id, item, policy, full, live)",
       "verdict = await self.verdict(provider_org_id, item, policy, full,\n"
       "                                              await self.d9.live(item.policy_ref))",
       V_NOW, file=P),
    _m("verdict_reads_absent", "LAB_RELEASES reads B4's experiments (0043)",
       "PgLabReads(connect)),", "None),", V_POOL, file=P),
    # merge #62 minors: RV-2, 1-LR7-RV-2, 1-LR7-RV-3 (R260)
    _m("verdict_report_of_any_provider", "B2's report is read for the listed provider only "
       "(RV-2)", "release_report(self.reads, self.store, provider_org_id,",
       'release_report(self.reads, self.store, "",', V_NOW, file=P),
    _m("verdict_unwired_reads_crash", "unwired B4 is a typed 503, never an AttributeError "
       "(1-LR7-RV-2)", "        if self.reads is None:", "        if False:", V_UNWIRED,
       file=P, dies_by=("AttributeError",)),
    _m("report_unavailable_fails_the_listing", "an unreadable B2 report degrades its row "
       "only (R260)", "            except ReportUnavailable:", "            except errors.Conflict:",
       V_BAD_ROW, file=P, dies_by=("ReportUnavailable",)),
    _m("report_unavailable_untyped", "a report-unavailable row names its typed reason (R260)",
       'verdict, refused = None, "report_unavailable"', "verdict, refused = None, None",
       V_BAD_ROW, file=P),
    _m("report_bad_body_fails_the_listing", "a body that is not JSON is a bad row (R260)",
       BAD_ROW, BAD_ROW.replace(", ValueError)", ")"), V_BAD_ROW, file=P,
       dies_by=("JSONDecodeError",)),
    _m("report_outage_is_a_bad_row", "a B4 outage still fails the listing (R260)",
       BAD_ROW, BAD_ROW.replace("(errors.NotFound, KeyError, TypeError, ValueError)",
                                "Exception"), V_BAD_ROW, file=P),
    _m("verdict_reads_off_the_pool", "the experiments are read on the gateway's pool",
       "PgLabReads(connect)),", "PgLabReads(None)),", V_POOL, file=P),
)


def case_names() -> set[str]:
    return auth.case_names_in(SUITE_FILES)


RUNNER = auth.runner("lab-releases", SUITE_FILES)
# C7-RV-6 on real 0054 (the p3 key): opt-in (`INFRX_LAB_RELEASES_PG=1`, `INFRX_D_TASK=p3`),
# since a whole-suite parent holds the key's port lock. Its own baseline, as worker_main's.
PG_FILE = "tests/g/lab_releases/test_lab_releases_unit_refused_pg.py"
PG_CASE = "test_lab_releases_unit_refused_pg__a_legacy_usd_release_nulls_only_its_own_row"
PG_RUNNER = Runner(name="lab-releases-pg", targets=(PG_FILE,), layout=_layout,
                   env=("INFRX_D_TASK",))
# 1-LR7-RV-5: WR-LR6-VERDICT's composition case on real 0054/0043/D7 (the r2 key), the same
# opt-in (`INFRX_LAB_RELEASES_PG=1`, `INFRX_D_TASK=r2`)
COMP_PG_FILE = "tests/g/lab_releases/test_lab_releases_composition_pg.py"
COMP_PG_CASE = ("test_lab_releases_composition_pg__a_running_releases_verdict_is_r2s_evaluate_"
                "at_read_time")
COMP_PG_RUNNER = Runner(name="lab-releases-pg-r2", targets=(COMP_PG_FILE,), layout=_layout,
                        env=("INFRX_D_TASK",))
#: each PostgreSQL case -> (its task-local key, its runner)
PG_KEYS = {PG_CASE: ("p3", PG_RUNNER), COMP_PG_CASE: ("r2", COMP_PG_RUNNER)}
PG_MUTANTS: tuple[Mutant, ...] = (
    _m("page_unit_refusal_fails_listing_pg", "a legacy-USD release on real 0054 never fails "
       "the listing", "            except errors.InvalidRequest:     # R248",
       "            except errors.Conflict:     # R248", PG_CASE, file=P),
    _m("verdict_never_evaluated_pg", "a running release's verdict is R2's evaluate over real "
       "0054 and B4's experiments, not null", NOT_RUNNING,
       "        if True:\n            return None\n", COMP_PG_CASE, file=P,
       dies_by=("TypeError",)),       # the case reads the null verdict's action
    _m("verdict_reads_absent_pg", "LAB_RELEASES composes B4's experiments (0043) on real "
       "PostgreSQL", "PgLabReads(connect)),", "None),", COMP_PG_CASE, file=P),
)


def pg_key(mutant) -> str:
    """The task-local key a PostgreSQL mutant's case runs on."""
    return PG_KEYS[mutant.cases[0]][0]


def run_mutant(mutant) -> Result:
    if mutant in PG_MUTANTS:
        runner = PG_KEYS[mutant.cases[0]][1]
        return shared.pristine(mutant.cases, runner) or shared.run_mutant(mutant, runner)
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run WR-R4-1's releases mutation list"))
