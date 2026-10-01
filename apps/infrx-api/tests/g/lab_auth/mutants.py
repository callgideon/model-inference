#!/usr/bin/env python3
"""R32/R40/R83 for LAB-API's auth seam: one single-edit defect per invariant `lab_auth` claims.

The shared runner, one mutant at a time in a throwaway copy after a pristine baseline. The
copies run the fake half of every `world` case; the PostgreSQL half runs there only on
request (`INFRX_LAB_API_PG=1` with `INFRX_D_TASK=l4`, which the copy then inherits) - inside a
whole-suite run the parent process holds the key's container. `INFRX_LAB_API_STACK=1` (the
ClickHouse case of tests/g/lab_traces) is inherited whenever it is set.

    uv run --frozen pytest -q tests/g/lab_auth/test_mutants.py
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/g/lab_auth/test_mutants.py
    uv run --frozen python -m tests.g.lab_auth.mutants --list
"""
from __future__ import annotations

import os
import pathlib
import re

from ...contracts import mutants as shared
from ...contracts.mutants import Mutant, Result, Runner
from ..feedback.mutants import _layout

API_DIR = pathlib.Path(__file__).resolve().parents[3]
SUITE_FILES = ("tests/g/lab_auth/test_lab_auth.py",)
F = "gateway/lab_auth.py"
A = "test_lab_auth__"


def runner(name: str, suite_files: tuple[str, ...]) -> Runner:
    """The three LAB-API lists' runner: the fake half unless the PostgreSQL half is asked."""
    pg = os.environ.get("INFRX_LAB_API_PG") == "1"
    return Runner(name=name, targets=suite_files, layout=_layout,
                  env=("INFRX_LAB_API_STACK", *(("INFRX_D_TASK",) if pg else ())),
                  extra_args=() if pg else ("-m", "not pg"))


def case_names_in(suite_files: tuple[str, ...]) -> set[str]:
    return {name for path in suite_files
            for name in re.findall(r"^def (test_\w+)\(", (API_DIR / path).read_text(), re.M)}


def _m(name, invariant, old, new, *cases, dies_by=()) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=F, old=old, new=new, cases=cases,
                  dies_by=dies_by)


