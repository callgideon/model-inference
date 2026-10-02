#!/usr/bin/env python3
"""R32/R83 for AP-01: one single-edit defect per decision `tests/ap01` claims, through the
shared runner (`tests/contracts/mutants.py`).

`MUTANTS` run the in-memory world only (`-m "not pg"`, no Docker). `PG_MUTANTS` are edits of
`PgIdentity`'s SQL and rules killed on PostgreSQL (`-m pg`, on the D harness of `INFRX_D_TASK`,
in a copy that carries the migrations).

    INFRX_MUTANTS=all INFRX_D_TASK=ap1 uv run --frozen pytest -q tests/ap01/test_mutants.py
    uv run --frozen python tests/ap01/mutants.py --list
"""
from __future__ import annotations

import pathlib
import re
import shutil
import sys

API_DIR = pathlib.Path(__file__).resolve().parents[2]
SUITES = ("tests/ap01/test_auth.py", "tests/ap01/test_identity.py")
if str(API_DIR) not in sys.path:        # `python tests/ap01/mutants.py`
    sys.path.insert(0, str(API_DIR))

from tests.contracts import mutants as shared  # noqa: E402
from tests.contracts.mutants import Mutant, Outcome, Result, Runner, _m  # noqa: E402,F401

F = "auth_facade/__init__.py"
R = "gateway/routes/auth.py"
C = "console/__init__.py"
S = "console/session.py"
M = "gateway/routes/console_me.py"
W = "gateway/routes/lab_workspaces.py"
OP = "gateway/routes/operator_providers.py"
ID = "state/identity.py"

SIGN_IN = "test_auth__sign_in_answers_tokens_in_the_body_no_store"
ENUMERATION = "test_auth__a_wrong_password_and_an_unknown_email_read_the_same"
OUTAGE = "test_auth__an_unreachable_or_failing_idp_is_503_never_bad_credentials"
CODES = "test_auth__unconfirmed_and_rate_limited_keep_their_fixed_codes"
SIGNUP_EXISTING = "test_auth__sign_up_of_an_existing_email_reads_sent"
FORWARD = "test_auth__sign_up_forwards_captcha_challenge_and_allowlisted_redirect"
CAPTCHA = "test_auth__a_required_challenge_guards_every_password_door_and_reveals_no_account"
SIGNUP_FAILURES = "test_auth__sign_up_failures_map_like_the_app"
RECOVERY = "test_auth__recovery_of_an_unknown_email_reads_sent"
REDIRECT = "test_auth__a_redirect_outside_the_allowlist_never_reaches_the_idp"
CSRF = "test_auth__a_cross_origin_submission_never_reaches_the_idp"
CALLBACK = "test_auth__callback_exchanges_the_code_with_the_forwarded_verifier"
LINKS = "test_auth__callback_links_expire_or_are_invalid_like_the_app"
REFRESH = "test_auth__refresh_rotates_and_a_used_refresh_token_is_unauthenticated"
SIGN_OUT = "test_auth__sign_out_is_idempotent"
PASSWORD = "test_auth__password_update_needs_the_session_and_maps_policy"
AVAILABILITY = "test_auth__availability_comes_from_the_idp_the_flag_and_the_captcha_setting"
SECRETS = "test_auth__no_password_or_token_reaches_a_log_or_an_error"
INVALID_BODY = "test_auth__an_invalid_body_is_the_r270_envelope_without_its_values"
MIRROR = "test_auth__failure_codes_mirror_the_app_flow"

