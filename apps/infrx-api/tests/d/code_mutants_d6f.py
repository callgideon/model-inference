"""R32/R40 for D6F: single-edit defects of the feedback migration (killed by the named check of
`test_d6f_feedback.py` on a database built from the mutated set, needs Docker) and of
`infrx/state/feedback.py` (killed by the named case of `test_d6f_units.py` through the shared
runner, no Docker).

    INFRX_D_TASK=dlab uv run --frozen pytest -q tests/d/test_code_mutants_d6f.py
"""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import psycopg
from infrx.state import migrations

from ..contracts import mutants as shared
from ..contracts.mutants import Mutant, Runner
from . import code_mutants_d7 as d7
from . import migration_mutants as _d
from . import pgharness
from . import test_d6f_feedback as t
from . import test_d6f_scrub as scrub_world

FILE = "0028_feedback_durable.sql"
SCRUB_FILE = "0035_feedback_scrub.sql"
DB = f"{pgharness.DATABASE}_d6fmut"

FLAG = "check_the_writes_fail_closed_until_the_flag_is_on"
ACK = "check_acceptance_commits_row_key_and_outbox_together"
SPOOF = "check_provenance_cannot_be_supplied_by_the_client"
OWNER = "check_ownership_is_the_durable_job"
LABEL = "check_a_label_is_an_audited_operator_row_of_the_jobs_tenant"
IMMUTABLE = "check_feedback_rows_are_immutable"
ROLES = "check_browser_roles_reach_no_write"
RACE = "check_a_concurrent_duplicate_replays_the_first_row"
_OUTBOX = ("  insert into infrx.outbox (event_id, aggregate_id, org_id, kind, payload, "
           "available_at)\n  values (gen_random_uuid(), v_job, v_org, 'feedback_projection',\n"
           "          jsonb_build_object('feedback_id', f.feedback_id), infrx.now());\n")
_AUDIT = ("  insert into infrx.audit_entries (id, actor_principal, action, target_org_id, "
          "reason, after,\n    idempotency_key)\n  values (gen_random_uuid(), f.author_principal, "
          "'calibration_label', v_org,\n    'calibration label', jsonb_build_object('feedback_id', "
          "f.feedback_id, 'request_id', v_job,\n      'label', f.value_text, 'rubric_version', "
          "f.rubric_version), f.idempotency_key);\n")


def _s(name, old, new, check, why, **kw):
    return _d.Mutant(name, FILE, old, new, "lab", check, why, **kw)


