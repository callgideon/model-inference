"""E3L l01, l07, l08: provider permissions and consumer denials on the merged L1/L2 code
(LAB-ACCESS, SPLIT-CONTRACT), on E3C's composed world in namespace e3l.

l01 two providers: each session sees only its own workspace (the Lab web's door) and a member
    of one provider is refused every operation on the other; a revoked membership is refused on
    its next call.
l07 consumer keys: a consumer `/v1` key reaches no provider control on the gateway and is no
    Lab session; a consumer owner has no provider workspace. The control service's own
    refusal is l12 (L3).
l08 consumer data: no grant, no content; a grant is purpose-bound and its revocation denies the
    next call; a provider session reads none of a consumer's rows directly.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lab_world as lab                                 # noqa: E402

world, stack = lab.world, lab.stack
A, B = lab.PROVIDER_A, lab.PROVIDER_B


# ------------------------------------------------------------------ l01 two providers


def test_l01_each_provider_session_sees_only_its_own_workspace(workdir, record_property):
    with lab.lab_trip(workdir) as trip:
        url, access = lab.rest_url(), lab.access(trip)
        seen = {name: lab.session_workspaces(url, user) for name, user in (
            ("dev_a", lab.DEV_A), ("dev_b", lab.DEV_B), ("viewer_a", lab.VIEWER_A),
            ("alpha", trip.world.alpha.user_id))}
        record_property("session_door", seen)
        assert seen == {"dev_a": [A], "dev_b": [B], "viewer_a": [A], "alpha": []}, seen
        for name, user in (("dev_a", lab.DEV_A), ("dev_b", lab.DEV_B)):
            server = sorted(w.membership.provider_org_id for w in lab.call(access.workspaces(user)))
            assert server == seen[name], (name, server, seen[name])


def test_l01_a_member_of_one_provider_is_refused_every_operation_on_the_other(workdir,
                                                                             record_property):
    with lab.lab_trip(workdir) as trip:
        access, alpha, beta = lab.access(trip), trip.world.alpha, trip.world.beta
        answers = {
            "a_reads_b_health": lab.refused(access.aggregates(lab.DEV_A, B)),
            "b_reads_a_health": lab.refused(access.aggregates(lab.DEV_B, A)),
            "a_reads_b_grants": lab.refused(access.grant_history(lab.DEV_A, B, beta.org_id)),
            "b_reads_a_grants": lab.refused(access.grant_history(lab.DEV_B, A, alpha.org_id)),
            "b_reads_a_content": content(access, lab.DEV_B, A, alpha.org_id, lab.MODEL_A,
                                         "provider_sharing"),
            "viewer_reads_grants": lab.refused(access.grant_history(lab.VIEWER_A, A, alpha.org_id)),
            "a_reads_own_health": lab.refused(access.aggregates(lab.DEV_A, A)),
        }
        record_property("answers", answers)
        assert answers == {"a_reads_b_health": "NotFound", "b_reads_a_health": "NotFound",
                           "a_reads_b_grants": "NotFound", "b_reads_a_grants": "NotFound",
                           "b_reads_a_content": "Forbidden", "viewer_reads_grants": "Forbidden",
                           "a_reads_own_health": "allowed"}, answers


def test_l01_a_revoked_membership_is_refused_on_its_next_call(workdir):
    with lab.lab_trip(workdir) as trip:
        access = lab.access(trip)
        assert lab.refused(access.aggregates(lab.DEV_B, B)) == "allowed"
        lab.sql(trip, "update infrx.provider_memberships set revoked_at = infrx.now() "
                "where provider_org_id = %s and user_id = %s", B, lab.DEV_B)
        assert lab.refused(access.aggregates(lab.DEV_B, B)) == "NotFound"
        assert lab.session_workspaces(lab.rest_url(), lab.DEV_B) == []


# ------------------------------------------------------------------ l07 consumer keys

#: Where a provider control could be reached from the consumer origin: none is mounted, so each
#: is the gateway's 404 for a valid consumer key (and G's /internal is loopback-only anyway).
CONTROL_PATHS = ("/lab/v1/deployments", "/v1/lab/deployments", "/v1/deployments",
                 "/v1/providers", "/v1/models/nemostation%2Fmarlin-2b/publish",
                 "/v1/deployments/rollback", "/internal/lab/deployments")


def test_l07_a_consumer_key_reaches_no_provider_control_on_the_gateway(workdir, record_property):
    with world.composed(workdir) as trip:
        alpha = trip.world.alpha
        served = trip.send(alpha, "sync", world.TEXT, None)
        assert served.status_code == 200, f"premise: the key serves inference: {served.text[:200]}"
        answers = {}
        for path in CONTROL_PATHS:
            for method in ("GET", "POST"):
                answer = trip.http.request(method, path, headers=trip.headers(alpha),
                                           json={"model": stack.CREDIT_ALIAS})
                answers[f"{method} {path}"] = answer.status_code
        record_property("answers", answers)
        assert set(answers.values()) <= {404, 405}, answers


def test_l07_a_consumer_key_is_no_lab_session(workdir, record_property):
    with lab.lab_trip(workdir) as trip:
        url, alpha = lab.rest_url(), trip.world.alpha
        as_key = lab.rest(url, None, "/rpc/lab_provider_memberships", token=alpha.secret,
                          method="POST")
        as_anon = lab.rest(url, None, "/rpc/lab_provider_memberships",
                           token=stack.jwt("anon"), method="POST")
        record_property("answers", {"consumer_key": as_key.status_code,
                                    "anon": as_anon.status_code})
        assert as_key.status_code == 401, as_key.text[:300]
        assert as_anon.status_code in (401, 403), as_anon.text[:300]
        assert alpha.secret not in as_key.text


def test_l07_a_consumer_owner_has_no_provider_workspace(workdir):
    with lab.lab_trip(workdir) as trip:
        access, alpha = lab.access(trip), trip.world.alpha
        assert lab.session_workspaces(lab.rest_url(), alpha.user_id) == []
        assert lab.call(access.workspaces(alpha.user_id)) == ()
        for provider in (A, B):
            assert lab.refused(access.aggregates(alpha.user_id, provider)) == "NotFound"


# ------------------------------------------------------------------ l08 consumer data


def content(access, user: str, provider: str, grantor: str, model: str, purpose: str) -> str:
    from infrx.contracts.v2 import records as v2
    return lab.refused(access.authorize_content(
        user_id=user, provider_org_id=provider, grantor_org_id=grantor, model_id=model,
        category=v2.DataCategory.request_content, purpose=v2.DataPurpose(purpose)))


def test_l08_no_grant_no_content(workdir, record_property):
    with lab.lab_trip(workdir) as trip:
        access, alpha, beta = lab.access(trip), trip.world.alpha, trip.world.beta
        answers = {
            "a_granted": content(access, lab.DEV_A, A, alpha.org_id, lab.MODEL_A, "provider_sharing"),
            "a_ungranted_grantor": content(access, lab.DEV_A, A, beta.org_id, lab.MODEL_A,
                                           "provider_sharing"),
            "b_other_providers_grant": content(access, lab.DEV_B, B, alpha.org_id, lab.MODEL_B,
                                               "provider_sharing"),
            "a_other_model": content(access, lab.DEV_A, A, alpha.org_id, lab.MODEL_B,
                                     "provider_sharing"),
            "viewer_granted": content(access, lab.VIEWER_A, A, alpha.org_id, lab.MODEL_A,
                                      "provider_sharing"),
        }
        record_property("answers", answers)
        assert answers == {"a_granted": "allowed", "a_ungranted_grantor": "Forbidden",
                           "b_other_providers_grant": "Forbidden", "a_other_model": "Forbidden",
                           "viewer_granted": "Forbidden"}, answers


def test_l08_a_grant_is_purpose_bound_and_its_revocation_denies_the_next_call(workdir):
    with lab.lab_trip(workdir) as trip:
        access, alpha = lab.access(trip), trip.world.alpha
        assert content(access, lab.DEV_A, A, alpha.org_id, lab.MODEL_A, "provider_sharing") == "allowed"
        for purpose in ("training", "external_judging", "capture"):
            assert content(access, lab.DEV_A, A, alpha.org_id, lab.MODEL_A, purpose) == "Forbidden", purpose
        lab.revoke(trip, alpha, A)
        assert content(access, lab.DEV_A, A, alpha.org_id, lab.MODEL_A, "provider_sharing") == "Forbidden"


#: A consumer's rows a provider session must never read through the shared Supabase origin.
CONSUMER_TABLES = ("/api_keys?select=id,org_id", "/usage_events?select=id,org_id",
                   "/org_members?select=org_id,user_id", "/credit_ledger?select=id,org_id")


def test_l08_a_provider_session_reads_no_consumer_rows_directly(workdir, record_property):
    with lab.lab_trip(workdir) as trip:
        url, alpha = lab.rest_url(), trip.world.alpha
        for path in ("/api_keys?select=id,org_id", "/org_members?select=org_id,user_id"):
            own = lab.rest(url, alpha.user_id, path)
            assert own.status_code == 200 and any(r["org_id"] == alpha.org_id for r in own.json()), \
                f"premise: the owner reads its own {path} row: {own.status_code} {own.text[:200]}"
        seen = {}
        for path in CONSUMER_TABLES:
            answer = lab.rest(url, lab.DEV_A, path)
            rows = answer.json() if answer.status_code == 200 else []
            seen[path] = (answer.status_code, sum(r.get("org_id") == alpha.org_id for r in rows))
        record_property("provider_session", seen)
        assert all(status in (200, 401, 403, 404) and leaked == 0
                   for status, leaked in seen.values()), seen