ME_OWN = "test_identity__me_is_the_sessions_own_account"
ME_STATES = "test_identity__me_states_are_server_owned"
KEY_DOOR = "test_identity__a_key_or_a_dead_session_is_not_a_web_session"
CONSOLE_CAPS = "test_identity__console_capabilities_follow_the_account_and_the_switches"
WORKSPACES = "test_identity__workspaces_are_current_memberships_with_their_capabilities"
LAB_CAPS = "test_identity__lab_capabilities_are_the_members_own"
MEMBERS_READ = "test_identity__members_are_read_by_members_only"
ADMIN_ADDS = "test_identity__an_administrator_adds_a_member_once"
ONLY_ADMIN = "test_identity__only_an_administrator_changes_members_and_never_their_own"
REVOKED = "test_identity__a_revoked_member_is_refused_at_once"
MUTATION_GUARDS = "test_identity__member_mutations_need_an_idempotency_key_and_the_origin"
OPERATOR_CREATES = "test_identity__an_operator_creates_a_provider_once"
ONLY_OPERATOR = "test_identity__only_an_operator_creates_providers"
NEMO = "test_identity_pg__an_existing_provider_is_never_reassigned"
REUSED = "test_identity__a_reused_key_with_another_request_is_409_and_writes_nothing"
REPLAY = "test_identity__a_replay_answers_the_first_outcome_and_never_redoes_it"
REFUSED = "test_identity__a_refused_mutation_claims_no_key"
CLAIMED = "test_identity_pg__a_first_mutation_is_one_finished_operation_under_its_key"
LAB_LOGIN = "test_identity_pg__the_lab_login_runs_the_identity_doors_and_reads_no_table"
FAKE_ONLY = (KEY_DOOR, MUTATION_GUARDS, ONLY_OPERATOR)
PG_ONLY = (NEMO, CLAIMED, LAB_LOGIN)