MUTANTS: tuple[Mutant, ...] = (
    # --- the token: a session JWT, never an API key, never forwarded otherwise ----------
    _m("bearer_accepts_any_token", "only a compact JWT is a Lab credential",
       'BEARER = re.compile(r"Bearer ([A-Za-z0-9_-]{1,4096}\\.[A-Za-z0-9_-]{1,8192}\\.'
       '[A-Za-z0-9_-]{1,4096})")', 'BEARER = re.compile(r"Bearer (.+)")',
       A + "only_a_bearer_session_jwt_reaches_the_verifier"),
    _m("bearer_prefix_match", "the whole header is the token, nothing trails it",
       "BEARER.fullmatch(", "BEARER.search(",
       A + "only_a_bearer_session_jwt_reaches_the_verifier"),
    # --- the verifier: a live, authenticated session only ---------------------------------
    _m("anon_role_accepted", "the anon key's JWT is not a user's session",
       ' or user.get("role") != SESSION', "",
       A + "gotrue_answers_only_for_a_live_authenticated_session"),
    _m("audience_unchecked", "only the `authenticated` audience is a session",
       'if user.get("aud") != SESSION or ', "if ",
       A + "gotrue_answers_only_for_a_live_authenticated_session"),
    _m("user_id_unchecked", "an answer without a user id is no user",
       ' \\\n                or not isinstance(user.get("id"), str)', "",
       A + "gotrue_answers_only_for_a_live_authenticated_session"),
    _m("forbidden_session_is_an_outage", "a 403 from the auth server is unauthenticated",
       "if answer.status_code in (401, 403):", "if answer.status_code == 401:",
       A + "gotrue_answers_only_for_a_live_authenticated_session"),
    _m("outage_read_as_an_answer", "a 5xx is unavailable, never a user or a refusal",
       "        if answer.status_code != 200:\n", "        if False:\n",
       A + "gotrue_answers_only_for_a_live_authenticated_session"),
    _m("apikey_not_sent", "the auth server is asked with the project key",
       '"apikey": self.apikey,', '"apikey": "",',
       A + "gotrue_answers_only_for_a_live_authenticated_session"),
    _m("another_token_verified", "the auth server is asked about THIS token",
       '"authorization": f"Bearer {token}"}', '"authorization": "Bearer " + self.apikey}',
       A + "gotrue_answers_only_for_a_live_authenticated_session"),
    # --- the actor --------------------------------------------------------------------------
    _m("consumer_only_not_denied", "a user with no provider membership is a 403",
       "    if not workspaces:\n", "    if False:\n",
       A + "a_consumer_only_user_is_denied_everywhere"),
    _m("foreign_provider_is_forbidden", "another provider's id is a 404, confirming nothing",
       'raise errors.NotFound("no such provider workspace")',
       'raise errors.Forbidden("no such provider workspace")',
       A + "another_providers_workspace_is_not_found"),
    _m("provider_unchecked", "the membership is the named provider's",
       "if w.membership.provider_org_id == provider_org_id", "if True",
       A + "another_providers_workspace_is_not_found"),
    _m("capability_unchecked", "the role must hold the operation's capability",
       "    if capability not in ROLE_CAPABILITIES[membership.role]:\n", "    if False:\n",
       A + "the_role_must_hold_the_capability"),
    _m("memberships_cached", "nothing is cached: a revocation refuses the next call",
       "    workspaces = await access.workspaces(user_id)\n",
       "    workspaces = member.__dict__.setdefault(user_id, await access.workspaces(user_id))\n",
       A + "a_revocation_takes_effect_on_the_next_call"),
    # --- the refusal ------------------------------------------------------------------------
    _m("unauthenticated_is_denied", "no session is a 401, not a 403",
       '(errors.InvalidApiKey, 401, "unauthenticated")', '(errors.InvalidApiKey, 403, "denied")',
       A + "refusals_are_the_lab_ports_reasons"),
    _m("invalid_is_a_400", "an invalid call is port.ts's 422",
       '(errors.InvalidRequest, 422, "invalid")', '(errors.InvalidRequest, 400, "invalid")',
       A + "refusals_are_the_lab_ports_reasons"),
    _m("conflict_reason_renamed", "a conflict is port.ts's `conflict`",
       '(errors.Conflict, 409, "conflict")', '(errors.Conflict, 409, "state_conflict")',
       A + "refusals_are_the_lab_ports_reasons"),
    _m("not_found_last", "a 404 is not swallowed by a broader kind",
       '(errors.InvalidApiKey, 401, "unauthenticated"), (errors.NotFound, 404, "not_found")',
       '(errors.InvalidApiKey, 401, "unauthenticated"), (errors.DomainError, 404, "not_found")',
       A + "refusals_are_the_lab_ports_reasons"),
    _m("gone_is_unavailable", "an expired record is 410 gone, not a retryable 503 (A7)",
       '(errors.Gone, 410, "gone")', '(errors.Gone, 503, "unavailable")',
       A + "refusals_are_the_lab_ports_reasons"),
    _m("bug_is_a_500", "anything else is 503 unavailable, the one reason the Lab retries",
       '(503, "unavailable"))', '(500, "internal"))',
       A + "refusals_are_the_lab_ports_reasons"),
    _m("refusal_cacheable", "a refusal is never cached",
       '    return JSONResponse({"refusal": reason}, status_code=status, headers=NO_STORE)',
       '    return JSONResponse({"refusal": reason}, status_code=status)',
       A + "refusals_are_the_lab_ports_reasons"),
    _m("record_cacheable", "a Lab record is never cached",
       "    return JSONResponse(content, status_code=status_code, headers=NO_STORE)",
       "    return JSONResponse(content, status_code=status_code)",
       A + "refusals_are_the_lab_ports_reasons"),
    _m("message_logged", "the log names the type, never a message that may carry the token",
       'log.error("lab route failed: %s", type(exc).__name__)',
       'log.error("lab route failed: %s", exc)',
       A + "the_token_is_never_logged"),
    _m("traceback_logged", "no traceback (its frames hold the token)",
       'log.error("lab route failed: %s", type(exc).__name__)',
       'log.exception("lab route failed: %s", type(exc).__name__)',
       A + "the_token_is_never_logged"),
    _m("failure_reraised", "nothing but a record or a refusal leaves a Lab handler",
       "            return refusal(exc)\n", "            raise\n",
       A + "the_token_is_never_logged", dies_by=("RuntimeError",)),
)


def case_names() -> set[str]:
    return case_names_in(SUITE_FILES)


RUNNER = runner("lab-auth", SUITE_FILES)


def run_mutant(mutant) -> Result:
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run LAB-API's auth mutation list"))
