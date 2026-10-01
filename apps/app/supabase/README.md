# Shared database and authentication

Consumer App and provider Lab share the ordered migration history in [migrations](migrations/README.md). **Last reported hosted level: 0001–0059**; see [current state](../../../STATUS.md) and the [operational log](../../../research/plan/consumer-v1/09-path-to-internal-testing.md). Migration comments saying LOCAL-ONLY are immutable merge-time history, not current deployment state.

## Applying changes

Use one migration owner and the next free number at integration. Do not edit applied SQL, replay an old seed to change live prices, or issue an unrestricted hosted `db push` from a fresh checkout.

Hosted changes follow [the rollout procedure](../../../infra/rollout/README.md) and `infra/rollout/hosted-migrate.sh`: explicit pending range, history/digest verification, backup, known-good compatibility reproof, a coordinated window, apply and postchecks. The next migration after this snapshot is 0060 unless newer work has already reserved it. Local test stacks use [the supported test environment](../../../tests/integration/ENVIRONMENT.md).

## Authority and exposure

- PostgreSQL owns consumer identity/entitlement, wallets/holds/ledger, jobs/fences/journal and lifecycle state. CREDIT and legacy USD remain separate.
- The one-time 10,000 CREDIT entitlement is keyed to the verified individual. Callback/login retries must replay the same grant.
- Public PostgREST exposure stays limited to the intended public wrappers; do not expose the internal `infrx` schema. Verify function grants and RLS with real roles, including Supabase defaults.
- Consumer ownership, provider memberships and platform operator capability are different permissions. Financial mutations use audited RPCs/operations, not arbitrary table updates.
- Provider worker roles have dedicated grants/logins. Some are still absent in production; do not substitute the broad runtime credential to make a disabled workflow run.

The migration files and executable permission tests are the authority for exact function/table names. Earlier USD-only reporting examples and 0001–0002 role tables have been removed from this README because they do not describe the current schema.

## Hosted authentication

Public signup remains closed at the last recorded cutoff. The required launch flow is verified email/password signup, confirmation, sign-in and password recovery; opening signup is separate from deploying the pages.

Validate the production site/callback allowlist, custom SMTP and templates, confirmation, password minimum, auth rate limits and challenge policy. Keep preview/development auth separate from production. The App's configuration matrix lives in [lib/deploy/env.ts](../lib/deploy/env.ts); **its CAPTCHA integration/checklist is incomplete** relative to P-05 and must be finished before public signup.

Use the [App deployment runbook](../../../infra/app/README.md), [operations checklist](../../../infra/app/operations.md) and [launch review LR-02](../../../research/plan/26-launch-readiness-review-2026-10-01.md#lr-02--p1-before-public-signup-captcha-is-missing-from-the-implemented-auth-flow). Do not reintroduce old Google-login assumptions or broad preview wildcard examples from historical handoffs.

Secret values come from the authorized project/secret store. No database password, service-role key, email token or user password belongs in the repository, command examples or task logs.