SQL_MUTANTS = (
    _s("d6f_missing_flag_row_enables", "where f.name = 'feedback'),\n                  false)",
       "where f.name = 'feedback'),\n                  true)", FLAG,
       "applying the migration turns feedback on (a missing row is not closed)"),
    _s("d6f_flag_not_checked", "  perform infrx.feedback_enabled();\n", "", FLAG,
       "feedback is written with its flag off", occurrences=2),
    _s("d6f_body_keys_unchecked", "     or exists (select 1 from jsonb_object_keys(v_body) k\n"
       "                where k not in ('name', 'value', 'comment'))\n", "", SPOOF,
       "a client's author, channel or marker key is silently accepted"),
    _s("d6f_role_follows_the_marker", "p_args->>'principal', 'customer',",
       "p_args->>'principal', case when (p_args->>'by_operator')::boolean then 'operator' "
       "else 'customer' end,", ACK, "an operator on the customer path authors operator rows (R31)"),
    _s("d6f_float_rating_rounded", " and v_value::text ~ '^-?[0-9]+$'", "", SPOOF,
       "a 1.5 rating is stored as 2"),
    _s("d6f_ownership_ignores_the_org", "where j.request_id = v_job and j.org_id = v_org)\n",
       "where j.request_id = v_job)\n", OWNER,
       "another organization answers for a request it does not own"),
    _s("d6f_suspension_ignored", "  if (select o.suspended from public.organizations o where "
       "o.id = v_org) then", "  if false then", OWNER, "a suspended org submits (R33)"),
    _s("d6f_duplicate_writes_twice", "  return not exists (select 1 from infrx.idempotency",
       "  return true or not exists (select 1 from infrx.idempotency", ACK, "a replayed submission fails instead of answering its row"),
    _s("d6f_concurrent_duplicate_fails", "  perform pg_advisory_xact_lock(hashtextextended(",
       "  perform (hashtextextended(", RACE,
       "the second of two simultaneous submissions fails instead of answering the first row"),
    _s("d6f_changed_payload_replays", "  if i.payload_digest <> p_idem->>'payload_hash' then",
       "  if false then", ACK, "a changed payload under a used key returns the old row"),
    _s("d6f_keys_cross_operations", "  if not found or f.calibration_set <> p_label then",
       "  if not found then", LABEL,
       "a customer replaying an operator's key receives the operator's label (R54)"),
    _s("d6f_no_projection_event", _OUTBOX, "", ACK,
       "an acknowledged feedback never reaches the projection"),
    _s("d6f_label_without_operator", "  if not coalesce((p_args->>'is_operator')::boolean, "
       "false) then", "  if false then", SPOOF, "a customer authors a calibration label"),
    _s("d6f_label_on_another_scope", "  if (p_args->'idem'->>'org_id')::uuid is distinct from "
       "v_org then", "  if false then", LABEL,
       "a label's key is scoped to the operator's org, not the row's (R26)"),
    _s("d6f_label_not_audited", _AUDIT, "", LABEL, "an operator's label leaves no audit row (R34)"),
    # 0035 re-creates this trigger over its scrub guard, so the mutant lives there now
    _d.Mutant("d6f_rows_mutable", SCRUB_FILE, "create trigger feedback_immutable before update "
              "or delete on infrx.feedback\n  for each row execute function "
              "infrx.feedback_guard();\n", "", "lab", IMMUTABLE,
              "an author, channel or role is rewritten after acknowledgment"),
    _s("d6f_reads_any_org", "                 and (v_org is null or j.org_id = v_org)) then",
       "                 ) then", OWNER, "another org learns a request exists"),
    _s("d6f_reads_mix_labels", "\n                      and f.calibration_set = coalesce(("
       "p_args->>'calibration')::boolean,\n                                                "
       "       false)", "", LABEL, "a customer's list carries operator labels (R49)"),
    _s("d6f_label_callable_by_browsers", "-- `accept_feedback` keeps 0004's grants through "
       "`create or replace`.", "grant execute on function infrx.label_calibration(jsonb) to "
       "authenticated;", ROLES, "a browser session calls the label RPC with a forged operator"),
)

RUNNER = Runner(name="d6f", targets=("tests/d/test_d6f_units.py",))
F = "state/feedback.py"
SENDS = "test_accept__sends_server_derived_provenance_and_the_signal_only"
REFUSES = "test_accept__refuses_forged_fields_and_foreign_scopes_without_calling"
MASKS = "test_accept__an_operator_row_reads_platform_to_a_customer"
LABELS = "test_label__operator_only_bounded_and_sends_the_label"
LISTS = "test_lists__owned_is_the_callers_org_and_labels_are_operator_only"
CODES = "test_refusals__the_disabled_flag_and_sql_codes_are_typed"
REPLAY = "test_accept_with_replay__a_stored_row_of_another_id_is_a_replay"
SCRUBBED = "test_scrub__sends_the_org_request_and_receipt_fields_and_answers_the_count"


def _p(name, invariant, old, new, *cases) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=F, old=old, new=new, cases=cases)


