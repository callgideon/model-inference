"""`AuthContextV2` from a bearer API key, on top of F1's bounded caches.

Two rules this module exists to enforce:

* **No accepted request without tenant identity.** `keys.Auth.authenticate`
  answers `(None, None)` - *allowed, with no row* - for a request bearing the
  shared `GATEWAY_API_KEY` and for a process configured with neither the legacy
  key nor Supabase (the `O-FAILOPEN` hazard: an install run that loses a
  parameter read publishes an open gateway). The new ingress cannot build an
  `AuthContext` out of that, and it does not invent one: no row is `401`, in
  every mode. The permissive F1 path was reachable only through the legacy
  `gateway:app` entry point, which the cutover retired.
* **Belt and braces with `config.validate_runtime` (r1 R44/R51).** Building a
  resolver in `pilot` mode against a shared key, or without an identity source,
  raises `config.RuntimeMisconfigured` - the same typed startup error, naming
  setting names and never values. `validate_runtime` checks this at
  `create_app`; this checks it again where the decision is actually used, so a
  settings object mutated after startup, or a composition root that forgets the
  hook, still cannot serve.

Everything else is F1's: the three bounded, insertion-ordered caches, their TTLs
and the constant-time legacy-key comparison all live in `keys.Auth` and are used
from here rather than reimplemented, so the cache bounds cannot drift apart.

G1R: the context is contracts-v2's `AuthContextV2`, built by `auth_context` from the
key row alone - its audience, the individual behind a consumer key, the provider and
private endpoint a provider dev key is scoped to (D1R 0009's `api_keys` columns). No
header or body field reaches it, and it carries no wallet: the wallet is resolved from
this identity by admission (R66). `auth_context` is the one derivation; the headless
operations surface (G6B) builds its sessions with it too.
"""
from __future__ import annotations

from ..config import PILOT_AUTH_SETTINGS, PILOT_FORBIDDEN_SETTINGS, RuntimeMisconfigured
from ..contracts import errors
from ..contracts.records import Role
from ..contracts.v2.records import AuthContextV2, CredentialAudience

# An API key is a machine credential for one organization: it is never a person and
# never a platform operator, so `by_operator` (r1 R50) can never be true for one.
API_KEY_ROLE = Role.service
# What the ingress reads of an `api_keys` row. `created_by` is the individual behind a
# consumer key that predates 0009's `user_id` column (D1R: legacy keys are not
# rewritten; D2's admission reads `coalesce(user_id, created_by)` the same way).
KEY_COLUMNS = "id,org_id,revoked_at,audience,user_id,created_by,provider_org_id,endpoint_id"


def auth_context(*, audience, org_id, key_id, user_id=None, provider_org_id=None,
                 endpoint_id=None, role=API_KEY_ROLE, entitlement_version=0) -> AuthContextV2:
    """The one derivation of `AuthContextV2` from a trusted key row. Typed refusals only.

    The audience is the row's, and each audience keeps only its own identity: the
    individual for a consumer key (the wallet owner, R66), the provider and endpoint
    for a provider dev key (R70), nothing for an operator key. An unknown audience or a
    consumer key naming no individual is not a credential (`invalid_api_key`); a row
    the record refuses otherwise is our data, so it is `internal_error` and its values
    never reach the message.
    """
    try:
        audience = CredentialAudience(audience)
    except ValueError:
        raise errors.InvalidApiKey("the credential has no recognised audience") from None
    if not org_id or not key_id:
        raise errors.InvalidApiKey("the api_keys row names no organization")
    consumer = audience is CredentialAudience.consumer
    if consumer and not user_id:
        raise errors.InvalidApiKey("a consumer credential names no individual")
    if audience is CredentialAudience.provider_dev and provider_org_id != org_id:
        # Its organization scopes idempotency and payloads while its provider's dev wallet
        # pays: the two must be one workspace (0009 has no CHECK for it yet - D1R).
        raise errors.InternalError("a provider dev key belongs to its provider's organization")
    try:
        return AuthContextV2(audience=audience, org_id=org_id, key_id=key_id, principal=key_id,
                             role=role, entitlement_version=entitlement_version,
                             user_id=user_id if consumer else None,
                             provider_org_id=provider_org_id, endpoint_id=endpoint_id)
    except ValueError:
        raise errors.InternalError("the api_keys row is not a valid identity") from None


class AuthResolver:
    """Bearer API key -> `AuthContextV2`. One per app, like `keys.Auth`."""

    def __init__(self, rt, *, entitlement_version=None) -> None:
        self.rt = rt
        # r1 R24/R10: identity only. Authorization - revocation, suspension, current
        # entitlement - is rechecked by `JobStore.admit` inside its transaction, so a
        # stale version here can never widen what a request may do. D1 supplies the
        # real source; until then every context reports version 0, "no per-org
        # entitlement decision recorded".
        self.entitlement_version = entitlement_version or (lambda org_id: 0)
        self._refuse_shared_identity()

    @property
    def pilot(self) -> bool:
        return self.rt.mode == "pilot"

    def _refuse_shared_identity(self) -> None:
        """r1 R51: a shared key is not tenant authentication, and no identity source
        at all is worse. Neither may exist in `pilot`."""
        if not self.pilot:
            return
        s = self.rt.settings
        forbidden = [name for name, value in (("GATEWAY_API_KEY", s.legacy_key),)
                     if name in PILOT_FORBIDDEN_SETTINGS and str(value or "").strip()]
        present = {"SUPABASE_URL": s.supabase_url, "SUPABASE_SERVICE_ROLE_KEY": s.supabase_key}
        missing = [name for name in PILOT_AUTH_SETTINGS if not str(present[name] or "").strip()]
        if missing or forbidden:
            raise RuntimeMisconfigured(self.rt.mode, missing, forbidden=forbidden)

    async def context(self, request) -> AuthContextV2:
        """The authenticated credential, or a typed error. Never a partial identity."""
        row, status = await self.rt.auth.authenticate(request, select=KEY_COLUMNS)
        if status == 401:
            raise errors.InvalidApiKey("the bearer token is missing, unknown or revoked")
        if status is not None:
            # F1 answers 503 for an unknown key while the identity source is down:
            # retryable, and never a 401 that would look like a revoked key.
            raise errors.DependencyUnavailable("the api_keys identity source is unreachable")
        if row is None:
            # The shared legacy key, or nothing configured at all: allowed by F1,
            # anonymous, and therefore unmeterable. Not an identity.
            raise errors.InvalidApiKey("the request carries no per-organization identity")
        org_id = row.get("org_id")
        return auth_context(
            audience=row.get("audience"), org_id=org_id, key_id=row.get("id"),
            user_id=row.get("user_id") or row.get("created_by"),
            provider_org_id=row.get("provider_org_id"), endpoint_id=row.get("endpoint_id"),
            entitlement_version=self.entitlement_version(org_id) if org_id else 0)
