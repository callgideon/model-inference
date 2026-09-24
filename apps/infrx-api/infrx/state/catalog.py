"""D5 item 8 (G1R request 2b): the PostgreSQL `CatalogDirectory` over 0007's registry.

One statement per lookup, on a `Connect` (`jobstore.connector`: a fresh connection per call,
so a probe asking from its own thread and event loop needs no shared pool). Errors are
raised typed (the ingress maps them to 503), never swallowed into `None`: `None` means only
"no such row for this caller".

    resolve              a PUBLIC alias (`<model>` or `<model>@<label>`) is the highest
                         effective catalog listing, its deployment public and active; a
                         PRIVATE deployment is reachable only by a provider_dev credential,
                         on its own endpoint, named `<provider slug>/<endpoint name>-<env>`
                         (`@<label>` picks the serving revision), newest first. Anyone else,
                         and any other endpoint, gets None (R70: never a 403).
    serving_revision     the immutable serving revision with its model version's artifact.
    active_rate_card     the CREDIT card the deployment's effective catalog listing names
                         (0008's pin; a private deployment no listing names: its newest
                         effective card), at the DATABASE clock; unpriced is None (R69).
    data_access_policy   the policy version in force at the database clock.
"""
from __future__ import annotations

from ..contracts.v2.records import (CredentialAudience, DataAccessPolicyRef,
                                    DeploymentRevision, RateCardSnapshot, ServingRevision)
from .jobstore import Connect
from .operations import (_CARD_FIELDS, _DEPLOYMENT_FIELDS, _SERVING, _SERVING_FIELDS, _Db,
                         _record)

_D = ("d.deployment_revision_id, d.endpoint_id, d.provider_org_id, d.serving_version_id, "
      "d.environment, d.visibility, d.state, d.max_input_tokens, d.max_output_tokens, "
      "d.created_at")
_PUBLIC = f"""
  select {_D} from infrx.catalog_listings l
  join infrx.serving_versions s on s.serving_version_id = l.serving_version_id
  join infrx.deployment_revisions d on d.deployment_revision_id = l.deployment_revision_id
  where l.public_model_id = %(alias)s and l.effective_at <= infrx.now()
    and (%(label)s::text is null or s.revision_label = %(label)s)
    and d.visibility = 'public' and d.state = 'active'
  order by l.version desc limit 1"""
_PRIVATE = f"""
  select {_D} from infrx.deployment_revisions d
  join infrx.endpoints e on e.endpoint_id = d.endpoint_id
  join infrx.provider_orgs p on p.provider_org_id = d.provider_org_id
  join infrx.serving_versions s on s.serving_version_id = d.serving_version_id
  where d.endpoint_id = %(endpoint)s and d.visibility = 'private' and d.state <> 'retired'
    and p.slug || '/' || e.name || '-' || e.environment = %(alias)s
    and (%(label)s::text is null or s.revision_label = %(label)s)
  order by d.created_at desc, d.deployment_revision_id desc limit 1"""
# D10 (F2C.c, S3 F11): ONE definition of "the card", the one 0008 pins at admission - the card
# the deployment's effective catalog listing names. A card minted for the deployment and not
# (yet) named by a listing never reprices it; only a new listing version does (G8 publishes
# both atomically). A deployment no listing names (a private dev one) keeps its newest
# effective card, as before.
_ACTIVE_CARD = """
  select c.rate_card_version, c.model_id, c.deployment_revision_id, c.serving_version_id,
         c.input_rate_per_million::text, c.output_rate_per_million::text, c.effective_at,
         c.approved_by
  from infrx.rate_card_versions c
  where c.effective_at <= infrx.now() and c.rate_card_version = coalesce(
    (select l.rate_card_version from infrx.catalog_listings l
      where l.deployment_revision_id = %(d)s and l.effective_at <= infrx.now()
      order by l.version desc limit 1),
    (select x.rate_card_version from infrx.rate_card_versions x
      where x.deployment_revision_id = %(d)s and x.effective_at <= infrx.now()
        and not exists (select 1 from infrx.catalog_listings l
                         where l.deployment_revision_id = %(d)s)
      order by x.effective_at desc, x.created_at desc, x.rate_card_version desc limit 1))"""
_POLICY = """
  select policy_version, effective_at from infrx.data_access_policies
  where effective_at <= infrx.now() and exists (select 1 from infrx.deployment_revisions d
                                                 where d.deployment_revision_id = %s)
  order by effective_at desc, policy_version desc limit 1"""


class PgCatalogDirectory:
    """`v2.ports.CatalogDirectory` over PostgreSQL (reads only)."""

    def __init__(self, connect: Connect) -> None:
        self._db = _Db(connect)

    async def resolve(self, requested_model: str, *, audience: CredentialAudience,
                      endpoint_id: str | None) -> DeploymentRevision | None:
        alias, _, label = requested_model.partition("@")
        row = await self._db.one(_PUBLIC, {"alias": alias, "label": label or None})
        if row is None and audience is CredentialAudience.provider_dev and endpoint_id:
            row = await self._db.one(_PRIVATE, {"alias": alias, "label": label or None,
                                                "endpoint": endpoint_id})
        return None if row is None else _record(DeploymentRevision, _DEPLOYMENT_FIELDS, row)

    async def serving_revision(self, serving_version_id: str) -> ServingRevision | None:
        row = await self._db.one(_SERVING, (serving_version_id,))
        return None if row is None else _record(ServingRevision, _SERVING_FIELDS, row)

    async def active_rate_card(self, deployment_revision_id: str) -> RateCardSnapshot | None:
        row = await self._db.one(_ACTIVE_CARD, {"d": deployment_revision_id})
        return None if row is None else _record(RateCardSnapshot, _CARD_FIELDS, row)

    async def data_access_policy(self, deployment_revision_id: str) -> DataAccessPolicyRef | None:
        """0007's policy table records a version and its instant only, so the reference
        carries the conservative defaults - consent version 1, trace mode `off` - that no
        request can widen (an admitted job pins its own consent and trace mode from its
        request, `load_work_credit`). ponytail: per-deployment policies when 0007 grows them."""
        row = await self._db.one(_POLICY, (deployment_revision_id,))
        if row is None:
            return None
        return DataAccessPolicyRef(policy_version=row[0], consent_version=1, trace_mode="off",
                                   effective_at=row[1])


__all__ = ["PgCatalogDirectory"]