CODE_MUTANTS = (
    _p("d6f_py_channel_fixed", "the channel is the service's", '"channel": self.channel.value,',
       '"channel": FeedbackChannel.api.value,', SENDS),
    _p("d6f_py_marker_dropped", "R50: the operator marker is the session's",
       '"by_operator": bool(auth.is_operator),', '"by_operator": False,', SENDS),
    _p("d6f_py_body_whole", "only the signal reaches the SQL",
       'include={"name", "value", "comment"},\n                                    exclude_none=True)',
       'exclude_none=True)', SENDS),
    _p("d6f_py_non_object_body", "a JSON array is a 400",
       "        if not isinstance(feedback, dict):\n", "        if False:\n", REFUSES),
    _p("d6f_py_foreign_scope", "R10: the key is the caller's org's",
       "        if idem.org_id != auth.org_id:", "        if False:", REFUSES),
    _p("d6f_py_keyless", "R3: a key is required",
       '        if idem.key is None:\n            raise errors.InvalidRequest("an idempotency key '
       'is required for feedback")', '        if False:\n            raise '
       'errors.InvalidRequest("an idempotency key is required for feedback")', REFUSES),
    _p("d6f_py_unmasked_answer", "R41/R54: an answer is projected for its viewer",
       "        return visible_feedback((Feedback.model_validate(row),),\n"
       "                                operator=bool(auth.is_operator))[0]",
       "        return Feedback.model_validate(row)", MASKS),
    _p("d6f_py_label_by_anyone", "R31: only an operator labels",
       '        if not auth.is_operator:\n            raise errors.Forbidden("labelling',
       '        if False:\n            raise errors.Forbidden("labelling', LABELS),
    _p("d6f_py_bool_rubric", "R54: a boolean is not a rubric version",
       "        if isinstance(rubric_version, bool) or not isinstance", "        if not isinstance",
       LABELS),
    _p("d6f_py_long_comment", "R43: a label's comment is bounded",
       "\n                                    or len(comment) > MAX_FEEDBACK_TEXT_CHARS):", "):",
       LABELS),
    _p("d6f_py_label_keyless", "a label needs a key", '        if idem.key is None:\n'
       '            raise errors.InvalidRequest("an idempotency key is required for a '
       'calibration label")', '        if False:\n            raise errors.InvalidRequest("an '
       'idempotency key is required for a calibration label")', LABELS),
    _p("d6f_py_owned_any_org", "list_owned is the caller's org's",
       '"org_id": auth.org_id})', '})', LISTS),
    _p("d6f_py_owned_shows_labels", "R49: no label in list_owned",
       "        return visible_feedback(tuple(Feedback.model_validate(r) for r in rows),\n"
       "                                operator=bool(auth.is_operator))",
       "        return tuple(Feedback.model_validate(r) for r in rows)", LISTS),
    _p("d6f_py_calibration_for_anyone", "R35: labels are operator data",
       '        if not auth.is_operator:\n            raise errors.Forbidden("calibration',
       '        if False:\n            raise errors.Forbidden("calibration', LISTS),
    _p("d6f_py_flag_off_untyped", "a disabled flag is a typed 503",
       '    if getattr(failed, "sqlstate", None) == "0A000":', "    if False:", CODES),
    _p("d6f_py_never_replayed", "WR-G4F-2: a replay is reported",
       '            row["feedback_id"] != feedback_id', "            False", REPLAY),
    _p("d6f_py_new_id_per_read", "the id compared is the one sent",
       '"request_id": request_id, "feedback_id": feedback_id,',
       '"request_id": request_id, "feedback_id": ids.new_feedback_id(),', REPLAY),
    _p("d6f_py_scrub_unattributed", "a scrub names its actor",
       '"org_id": org_id, "request_id": request_id, "actor": actor,',
       '"org_id": org_id, "request_id": request_id, "actor": "platform",', SCRUBBED),
    _p("d6f_py_scrub_raw", "the scrub answers its count",
       '            "reason": reason}))["scrubbed"]', '            "reason": reason}))', SCRUBBED),
)
SQL_NAMES = tuple(m.name for m in SQL_MUTANTS)

# ------------------------------------------------------------ the scrub (0035, T3's request)
DB_S = f"{pgharness.DATABASE}_d6fsmut"
SCRUBS = "check_a_scrub_removes_the_text_and_leaves_a_receipt"
OWN = "check_only_the_orgs_own_request_is_scrubbed"
GUARD = "check_nothing_but_a_scrub_rewrites_feedback"
ROLES_S = "check_browser_roles_cannot_scrub"
SERVICE = "check_the_service_reports_replays_and_scrubs"


