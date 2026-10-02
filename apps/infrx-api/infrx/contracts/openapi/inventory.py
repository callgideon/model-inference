"""AP-00 00a: the route/action coverage artifact (contracts.md §3, R271).

    uv run --frozen python -m infrx.contracts.openapi.export   # also rewrites the inventory

One artifact joins three lists:
- `routes`: every mounted (method, path) of every composition `export.compositions` builds -
  its compositions, auth audience, envelope, request/response schema state, and the web
  client that consumes it today;
- `web_actions`: every product action of `apps/app` and `apps/lab` (the wave-7 maps §B), its
  transport today and the target operation(s) it moves to. `flag` marks an action that
  bypasses the API (a product table/RPC or the service-role client: R271's to-remove list);
  the identity provider's own transport is the allowlisted exception, not flagged;
- `targets`: contracts.md §3's operations, each `existing` when a composition mounts that
  path shape today, else `target`.
"""
from __future__ import annotations

import json
import re
from typing import Any

from fastapi import FastAPI

from . import export

EVIDENCE = export.API_DIR.parents[1] / "research/plan/api-lifecycle/evidence"
JSON_PATH = EVIDENCE / "route-inventory.json"
MD_PATH = EVIDENCE / "route-inventory.md"

API, IDP, DB, ADMIN = "api", "identity-provider", "supabase-db", "service-role"
#: Transports a web action may not keep (R271): direct product database access.
BYPASS = frozenset({DB, ADMIN})

LAB, APP = "apps/lab/lib/services", "apps/app"
#: Today's consumer of each mounted route: (method, path) first, then a path prefix.
CONSUMERS: tuple[tuple[str, str], ...] = (
    ("GET /v1/models", f"{APP}/app/(console)/models/catalog.ts loadCatalog"),
    ("GET /lab/v1/control/models", f"{LAB}/control/http.ts models"),
    ("GET /lab/v1/control/deployments", f"{LAB}/control/http.ts deployments"),
    ("GET /lab/v1/control/proposals", f"{LAB}/control/http.ts proposals"),
    ("GET /lab/v1/control/aggregates", f"{LAB}/control/http.ts aggregates"),
    ("POST /lab/v1/control/register", f"{LAB}/control/http.ts register"),
    ("POST /lab/v1/control/deployments/{deployment_revision_id}/smoke",
     f"{LAB}/control/http.ts smoke"),
    ("POST /lab/v1/control/proposals", f"{LAB}/control/http.ts propose"),
    ("/lab/v1/evaluations/", f"{LAB}/evaluation/http.ts"),
    ("/lab/v1/pipelines/", f"{LAB}/pipelines/http.ts"),
    ("/lab/v1/releases", f"{LAB}/rollouts/http.ts"),
    ("/lab/v1/optimizations", f"{LAB}/rollouts/http.ts"),
    ("/lab/v1/providers/", f"{LAB}/datasets/port.ts"),
    ("/lab/v1/traces", f"{LAB}/traces/port.ts"),
)

