#!/usr/bin/env python3
"""LAB-ACCESS, record level: provider roles and source-purpose grants (F2P item 6).

Default deny, in three separate senses: a role grants only its own listed
capabilities, provider ownership grants no customer payload at all, and a grant is
checked against its *current* state rather than a snapshot.

    uv run --frozen pytest -q tests/contracts/v2/test_access_v2.py
"""
from __future__ import annotations

import datetime

import pytest
from pydantic import ValidationError

from infrx.contracts import errors
from infrx.contracts.v2 import fixtures as v2fix, ports as v2ports, records as v2

IDS = v2fix.IDS
MEMBERSHIP = v2fix.load("provider_membership.json")
GRANT = v2fix.load("access_grant.json")
NOW = datetime.datetime(2026, 9, 22, 12, 0, tzinfo=datetime.timezone.utc)
BEFORE = datetime.datetime(2026, 8, 1, tzinfo=datetime.timezone.utc)
AFTER_EXPIRY = datetime.datetime(2027, 1, 1, tzinfo=datetime.timezone.utc)

CONTENT = v2.DataCategory.request_content
SHARING = v2.DataPurpose.provider_sharing


def membership(**update) -> v2.ProviderMembership:
    return v2.ProviderMembership.model_validate(dict(MEMBERSHIP, **update))


def grant(**update) -> v2.AccessGrant:
    return v2.AccessGrant.model_validate(dict(GRANT, **update))


# --- roles -------------------------------------------------------------------
def test_the_role_vocabulary_and_its_capability_sets_are_closed():
    assert {role.value for role in v2.ProviderRole} == {"viewer", "developer", "administrator"}
    assert set(v2.ROLE_CAPABILITIES) == set(v2.ProviderRole)
    assert v2.ROLE_CAPABILITIES[v2.ProviderRole.viewer] == \
        frozenset({v2.ProviderCapability.read_aggregate_health})
    # every capability named in a role set is a real capability
    for role, capabilities in v2.ROLE_CAPABILITIES.items():
        assert capabilities <= set(v2.ProviderCapability), role
    with pytest.raises(ValidationError):
        membership(role="editor")


def test_no_role_can_read_customer_content():
    """F2P item 6: "provider ownership yields no customer payload". It is in the
    vocabulary and in no role's set, so only a grant can reach it."""
    for role, capabilities in v2.ROLE_CAPABILITIES.items():
        assert v2.ProviderCapability.read_customer_content not in capabilities, role
        assert membership(role=role.value).permits(
            v2.ProviderCapability.read_customer_content, NOW, IDS.provider_org) is False


@pytest.mark.parametrize("role,capability,allowed", [
    ("viewer", "read_aggregate_health", True),
    ("viewer", "manage_dev_deployment", False),
    ("viewer", "run_evaluation", False),
    ("viewer", "propose_publication", False),
    ("viewer", "manage_members", False),
    ("developer", "manage_dev_deployment", True),
    ("developer", "run_evaluation", True),
    ("developer", "propose_publication", False),
    ("developer", "manage_members", False),
    ("administrator", "propose_publication", True),
    ("administrator", "manage_members", True),
])
def test_each_role_permits_exactly_its_own_capabilities(role, capability, allowed):
    assert membership(role=role).permits(v2.ProviderCapability(capability), NOW,
                                         IDS.provider_org) is allowed


def test_a_membership_of_another_provider_or_a_revoked_one_permits_nothing():
    current = membership()
    assert current.permits(v2.ProviderCapability.read_aggregate_health, NOW,
                           IDS.rival_provider_org) is False
    revoked = membership(revoked_at=NOW.isoformat().replace("+00:00", "Z"))
    assert revoked.is_current(NOW) is False
    assert revoked.permits(v2.ProviderCapability.read_aggregate_health, NOW,
                           IDS.provider_org) is False
    # ... and it was current the instant before revocation
    assert revoked.is_current(NOW - datetime.timedelta(seconds=1)) is True
    # a membership not yet in effect is not current either
    assert current.is_current(BEFORE) is False


# --- grants ------------------------------------------------------------------
def test_the_purpose_and_category_vocabularies_are_the_four_and_five_named():
    assert {p.value for p in v2.DataPurpose} == {"capture", "provider_sharing",
                                                "external_judging", "training"}
    assert {c.value for c in v2.DataCategory} == {"request_content", "response_content",
                                                  "media", "usage_metadata", "feedback"}
    with pytest.raises(ValidationError):
        grant(purposes=["analytics"])
    with pytest.raises(ValidationError):
        grant(categories=["prompt"])