MUTANTS: tuple[Mutant, ...] = (
    # --- 01a: the facade ---------------------------------------------------------------------
    _m("session_user_is_not_the_idps", "the session's user id is the IdP's user",
       F, 'user_id=body["user"]["id"])', 'user_id=body["access_token"])', SIGN_IN),
    _m("unknown_user_is_its_own_failure", "flow.ts: user_not_found reads as a wrong password",
       F, '"user_not_found": "invalid_credentials"', '"user_not_found": "unavailable"',
       ENUMERATION, MIRROR),
    _m("outage_reads_as_bad_credentials", "an unreachable IdP is 503, never a 401",
       F, '            raise AuthRefused("unavailable") from None\n        if',
       '            raise AuthRefused("invalid_credentials") from None\n        if', OUTAGE),
    _m("idp_5xx_passes_through", "a 5xx answer is an outage before any per-call mapping",
       F, "if answer.status_code >= 500:", "if answer.status_code >= 600:", OUTAGE),
    _m("unconfirmed_is_unavailable", "flow.ts: email_not_confirmed keeps its code",
       F, '"email_not_confirmed": "email_not_confirmed"', '"email_not_confirmed": "unavailable"',
       CODES, MIRROR),
    _m("rate_limit_without_retry_after", "429 tells the caller when to retry",
       F, 'headers["Retry-After"] = str(RETRY_AFTER_S)', 'headers["X-Retry"] = "60"', CODES),
    _m("bare_429_is_unavailable", "flow.ts: a 429 without a code is rate limited",
       F, "if status == 429:", "if status == 428:", MIRROR),
    _m("existing_email_is_refused", "flow.ts signupSettled: an existing address reads sent",
       F, "if answer.status_code != 200 and _code(answer) not in EXISTING:",
       "if answer.status_code != 200:", SIGNUP_EXISTING),
    _m("captcha_not_forwarded", "LR-02: the CAPTCHA token reaches the IdP's own field",
       F, 'extras["gotrue_meta_security"] = {"captcha_token": captcha_token}',
       'extras["captcha_token"] = captcha_token', FORWARD),
    _m("failed_challenge_is_unavailable", "LR-02: a failed challenge is captcha_failed, not 503",
       F, '    "captcha_failed": "captcha_failed",\n}', "}", FORWARD, CAPTCHA, MIRROR),
    _m("sign_in_drops_the_challenge", "the hosted policy checks sign-in: its token is forwarded",
       F, '"password": password, **self._extras(captcha_token, None)})',
       '"password": password})', CAPTCHA),
    _m("sign_in_route_drops_the_challenge", "the sign-in body's token reaches the facade",
       R, "captcha_token=body.captcha_token))", "captcha_token=None))", CAPTCHA),
    _m("widget_without_site_key", "a widget needs its public site key",
       F, "ready = captcha_provider in CAPTCHA_PROVIDERS and bool(captcha_site_key.strip())",
       "ready = captcha_provider in CAPTCHA_PROVIDERS", AVAILABILITY),
    _m("signup_closed_is_unavailable", "flow.ts: signup_disabled is signup_closed",
       F, '"signup_disabled": "signup_closed"', '"signup_disabled": "unavailable"',
       SIGNUP_FAILURES, MIRROR),
    _m("unknown_recovery_is_refused", "flow.ts emailSettled: an unknown address reads sent",
       F, '            if reason != "invalid_credentials":\n',
       "            if True:\n", RECOVERY),
    _m("redirect_unchecked", "redirect_to only on a configured web origin",
       F, '" " in url or f"{parts.scheme}://{parts.netloc}" \\\n                not in self.origins:',
       '" " in url or f"{parts.scheme}://{parts.netloc}" \\\n                in ():', REDIRECT),
    _m("origin_unchecked", "a cross-origin POST is refused before the IdP is called",
       F, 'if origin is not None and origin.rstrip("/") not in self.origins:', "if False:",
       CSRF),
    _m("code_without_verifier", "a PKCE code is exchanged only with the forwarded verifier",
       F, "            if not verifier:\n                raise AuthRefused(\"link_invalid\")\n",
       "", CALLBACK),
    _m("recovery_lands_on_next", "a recovery link lands on set-a-new-password",
       F, '("/update-password" if kind == "recovery"', '("/update-password" if kind == "x"',
       CALLBACK),
    _m("next_is_any_string", "flow.ts safeNext: only a same-site path",
       F, "if not isinstance(path, str) or not SAME_SITE.fullmatch(path) or SNEAKY.search(path):",
       "if not isinstance(path, str):", CALLBACK),
    _m("every_link_error_is_expired", "only an expiry code reads as expired",
       F, '            expired = failure(error_code, 400) == "link_expired"\n',
       "            expired = True\n", LINKS),
    _m("used_link_is_invalid", "a used email link is expired, not invalid",
       F, 'return AuthRefused("link_expired" if expired else "link_invalid")',
       'return AuthRefused("link_invalid")', LINKS),
    _m("dead_refresh_is_unavailable", "a used refresh token means sign in again",
       F, '            raise AuthRefused("unauthenticated")\n        return _session(answer)',
       '            raise AuthRefused("unavailable")\n        return _session(answer)', REFRESH),
    _m("sign_out_not_idempotent", "a session already gone signs out the same",
       F, "answer.status_code not in (401, 403, 404):", "answer.status_code not in ():",
       SIGN_OUT),
    _m("password_dead_session_unavailable", "a dead session at password update is 401",
       F, "        if answer.status_code in (401, 403):\n", "        if False:\n", PASSWORD),
    # --- 01a: the routes ------------------------------------------------------------------------
    _m("unknown_availability_is_disabled", "a failed read is unknown, never disabled",
       R, "        if on is None:\n", "        if False:\n", AVAILABILITY),
    _m("unpassable_challenge_reads_configured", "a required challenge with no widget closes "
       "every password door", R, "        if on and required and widget is None:\n",
       "        if False:\n", AVAILABILITY),
    _m("missing_site_key_reads_not_required", "required without a site key is unavailable",
       R, "    elif required:\n", "    elif False:\n", AVAILABILITY),
    _m("closed_signup_ignored", "the IdP's closed signup closes sign-up",
       R, 'email and idp.get("disable_signup") is False', "email", AVAILABILITY),
    _m("flag_failure_is_off", "the grant flag's failed read is unknown",
       R, "            grant = None\n", "            grant = False\n", AVAILABILITY),
    _m("validation_left_to_fastapi", "a malformed body is the R270 envelope, not {detail}",
       C, "                return render(exc, control.request_id(request))\n",
       "                raise\n", INVALID_BODY),
    _m("field_error_echoes_input", "a field error carries the type, never the value",
       C, 'code=str(e.get("type", "invalid"))', 'code=str(e.get("input", "invalid"))',
       SECRETS, INVALID_BODY),
    # --- 01b: the session actor and the console account --------------------------------------
    _m("me_reads_the_query_user", "the account is the session's, never a query value",
       M, "account = await rt.identity.account(actor.user_id)",
       'account = await rt.identity.account(request.query_params.get("user_id") '
       'or actor.user_id)', ME_OWN),
    _m("operator_bit_for_everyone", "the operator bit is the profile's",
       S, "operator=account.operator)", "operator=True)", ME_STATES),
    _m("unverified_reads_ready", "an unconfirmed email is unverified",
       M, "    if not account.verified:\n", "    if False:\n", ME_STATES),
    _m("onboarding_reads_ready", "no grant wallet yet is onboarding",
       M, 'return "ready" if account.wallet else "onboarding"', 'return "ready"', ME_STATES),
    _m("a_key_is_a_web_session", "wrong audience: an API key never opens a web door",
       S, 'if actor.audience != "session" or actor.user_id is None:',
       "if actor.user_id is None:", KEY_DOOR),
    _m("cross_origin_mutation_accepted", "CSRF: a foreign Origin never mutates",
       S, '        if request.method not in SAFE_METHODS and origin is not None \\\n'
          '                and origin.rstrip("/") not in self.origins:',
       '        if request.method not in SAFE_METHODS and origin is not None \\\n'
       '                and False:', MUTATION_GUARDS),
    _m("suspended_creates_keys", "a suspended account creates no key",
       M, '"create_key": ready and not account.suspended', '"create_key": ready',
       CONSOLE_CAPS),
    _m("suspended_cannot_revoke", "revocation stays allowed while suspended",
       M, '"revoke_key": account.wallet', '"revoke_key": ready and not account.suspended',
       CONSOLE_CAPS),
    _m("unverified_claims", "only a verified individual claims the grant",
       M, '"claim_signup_grant": account.verified and account.grant_amount is None',
       '"claim_signup_grant": account.grant_amount is None', CONSOLE_CAPS),
    _m("switch_ignored", "a feature's availability is its switch's",
       M, "if getattr(deployment, switch) else", "if True else", CONSOLE_CAPS),
    _m("lab_feature_by_switch_alone", "a Lab feature is configured only where its routes are",
       W, "if path in paths else", "if True else", LAB_CAPS),
    # --- 01c: Lab workspaces and members -------------------------------------------------------
    _m("every_role_is_administrator", "the capability set is the role's (ROLE_CAPABILITIES)",
       W, "ROLE_CAPABILITIES[ProviderRole(role)]",
       "ROLE_CAPABILITIES[ProviderRole.administrator]", WORKSPACES, LAB_CAPS),
    _m("capabilities_of_any_workspace", "forged provider: only the user's own membership",
       W, "next((w for w in found if w.provider_org_id == provider_org_id), None)",
       "next(iter(found), None)", LAB_CAPS),
    _m("members_unguarded", "only a current member reads the members",
       W, "        await access.require(actor.user_id, provider_org_id,\n"
          "                             ProviderCapability.read_aggregate_health)\n", "",
       MEMBERS_READ, REVOKED),
    _m("any_member_manages_members", "member changes need manage_members (administrator)",
       W, "ProviderCapability.manage_members)", "ProviderCapability.read_aggregate_health)",
       ONLY_ADMIN),
    _m("self_grant", "no one grants themself (no self-elevation)",
       W, "        if user_id == claim.actor.user_id:\n", "        if False:\n", ONLY_ADMIN),
    _m("self_revoke", "no one revokes themself",
       W, "        if str(user_id) == claim.actor.user_id:\n", "        if False:\n", ONLY_ADMIN),
    _m("mutation_without_key", "R270: a member mutation carries an Idempotency-Key",
       W, "        key = idempotency_key(request)\n",
       '        key = request.headers.get("idempotency-key", "")\n', MUTATION_GUARDS),
    _m("hash_ignores_the_body", "R270: the same key with another body is 409, never a replay",
       W, '{"provider_org_id": provider_org_id, **doc}', '{"provider_org_id": provider_org_id}',
       REUSED),
    _m("one_scope_for_every_action", "the key is scoped to the action: grant != revocation",
       W, '"lab.member_revoke",', '"lab.member_grant",', REUSED),
    _m("replay_reads_created", "a retried grant answers 200, the first 201",
       W, "return control.ok(member, 201 if created else 200)",
       "return control.ok(member, 201)", ADMIN_ADDS),
    _m("unknown_email_granted", "an unknown address is a 404, nothing written",
       W, "        if user_id is None:\n", "        if False:\n", ADMIN_ADDS),
    # --- 01d: operator onboarding --------------------------------------------------------------
    _m("anyone_onboards", "developer at the operator door: only the operator",
       OP, "        if not actor.operator:\n", "        if False:\n", ONLY_OPERATOR),
    _m("any_key_is_the_operator", "only an operator-audience key is the operator",
       S, 'operator=audience == "operator")', 'operator=audience != "session")',
       ONLY_OPERATOR),
    _m("onboarding_without_key", "R270: onboarding carries an Idempotency-Key",
       OP, "actor, idempotency_key(request),", 'actor, request.headers.get("idempotency-key", ""),',
       ONLY_OPERATOR),
    _m("onboarding_hash_ignores_the_body", "another provider under the same key is 409",
       OP, "input_hash(body.model_dump(mode=\"json\")))", "input_hash({}))", REUSED),
)

