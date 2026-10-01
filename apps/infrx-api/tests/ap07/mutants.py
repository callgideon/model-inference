#!/usr/bin/env python3
"""R32/R40/R83 for AP-07: one single-edit defect per decision the data-use controls and the
Lab trace projection claim, each killed by a named case of `tests/ap07`.

The shared runner, one mutant at a time in a throwaway copy after a pristine baseline. The
data-use cases are PostgreSQL's (the copy inherits `INFRX_D_TASK`; without Docker they skip and
the runner reports `misdeclared`, never a pass).

    INFRX_D_TASK=ap7 uv run --frozen pytest -q tests/ap07/test_mutants.py
    INFRX_D_TASK=ap7 INFRX_MUTANTS=all uv run --frozen pytest -q tests/ap07/test_mutants.py
    INFRX_D_TASK=ap7 uv run --frozen python -m tests.ap07.mutants --list
"""
from __future__ import annotations

import pathlib
import re

from ..contracts import mutants as shared
from ..contracts.mutants import Mutant, Result, Runner
from ..g.feedback.mutants import _layout

API_DIR = pathlib.Path(__file__).resolve().parents[2]
SUITE_FILES = ("tests/ap07/test_data_use.py",)
D = "console/data_use.py"
R = "gateway/routes/console_data_use.py"
FILES = (D, R)
U = "test_data_use__"
OWNER = U + "only_the_grantors_owner_decides"
CAPTURE = U + "capture_is_a_versioned_consent_the_gateway_reads"
HEAD = U + "the_head_is_the_highest_live_key_mode"
REPLAY = U + "a_replay_writes_nothing_and_a_stale_view_conflicts"
GRANTS = U + "grants_are_purpose_specific_versions"
EXPIRY = U + "a_grant_expires_on_the_databases_clock"
REVOKE = U + "revocation_is_the_grantors_and_holds_at_once"


def _m(name, invariant, file, old, new, *cases, dies_by=()) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=file, old=old, new=new, cases=cases,
                  dies_by=tuple(dies_by))