def test_a_grant_permits_only_what_it_names():
    current = grant()
    assert current.permits(now=NOW, provider_org_id=IDS.provider_org, model_id=IDS.model,
                           category=CONTENT, purpose=SHARING) is True
    # every dimension, changed one at a time
    assert current.permits(now=NOW, provider_org_id=IDS.rival_provider_org,
                           model_id=IDS.model, category=CONTENT, purpose=SHARING) is False
    assert current.permits(now=NOW, provider_org_id=IDS.provider_org, model_id="other",
                           category=CONTENT, purpose=SHARING) is False
    assert current.permits(now=NOW, provider_org_id=IDS.provider_org, model_id=IDS.model,
                           category=v2.DataCategory.media, purpose=SHARING) is False
    for purpose in (v2.DataPurpose.capture, v2.DataPurpose.external_judging,
                    v2.DataPurpose.training):
        assert current.permits(now=NOW, provider_org_id=IDS.provider_org, model_id=IDS.model,
                              category=CONTENT, purpose=purpose) is False, purpose


def test_an_empty_scope_fails_closed():
    """An empty tuple is "nothing in scope", never "everything": the same
    fail-closed reading v1's `OrgEntitlements.model_ids == ()` already has."""
    for empty in ({"model_ids": []}, {"categories": []}, {"purposes": []}):
        assert grant(**empty).permits(now=NOW, provider_org_id=IDS.provider_org,
                                      model_id=IDS.model, category=CONTENT,
                                      purpose=SHARING) is False, empty


def test_a_grant_is_checked_current_not_snapshot():
    """Revocation and expiry both block new access immediately. A grant that was
    valid when the data was captured is audit evidence, not standing permission."""
    current = grant()
    assert current.is_current(NOW) is True
    assert current.is_current(BEFORE) is False, "not yet effective"
    assert current.is_current(AFTER_EXPIRY) is False, "expired"
    revoked = grant(revoked_at=NOW.isoformat().replace("+00:00", "Z"), version=2)
    assert revoked.is_current(NOW) is False
    assert revoked.is_current(NOW - datetime.timedelta(seconds=1)) is True
    assert revoked.permits(now=NOW, provider_org_id=IDS.provider_org, model_id=IDS.model,
                           category=CONTENT, purpose=SHARING) is False
    # an open-ended grant has no expiry but is still revocable
    open_ended = v2.AccessGrant.model_validate(
        {k: v for k, v in GRANT.items() if k != "expires_at"})
    assert open_ended.is_current(AFTER_EXPIRY) is True


def test_retention_is_bounded_and_zero_is_not_a_retention_policy():
    """1..90 days, the same bound v1's `ConsentSnapshot` already enforces: "keep
    nothing" is expressed by having no grant, not by a zero-day one."""
    assert grant(retention_days=1).retention_days == 1
    assert grant(retention_days=90).retention_days == 90
    for bad in (0, -1, 91, 365):
        with pytest.raises(ValidationError):
            grant(retention_days=bad)


def test_a_grant_carries_its_grantor_recipient_and_version():
    current = grant()
    assert current.grantor_org_id == IDS.consumer_org
    assert current.recipient_provider_org_id == IDS.provider_org
    assert current.version == 1
    with pytest.raises(ValidationError):
        grant(version=0)


# --- both halves together ----------------------------------------------------
def test_content_access_needs_a_current_membership_and_a_current_grant():
    both = dict(now=NOW, provider_org_id=IDS.provider_org, model_id=IDS.model,
                category=CONTENT, purpose=SHARING)
    assert v2.may_read_customer_content(membership=membership(), grant=grant(), **both) is True
    assert v2.may_read_customer_content(membership=None, grant=grant(), **both) is False
    assert v2.may_read_customer_content(membership=membership(), grant=None, **both) is False
    assert v2.may_read_customer_content(membership=None, grant=None, **both) is False
    # a viewer has a membership and still cannot read content
    assert v2.may_read_customer_content(membership=membership(role="viewer"), grant=grant(),
                                        **both) is False
    # a revoked membership with a live grant, and the reverse, are both denied
    revoked_member = membership(revoked_at=NOW.isoformat().replace("+00:00", "Z"))
    assert v2.may_read_customer_content(membership=revoked_member, grant=grant(),
                                        **both) is False
    assert v2.may_read_customer_content(
        membership=membership(),
        grant=grant(revoked_at=NOW.isoformat().replace("+00:00", "Z"), version=2),
        **both) is False


def test_the_port_raises_a_typed_refusal_rather_than_returning_false():
    both = dict(now=NOW, provider_org_id=IDS.provider_org, model_id=IDS.model,
                category=CONTENT, purpose=SHARING)
    v2ports.authorize_content_read(membership=membership(), grant=grant(), **both)
    with pytest.raises(errors.Forbidden):
        v2ports.authorize_content_read(membership=membership(), grant=None, **both)
    with pytest.raises(errors.Forbidden):
        v2ports.authorize_content_read(membership=membership(role="viewer"), grant=grant(),
                                       **both)


def test_the_provider_directory_has_no_as_of_parameter():
    """A "grant as it stood at time T" lookup would let a caller authorize with a
    snapshot, which is exactly what item 6 forbids, so the Protocol does not offer
    one."""
    import inspect
    parameters = list(inspect.signature(v2ports.ProviderDirectory.current_grant).parameters)
    assert parameters == ["self", "grantor_org_id", "provider_org_id"], parameters