#: (app, surface, action, source, transport today, target operations) - maps §B.
WEB_ACTIONS: tuple[tuple[str, str, str, str, str, tuple[str, ...]], ...] = (
    ("app", "login", "signInWithPassword", "app/(auth)/login/login-form.tsx", IDP,
     ("POST /auth/v1/sign-in",)),
    ("app", "signup", "signUp", "app/(auth)/signup/signup-form.tsx", IDP,
     ("POST /auth/v1/sign-up",)),
    ("app", "verify-email", "resend", "app/(auth)/verify-email/resend-form.tsx", IDP,
     ("POST /auth/v1/sign-up",)),
    ("app", "forgot-password", "resetPasswordForEmail", "app/(auth)/forgot-password/page.tsx",
     IDP, ("POST /auth/v1/recovery",)),
    ("app", "update-password", "updateUser", "app/(auth)/update-password/page.tsx", IDP,
     ("POST /auth/v1/password",)),
    ("app", "callback", "completeCallback", "app/auth/callback/route.ts", IDP,
     ("GET /auth/v1/callback",)),
    ("app", "shell", "signOut", "app/actions.ts", IDP, ("POST /auth/v1/sign-out",)),
    ("app", "welcome", "claimSignupGrant", "app/(auth)/grant.ts (rpc claim_signup_grant)",
     ADMIN, ("POST /console/v1/signup-grant/claim",)),
    ("app", "welcome", "claimOnboarding", "app/(auth)/welcome/actions.ts", ADMIN,
     ("POST /console/v1/signup-grant/claim",)),
    ("app", "welcome", "walletSummary", "app/(auth)/welcome/page.tsx (rpc console_wallet_summary)",
     DB, ("GET /console/v1/credits",)),
    ("app", "shell", "getSession", "lib/session.ts (profiles)", DB, ("GET /console/v1/me",)),
    ("app", "shell", "resolveConsumerContext", "lib/services/console.ts (org_members, wallet)",
     DB, ("GET /console/v1/me", "GET /console/v1/capabilities")),
    ("app", "api-keys", "keysList", "lib/services/console.ts (named query keys_list)", DB,
     ("GET /console/v1/keys",)),
    ("app", "api-keys", "createConsumerKey", "app/actions.ts -> lib/services/actions.ts "
     "(generateKey + api_keys insert)", DB, ("POST /console/v1/keys",)),
    ("app", "api-keys", "revokeConsumerKey", "app/actions.ts -> lib/services/actions.ts "
     "(api_keys update)", DB, ("DELETE /console/v1/keys/{id}",)),
    ("app", "models", "loadCatalog", "app/(console)/models/catalog.ts", API,
     ("GET /v1/models",)),
    ("app", "models", "keysForExamples", "app/(console)/models/page.tsx (api_keys)", DB,
     ("GET /console/v1/keys",)),
    ("app", "docs", "keysForExamples", "app/(console)/docs/page.tsx (api_keys)", DB,
     ("GET /console/v1/keys",)),
    ("app", "billing", "creditReads", "app/(console)/billing/credit-reads.ts "
     "(console_credit_wallets, consumer_credit_ledger, console_credit_ledger)", DB,
     ("GET /console/v1/credits", "GET /console/v1/credit-ledger")),
    ("app", "billing", "legacyStatement", "lib/services/console.ts "
     "(rpc console_legacy_usd_statement)", DB, ("GET /console/v1/legacy-statement",)),
    ("app", "usage", "requests", "lib/services/console.ts (rpc consumer_jobs)", DB,
     ("GET /console/v1/requests",)),
    ("app", "usage", "requestDetail", "app/(console)/usage/[requestId]/request-reads.ts "
     "(rpc consumer_jobs, consumer_job_result)", DB,
     ("GET /console/v1/requests/{id}", "GET /console/v1/requests/{id}/result")),
    ("app", "usage", "submitFeedback", "lib/services/feedback.ts (rpc submit_feedback; unwired)",
     DB, ("POST /console/v1/requests/{id}/feedback",)),
    ("app", "teams", "members", "app/(console)/teams/page.tsx (org_members, profiles)", DB,
     ("GET /console/v1/account/members",)),
    ("app", "admin", "operatorReads", "app/(console)/admin/operator-reads.ts "
     "(console_admin_orgs, operator_wallet_drift, operator_unknown_usage, operator_audit)", DB,
     ("GET /operator/v1/accounts", "GET /operator/v1/wallet-drift",
      "GET /operator/v1/unknown-usage", "GET /operator/v1/audit")),
    ("app", "admin", "operatorAction", "app/(console)/admin/operator-port.ts (rpc "
     "operator_adjust_credit / operator_set_suspension / operator_revoke_key)", DB,
     ("POST /operator/v1/credit-adjustments", "POST /operator/v1/suspensions",
      "POST /operator/v1/key-revocations")),
    ("lab", "sign-in", "signIn", "lib/auth/sign-in.ts", IDP, ("POST /auth/v1/sign-in",)),
    ("lab", "shell", "signOut", "lib/auth/sign-in.ts", IDP, ("POST /auth/v1/sign-out",)),
    ("lab", "callback", "authCallback", "lib/auth/routes.ts", IDP, ("GET /auth/v1/callback",)),
    ("lab", "shell", "refreshSession", "lib/auth/routes.ts", IDP, ("POST /auth/v1/refresh",)),
    ("lab", "shell", "memberships", "lib/auth/memberships.ts (rpc lab_provider_memberships)",
     DB, ("GET /lab/v1/workspaces",)),
    ("lab", "models", "control", "lib/services/control/http.ts", API,
     ("GET /lab/v1/control/models", "GET /lab/v1/control/deployments",
      "GET /lab/v1/control/aggregates", "POST /lab/v1/control/proposals")),
    ("lab", "requests", "traces", "lib/services/traces/port.ts", API,
     ("GET /lab/v1/traces", "GET /lab/v1/traces/{id}")),
    ("lab", "requests", "reviewFeedback", "lib/services/review/index.ts "
     "(rpc lab_review_feedback)", DB, ("GET /lab/v1/traces/{id}/feedback",)),
    ("lab", "requests", "review", "lib/services/review/actions.ts (rpc lab_review_feedback)",
     DB, ("POST /lab/v1/traces/{id}/reviews",)),
    ("lab", "judge", "configure", "lib/services/judge/core.ts (rpc lab_judge_configure)", DB,
     ("POST /lab/v1/judge/configs",)),
    ("lab", "judge", "setBudget", "lib/services/judge/core.ts (rpc lab_judge_set_budget)", DB,
     ("PUT /lab/v1/judge/budgets/{payer_id}",)),
    ("lab", "judge", "requestRun", "lib/services/judge/core.ts (rpc lab_judge_request_run)",
     DB, ("POST /lab/v1/judge/runs",)),
    ("lab", "judge", "calibration", "lib/services/judge/core.ts (rpc lab_judge_calibration)",
     DB, ("GET /lab/v1/judge/calibration",)),
    ("lab", "judge", "runs", "lib/services/judge/runs.ts, components/traces/judge/port.ts "
     "(rpc lab_judge_runs)", DB, ("GET /lab/v1/judge/runs",)),
    ("lab", "datasets", "datasets", "lib/services/datasets/port.ts", API,
     ("GET /lab/v1/providers/{provider}/datasets/versions",)),
    ("lab", "evaluations", "evaluation", "lib/services/evaluation/http.ts", API,
     ("GET /lab/v1/evaluations/runs",)),
    ("lab", "annotations", "pipelines", "lib/services/pipelines/http.ts", API,
     ("GET /lab/v1/pipelines/labels",)),
    ("lab", "releases", "rollouts", "lib/services/rollouts/http.ts", API,
     ("GET /lab/v1/releases", "GET /lab/v1/optimizations")),
)