#: `PgIdentity`'s SQL and rules, killed on PostgreSQL by the same world cases (`-m pg`).
PG_MUTANTS: tuple[Mutant, ...] = (
    _m("pg_account_never_suspended", "suspension is the organization's",
       S, "coalesce(o.suspended, false)", "false", ME_STATES),
    _m("pg_account_always_verified", "verification is the auth server's confirmation",
       S, "v.verification_evidence_ref is not null", "true", ME_STATES),
    _m("pg_account_always_has_a_wallet", "onboarding until the grant made the wallet",
       S, "w.wallet_id is not null", "true", ME_STATES),
    _m("pg_account_never_operator", "the operator bit is profiles.is_operator",
       S, "select p.is_operator,", "select false,", ME_STATES),
    _m("pg_email_case_sensitive", "addresses fold case, as the auth server's do",
       S, "where lower(email) = lower(%(email)s)", "where email = %(email)s", ADMIN_ADDS),
    _m("pg_role_change_overwrites", "another current role is a 409, never an update",
       ID, "        if member.role != role:\n", "        if False:\n", ADMIN_ADDS),
    _m("pg_retry_reads_created", "a retry finds the first grant's row",
       S, "created = bool(await fetch(conn, GRANT, a))", "created = True", ADMIN_ADDS),
    _m("pg_grant_always_created", "a grant reads created only when its statement made it",
       ID, "return member, row[6]", "return member, True", ADMIN_ADDS),
    _m("pg_revocation_in_the_future", "a revocation takes effect at once",
       S, "set revoked_at = greatest(infrx.now(), granted_at)",
       "set revoked_at = greatest(infrx.now(), granted_at) + interval '1 day'", REVOKED),
    _m("pg_revoked_listed", "the members list is the current members",
       S, "and m.revoked_at is null order by", "order by", REVOKED),
    _m("pg_repeat_revocation_refused", "a repeated revocation answers the same row",
       S, "        return next(iter(await fetch(conn, LATEST, a)), None)",
       "        return next(iter(await fetch(conn, MEMBERS, a)), None)", REVOKED),
    _m("pg_existing_provider_renamed_through", "an existing slug under another name is 409",
       ID, "            if provider.display_name != display_name:\n", "            if False:\n",
       OPERATOR_CREATES),
    _m("pg_provider_retry_reads_created", "a retried onboarding answers 200",
       S, "                bool(made))", "                True)", OPERATOR_CREATES),
    _m("pg_provider_always_created", "onboarding reads created only when it made the row",
       ID, '"created": row[5],', '"created": True,', OPERATOR_CREATES, NEMO),
    _m("first_admin_is_a_developer", "the first member is the provider's administrator",
       ID, '"by": created_by}, "administrator"))[0]', '"by": created_by}, "developer"))[0]',
       OPERATOR_CREATES, LAB_LOGIN),
    # --- the Idempotency-Key bound to 0060 (API-KEYGRANT) -------------------------------------
    _m("pg_replay_redoes_the_write", "a replay answers the first outcome, never writes again",
       ID, '                        if started["replayed"]:\n',
       "                        if False:\n", REPLAY),
    _m("pg_refusal_claims_the_key", "a refused write stores no outcome under its key",
       ID, "                        if refusal is not None:\n                            raise "
          "refusal\n", "", REFUSED),
    _m("pg_operation_left_unfinished", "the claim's operation finishes succeeded",
       ID, '"state": "succeeded"})', '"state": "cancelled"})', CLAIMED),
    _m("pg_replay_reads_created", "a replayed grant answers 200",
       ID, 'outcome["created"] and not replayed\n', 'outcome["created"]\n', ADMIN_ADDS),
    _m("pg_provider_replay_reads_created", "a replayed onboarding answers 200",
       ID, 'outcome["created"] and not replayed)', 'outcome["created"])', OPERATOR_CREATES),
    _m("pg_scope_is_the_admins_org", "a member mutation's key is the workspace's",
       W, 'actor.model_copy(update={"provider_org_id": provider_org_id})', "actor", CLAIMED),
    _m("pg_hash_without_the_path", "the input hash covers the workspace in the path",
       W, '{"provider_org_id": provider_org_id, **doc}', "doc", CLAIMED),
)


