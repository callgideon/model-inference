#!/usr/bin/env python3
"""R32/R83 for AP-01: one single-edit defect per decision `tests/ap01` claims, through the
shared runner (`tests/contracts/mutants.py`).

    INFRX_MUTANTS=all uv run --frozen pytest -q tests/ap01/test_mutants.py
    uv run --frozen python tests/ap01/mutants.py --list
"""
from __future__ import annotations

import pathlib
import re
import sys

API_DIR = pathlib.Path(__file__).resolve().parents[2]
AUTH_SUITE = "tests/ap01/test_auth.py"
SUITES = (AUTH_SUITE,)
if str(API_DIR) not in sys.path:        # `python tests/ap01/mutants.py`
    sys.path.insert(0, str(API_DIR))

from tests.contracts import mutants as shared  # noqa: E402
from tests.contracts.mutants import Mutant, Outcome, Result, Runner, _m  # noqa: E402,F401

F = "auth_facade/__init__.py"
R = "gateway/routes/auth.py"
C = "console/__init__.py"
STUB = "auth_facade/stub.py"

SIGN_IN = "test_auth__sign_in_answers_tokens_in_the_body_no_store"
ENUMERATION = "test_auth__a_wrong_password_and_an_unknown_email_read_the_same"
OUTAGE = "test_auth__an_unreachable_or_failing_idp_is_503_never_bad_credentials"
CODES = "test_auth__unconfirmed_and_rate_limited_keep_their_fixed_codes"
SIGNUP_EXISTING = "test_auth__sign_up_of_an_existing_email_reads_sent"
FORWARD = "test_auth__sign_up_forwards_captcha_challenge_and_allowlisted_redirect"
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
)


def case_names() -> set[str]:
    return {name for suite in SUITES
            for name in re.findall(r"^def (test_\w+)\(", (API_DIR / suite).read_text(), re.M)}


RUNNER = Runner(name="ap01", targets=SUITES, extra_args=("-m", "not pg"))


def run_mutant(mutant) -> Result:
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run AP-01's mutation list"))