#: contracts.md §3, one (method, path, surface) per operation it names.
TARGETS: tuple[tuple[str, str, str], ...] = tuple(
    (op.partition(" ")[0], op.partition(" ")[2], surface) for surface, ops in (
        ("auth", ("POST /auth/v1/sign-in", "POST /auth/v1/sign-up", "POST /auth/v1/refresh",
                  "POST /auth/v1/sign-out", "POST /auth/v1/recovery", "POST /auth/v1/password",
                  "GET /auth/v1/callback", "GET /auth/v1/availability")),
        ("app shell", ("GET /console/v1/me", "GET /console/v1/capabilities")),
        ("welcome", ("POST /console/v1/signup-grant/claim",)),
        ("api keys", ("GET /console/v1/keys", "POST /console/v1/keys",
                      "DELETE /console/v1/keys/{id}")),
        ("models and docs", ("GET /v1/models",)),
        ("usage", ("GET /console/v1/requests", "GET /console/v1/requests/{id}",
                   "GET /console/v1/requests/{id}/result")),
        ("feedback", ("POST /console/v1/requests/{id}/feedback", "POST /v1/feedback")),
        ("credits", ("GET /console/v1/credits", "GET /console/v1/credit-ledger",
                     "GET /console/v1/legacy-statement")),
        ("teams", ("GET /console/v1/account/members",)),
        ("privacy", ("GET /console/v1/data-use", "PUT /console/v1/keys/{id}/capture",
                     "POST /console/v1/data-grants", "GET /console/v1/data-grants",
                     "DELETE /console/v1/data-grants/{id}")),
        ("operator", ("GET /operator/v1/accounts", "GET /operator/v1/wallet-drift",
                      "GET /operator/v1/unknown-usage", "GET /operator/v1/audit",
                      "POST /operator/v1/credit-adjustments", "POST /operator/v1/suspensions",
                      "POST /operator/v1/key-revocations", "POST /operator/v1/providers")),
        ("lab shell", ("GET /lab/v1/workspaces", "GET /lab/v1/capabilities",
                       "GET /lab/v1/workspaces/{id}/members",
                       "POST /lab/v1/workspaces/{id}/members",
                       "DELETE /lab/v1/workspaces/{id}/members/{user}")),
        ("lab overview", ("GET /lab/v1/control/aggregates", "GET /lab/v1/control/models",
                          "GET /lab/v1/control/deployments")),
        ("lab models", ("GET /lab/v1/control/model-projects",
                        "POST /lab/v1/control/model-projects",
                        "GET /lab/v1/control/model-projects/{id}/revisions",
                        "POST /lab/v1/control/model-projects/{id}/revisions")),
        ("lab deployments", ("POST /lab/v1/control/deployments",
                             "GET /lab/v1/control/deployments/{id}",
                             "GET /lab/v1/control/deployments/{id}/readiness")),
        ("lab publication", ("POST /lab/v1/control/proposals",)),
        ("lab requests", ("GET /lab/v1/traces", "GET /lab/v1/traces/{id}",
                          "GET /lab/v1/traces/{id}/feedback",
                          "POST /lab/v1/traces/{id}/reviews")),
        ("lab judge", ("POST /lab/v1/judge/configs", "PUT /lab/v1/judge/budgets/{payer_id}",
                       "POST /lab/v1/judge/runs", "GET /lab/v1/judge/runs",
                       "GET /lab/v1/judge/calibration")),
        ("lab datasets", ("GET /lab/v1/providers/{provider}/datasets/versions",)),
        ("lab evaluations", ("GET /lab/v1/evaluations/runs",)),
        ("lab annotations", ("GET /lab/v1/pipelines/labels",)),
        ("lab releases", ("GET /lab/v1/releases", "GET /lab/v1/optimizations")),
    ) for op in ops)