def _c(name, old, new, check, why, **kw):
    return _d.Mutant(name, SCRUB_FILE, old, new, "lab", check, why, **kw)


SCRUB = (
    _c("d6f_scrub_keeps_comments", "  update infrx.feedback set comment = null,",
       "  update infrx.feedback set comment = comment,", SCRUBS,
       "a deleted trace's customer comment survives in PostgreSQL"),
    _c("d6f_scrub_erases_verdicts", "    value_text = case when name in ('correction', 'comment') "
       "then '[scrubbed]'\n                      else value_text end", "    value_text = case "
       "when name in ('correction', 'comment', 'calibration_label') then '[scrubbed]'\n"
       "                      else value_text end", SCRUBS,
       "an operator's calibration verdict is destroyed by a customer deletion"),
    _c("d6f_scrub_unreceipted", "  if n > 0 then", "  if false then", SCRUBS,
       "a deletion leaves no receipt"),
    _c("d6f_scrub_receipts_repeats", "  if n > 0 then", "  if true then", SCRUBS,
       "every sweep writes an empty receipt (and fails its own check)"),
    _c("d6f_scrub_any_org", "  if not exists (select 1 from infrx.jobs j where j.request_id = "
       "v_job and j.org_id = v_org)", "  if not exists (select 1 from infrx.jobs j where "
       "j.request_id = v_job)", OWN, "one org erases another org's feedback"),
    _c("d6f_scrub_whole_org", "   where org_id = v_org and request_id = v_job\n",
       "   where org_id = v_org\n", OWN, "deleting one trace erases every trace's feedback"),
    _c("d6f_guard_ignores_marker", "  if tg_op = 'UPDATE' and current_setting("
       "'infrx.feedback_scrub', true) = 'on'", "  if tg_op = 'UPDATE'", GUARD,
       "any writer blanks feedback text outside the audited scrub"),
    _c("d6f_guard_ignores_provenance", "\n     and to_jsonb(new) - 'comment' - 'value_text' = "
       "to_jsonb(old) - 'comment' - 'value_text'", "", GUARD,
       "a scrub-shaped update also rewrites the author"),
    _c("d6f_guard_any_comment", "     and new.comment is null\n", "", GUARD,
       "a comment is rewritten under the scrub marker"),
    _c("d6f_guard_any_text", "     and new.value_text is not distinct from (case", "     and "
       "true or new.value_text is not distinct from (case", GUARD,
       "a correction is rewritten under the scrub marker"),
    _c("d6f_scrub_unattributed", "      perform infrx.refuse('invalid_request', 'a scrub names "
       "its actor and reason');", "      null;", GUARD, "an unattributed scrub removes text"),
    _c("d6f_scrub_receipts_deletable", "grant select on infrx.feedback_scrubs to service_role;",
       "grant select, delete on infrx.feedback_scrubs to service_role;", ROLES_S,
       "the platform role erases the evidence that a deletion happened"),
    _c("d6f_scrub_count_misreported", "  return jsonb_build_object('scrubbed', n);",
       "  return jsonb_build_object('scrubbed', 0);", SERVICE,
       "T3's sweep cannot tell a scrub from a no-op"),
)
SCRUB_NAMES = tuple(m.name for m in SCRUB)


def kill_scrub(mutant) -> tuple[str, str]:
    return d7.kill(mutant, DB_S, scrub_world)


def kill(mutant) -> tuple[str, str]:
    """migration_mutants.kill's classification on this lane's database and seed."""
    pgharness.ensure()
    with TemporaryDirectory(prefix=f"infrx-dlab-{mutant.name}-") as tmp:
        directory = Path(tmp)
        refused = _d._mutate(directory, mutant)
        if refused is not None:
            return _d.MISDECLARED, refused
        try:
            pgharness.recreate(DB)
            pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM,
                                                   directory=directory))
        except (AssertionError, psycopg.Error) as broken:
            return _d.APPLY_ERROR, _d._first_line(broken)
        try:
            with pgharness.connect(DB) as conn:
                t.seed(conn)
                return _d._run(t.CHECKS[mutant.check], conn)
        except (AssertionError, psycopg.Error) as during_setup:
            return _d.SETUP_ERROR, _d._first_line(during_setup)