MUTANTS: tuple[Mutant, ...] = (
    # --- mounting and the envelope ------------------------------------------------------------
    _m("mounted_without_data_use", "no rt.data_use (the switch off): no route", R,
       "    if service is None:\n        return None", "    if False:\n        return None",
       U + "nothing_is_mounted_without_data_use"),
    _m("validation_not_enveloped", "a body that does not validate is the R270 envelope", R,
       "APIRouter(route_class=EnvelopeRoute)", "APIRouter()",
       U + "evaluation_consent_needs_full_capture"),
    _m("field_errors_dropped", "a 422 names the fields that failed", R,
       'fields = tuple(api.FieldError(', 'fields = () and tuple(api.FieldError(',
       U + "evaluation_consent_needs_full_capture"),
    _m("replayed_grant_is_created", "a new version is 201, a replay 200", R,
       "status = 201 if created else 200", "status = 201",
       GRANTS),
    # --- who decides --------------------------------------------------------------------------
    _m("key_audience_decides", "only a verified web session decides data use", D,
       'if actor.audience != "session" or not actor.user_id', "if not actor.user_id", OWNER),
    _m("any_member_decides", "the organization's owner decides, not any member", D,
       "if row is None or not row[1]:", "if row is None:", OWNER),
    _m("member_role_is_owner", "a plain member is not an owner", D,
       "and m.user_id = %s and m.role = 'owner')", "and m.user_id = %s and m.role <> 'x')",
       OWNER),
    _m("suspension_ignored", "a suspended organization writes nothing (R33)", D,
       "        if write and row[0]:\n", "        if False:\n",
       U + "a_suspended_organization_decides_nothing"),
    _m("another_orgs_key", "a key of another organization is not found", D,
       '"org_id = %s and revoked_at is null for update", (key_id, org))',
       '"%s::uuid is not null and revoked_at is null for update", (key_id, org))', OWNER),
    # --- capture: a versioned consent decision ------------------------------------------------
    _m("head_is_the_key_mode", "the head is the highest live key mode", D,
       "mode = max((body.mode, *(m for (m,) in others)), key=MODES.index)",
       "mode = body.mode", HEAD),
    _m("key_mode_not_written", "the key's own mode is set with the consent", D,
       'await conn.execute("update public.api_keys set trace_mode = %s where id = %s",',
       'await conn.execute("select %s::text, %s::uuid",', CAPTURE),
    _m("retention_not_recorded", "the consent carries the chosen retention", D,
       "(org, body.consent_version + 1, mode, body.retention_days,",
       "(org, body.consent_version + 1, mode, 30,", CAPTURE),
    _m("principal_is_the_org", "the consent names the owner who decided", D,
       "body.evaluation_consent, user))", "body.evaluation_consent, org))", CAPTURE),
    _m("effective_is_the_key_mode", "effective_mode is what the gateway admits", D,
       "effective_mode=effective(", "effective_mode=mode or 'off' or effective(", CAPTURE),
    _m("stale_view_writes", "a decision on a stale version is a 409", D,
       "elif (head[0] if head else 0) != body.consent_version:", "elif False:", REPLAY),
    _m("replay_rewrites", "the same decision again writes nothing", D,
       'if (key[0] or "off") == body.mode and head is not None',
       'if False and head is not None', REPLAY),
    _m("evaluation_without_full", "no inferred evaluation consent (0003)", D,
       'if self.evaluation_consent and self.mode != "full":', "if False:",
       U + "evaluation_consent_needs_full_capture"),
    # --- purpose grants -----------------------------------------------------------------------
    _m("every_purpose_granted", "a grant permits only its named purposes", D,
       '"purposes": [p.value for p in body.purposes]', '"purposes": [p.value for p in DataPurpose]',
       GRANTS),
    _m("models_not_forwarded", "the grant's models are the ones asked (0027 judges them)", D,
       '"model_ids": list(body.model_ids)', '"model_ids": []',
       U + "a_grant_names_only_the_recipients_models"),
    _m("grant_replay_rewrites", "the same grant again writes no version", D,
       "if current is not None and current.is_current(now) and same_scope(current, body):",
       "if False:", GRANTS),
    _m("stale_grant_writes", "a grant on a stale version is a 409", D,
       "if (current.version if current else 0) != body.grant_version:", "if False:", GRANTS),
    _m("scope_ignores_purposes", "a different purpose set is a different grant", D,
       "(set(g.model_ids), set(g.categories), set(g.purposes), g.retention_days,",
       "(set(g.model_ids), set(g.categories), set(body.purposes), g.retention_days,", GRANTS),
    _m("expired_reads_active", "a grant past its expiry is expired", D,
       '"active" if g.is_current(now)', '"active" if g.revoked_at is None', EXPIRY),
    _m("revoked_reads_expired", "a revoked grant says so", D,
       'else "revoked" if g.revoked_at is not None else "expired"', 'else "expired"', REVOKE),
    _m("older_version_listed", "a grant is its latest version", D,
       "g.version = (select max(h.version)", "g.version = (select min(h.version)", GRANTS),
    _m("every_grantors_grants", "the grants listed are the grantor's own", D,
       "where g.grantor_org_id = %s and g.version", "where %s::uuid is not null and g.version",
       U + "the_read_is_the_grantors_own"),
    _m("revoke_another_grantors", "another grantor's grant is not found", D,
       '"infrx.lab_access_grants where grant_id = %s and grantor_org_id = %s "',
       '"infrx.lab_access_grants where grant_id = %s and %s::uuid is not null "', REVOKE),
    _m("revoke_replay_rewrites", "a second DELETE answers the revoked grant", D,
       "if current is None or current.revoked_at is None:", "if True:", REVOKE),
)


def case_names() -> set[str]:
    return {name for path in SUITE_FILES
            for name in re.findall(r"^def (test_\w+)\(", (API_DIR / path).read_text(), re.M)}


RUNNER = Runner(name="ap07", targets=SUITE_FILES, layout=_layout, env=("INFRX_D_TASK", "INFRX_D1_IMAGE"))


def run_mutant(mutant: Mutant) -> Result:
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run AP-07's mutation list"))