def shape_of(path: str) -> str:
    """A path with its parameter names erased: `/a/{id}` and `/a/{request_id}` are one."""
    return re.sub(r"\{[^}]*\}", "{}", path)


def consumer(method: str, path: str) -> list[str]:
    exact = [who for key, who in CONSUMERS if key == f"{method} {path}"]
    if exact:
        return exact
    return [who for key, who in CONSUMERS if " " not in key and path.startswith(key)][:1]


def build(apps: dict[str, FastAPI]) -> dict[str, Any]:
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for name, app in apps.items():
        for route in export.routes(app):
            for method in sorted(route.methods or ()):
                key = (route.path_format, method)
                if key not in rows:
                    found = export.family(route.path_format)
                    request, response = export.shape(route)
                    rows[key] = {"method": method, "path": route.path_format, "compositions": [],
                                 "audience": found[2] if found else "unclassified",
                                 "envelope": found[1] if found else "unclassified",
                                 "request_schema": request, "response_schema": response,
                                 "consumers": consumer(method, route.path_format)}
                rows[key]["compositions"].append(name)
    mounted = {(row["method"], shape_of(row["path"])) for row in rows.values()}
    return {
        "routes": [rows[key] for key in sorted(rows)],
        "web_actions": [{"app": app, "surface": surface, "action": action, "source": source,
                         "transport": transport, "targets": list(targets),
                         "flag": transport in BYPASS}
                        for app, surface, action, source, transport, targets in WEB_ACTIONS],
        "targets": [{"method": method, "path": path, "surface": surface,
                     "state": "existing" if (method, shape_of(path)) in mounted else "target"}
                    for method, path, surface in TARGETS],
    }


def markdown(doc: dict[str, Any]) -> str:
    lines = ["# Route and web-action inventory (AP-00 00a)", "",
             "Generated by `uv run --frozen python -m infrx.contracts.openapi.export` "
             "(apps/infrx-api); do not edit. Diffed by "
             "`tests/contracts/test_openapi_export.py`.", "",
             "## Mounted routes", "",
             "| Method | Path | Compositions | Audience | Envelope | Request | Response | "
             "Consumer today |", "|---|---|---|---|---|---|---|---|"]
    lines += [f"| {r['method']} | `{r['path']}` | {', '.join(r['compositions'])} | "
              f"{r['audience']} | {r['envelope']} | {r['request_schema']} | "
              f"{r['response_schema']} | {'; '.join(r['consumers']) or '-'} |"
              for r in doc["routes"]]
    lines += ["", "## Web actions (flag = bypasses the API; R271 removes it)", "",
              "| App | Surface | Action | Source | Transport | Target | Flag |",
              "|---|---|---|---|---|---|---|"]
    lines += [f"| {a['app']} | {a['surface']} | {a['action']} | {a['source']} | "
              f"{a['transport']} | {'; '.join(a['targets'])} | {'FLAG' if a['flag'] else ''} |"
              for a in doc["web_actions"]]
    lines += ["", "## contracts.md §3 operations: existing vs target", "",
              "| Surface | Method | Path | State |", "|---|---|---|---|"]
    lines += [f"| {t['surface']} | {t['method']} | `{t['path']}` | {t['state']} |"
              for t in doc["targets"]]
    return "\n".join(lines) + "\n"


def write(apps: dict[str, FastAPI]) -> None:
    doc = build(apps)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    JSON_PATH.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    MD_PATH.write_text(markdown(doc))