def case_names() -> set[str]:
    return {name for suite in SUITES
            for name in re.findall(r"^def (test_\w+)\(", (API_DIR / suite).read_text(), re.M)}


RUNNER = Runner(name="ap01", targets=SUITES, extra_args=("-m", "not pg"))


def _pg_layout(root: pathlib.Path) -> pathlib.Path:
    """The default copy one level down as `apps/infrx-api`, beside a copy of the migrations
    `infrx.state.migrations` reads from `<repo>/apps/app/supabase/migrations`."""
    api = root / "apps" / "infrx-api"
    api.mkdir(parents=True)
    shared._copy(api, RUNNER)
    migrations = pathlib.Path("apps", "app", "supabase", "migrations")
    shutil.copytree(API_DIR.parents[1] / migrations, root / migrations)
    return api


PG_RUNNER = Runner(name="ap01-pg", targets=("tests/ap01/test_identity.py",),
                   extra_args=("-m", "pg"), env=("INFRX_D_TASK", "INFRX_D1_IMAGE"),
                   layout=_pg_layout)


def run_mutant(mutant) -> Result:
    """The PostgreSQL list is not a module's `MUTANTS`, so the shared runner takes no baseline
    for it: its cases run unmutated first, once per process (R83 (b))."""
    if mutant not in PG_MUTANTS:
        return shared.run_mutant(mutant, RUNNER)
    cases = tuple(sorted({case for m in PG_MUTANTS for case in m.cases}))
    return shared.pristine(cases, PG_RUNNER) or shared.run_mutant(mutant, PG_RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run AP-01's mutation list"))