def run_code_mutant(mutant):
    return shared.run_mutant(mutant, RUNNER)


# --- WR-C3F-1: the session doors, `0038_feedback_doors.sql` (test_d6f_doors' world) --------
DOORS_FILE = "0038_feedback_doors.sql"
DB_D = f"{pgharness.DATABASE}_d6fdmut"
SUBMIT = "check_submit_takes_the_signal_and_derives_the_rest"
REVIEW = "check_review_is_l2s_rule_on_the_database_clock"


def _o(name, old, new, check, why, **kw):
    return _d.Mutant(name, DOORS_FILE, old, new, "lab", check, why, **kw)


DOORS = (
    _o("c3f_submit_any_org", "   where j.request_id = v_job and public.is_org_member(j.org_id);",
       "   where j.request_id = v_job;", SUBMIT,
       "anyone signed in leaves feedback on another org's request"),
    _o("c3f_submit_forged_author", "    'org_id', v_org, 'principal', auth.uid()::text,",
       "    'org_id', v_org, 'principal', v_org::text,",
       SUBMIT, "the stored author is not the session's user"),
    _o("c3f_submit_extra_keys", "                where k not in ('request_id', 'name', 'value', "
       "'comment', 'idempotency_key'))", "                where false)", SUBMIT,
       "a provenance field in the body is silently accepted"),
    _o("c3f_submit_api_channel", "    'channel', 'console', 'request_id', v_job,",
       "    'channel', 'api', 'request_id', v_job,", SUBMIT,
       "console feedback is recorded as an API call"),
    _o("c3f_submit_anon", "grant execute on function public.submit_feedback(jsonb) to "
       "authenticated;", "grant execute on function public.submit_feedback(jsonb) to "
       "authenticated, anon;", SUBMIT, "an anonymous caller reaches the door"),
    _o("c3f_review_any_role", "                    and m.role in ('developer', "
       "'administrator')) then", "                    ) then", REVIEW,
       "a viewer reads customer feedback"),
    _o("c3f_review_any_category", "                 and 'feedback' = any(g.categories) and "
       "'provider_sharing' = any(g.purposes)", "                 and 'provider_sharing' = "
       "any(g.purposes)", REVIEW, "feedback is read under a grant that never shared it"),
    _o("c3f_review_expiry_ignored", "                 and (g.expires_at is null or infrx.now() "
       "< g.expires_at)\n", "", REVIEW, "an expired grant still shows feedback"),
    _o("c3f_review_revocation_ignored", "                 and (g.revoked_at is null or "
       "infrx.now() < g.revoked_at)\n", "", REVIEW, "a revoked grant still shows feedback"),
    _o("c3f_review_first_version", "           order by g.version desc limit 1);",
       "           order by g.version asc limit 1);", REVIEW,
       "the first grant version decides forever"),
    _o("c3f_review_any_recipient", "           where g.grantor_org_id = j.org_id and "
       "g.recipient_provider_org_id = v_provider", "           where g.grantor_org_id = "
       "j.org_id", REVIEW, "one provider reads feedback shared with another"),
    _o("c3f_review_labels", "           'request_id', v_job, 'org_id', v_org))) with ordinality",
       "           'request_id', v_job, 'org_id', v_org, 'calibration', true))) with "
       "ordinality", REVIEW, "a provider reads the operator's calibration labels"),
)
DOORS_NAMES = tuple(m.name for m in DOORS)


def kill_doors(mutant) -> tuple[str, str]:
    from . import test_d6f_doors as doors_world
    return d7.kill(mutant, DB_D, doors_world)
