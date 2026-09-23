#!/usr/bin/env python3
"""R32/R40 for contracts v2: the mutation list and its runner.

A conformance case is only worth the invariant it can *kill*. Each entry below is a
single edit to `infrx/contracts/v2/*` (or to the v2 fakes) that breaks one named
invariant, together with the case in `tests/contracts/v2/test_conformance_v2.py`
that must notice. The runner applies one mutant at a time to a **copy** of the
package in a temporary directory, runs only the named cases there, and fails if the
mutant survives. Nothing is ever written inside the worktree.

    uv run --frozen pytest -q tests/contracts/v2/test_mutants_v2.py    # the whole list
    uv run --frozen python tests/contracts/v2/mutants_v2.py --list     # names only
    uv run --frozen python tests/contracts/v2/mutants_v2.py settle_ignores_certainty

What counts as a kill is the shared runner's rule (`tests/contracts/mutants.py`, F2R
item 9; this list delegates to it with its own `Runner` since IR-A10): pytest exited 1,
only the mutant's own cases failed, and each died by an assertion, a typed
`DomainError`, or an exception the mutant declares. A syntax error, an import-time
failure or a collection error is `broken_runner`, which fails the run just as a
survivor does.
"""
from __future__ import annotations

import pathlib
import sys

if __package__:
    from .. import mutants as v1runner
else:                                   # run as a script: `python tests/contracts/v2/mutants_v2.py`
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
    import mutants as v1runner
Mutant, Outcome, Result = v1runner.Mutant, v1runner.Outcome, v1runner.Result

TEST_FILE = "tests/contracts/v2/test_conformance_v2.py"
RUNNER = v1runner.Runner(name="v2", targets=(TEST_FILE,))


def _m(name, invariant, file, old, new, *cases, dies_by=()) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=file, old=old, new=new, cases=cases,
                  dies_by=tuple(dies_by))


R = "contracts/v2/records.py"
P = "contracts/v2/ports.py"
MU = "contracts/v2/money_units.py"
FX = "contracts/v2/fixtures.py"
INIT = "contracts/v2/__init__.py"
FAKE = "contracts/conformance/v2_fakes.py"

MUTANTS: tuple[Mutant, ...] = (
    # --- SPLIT-CONTRACT ------------------------------------------------------
    _m("catalog_lists_private_dev_publicly",
       "a private dev deployment is not in the public catalog at all",
       FAKE, "        if deployment.visibility is v2.Visibility.private:",
       "        if False:",
       "split_contract__a_consumer_credential_cannot_reach_a_private_dev_endpoint"),
    _m("pin_lets_a_copied_consumer_context_into_a_private_endpoint",
       "only a provider_dev audience reaches a private deployment, even when a "
       "model_copy'd consumer context carries the endpoint's ids (review B1, G1)",
       P, "        if auth.audience is not CredentialAudience.provider_dev:",
       "        if False:",
       "split_contract__a_consumer_credential_cannot_reach_a_private_dev_endpoint"),
    _m("pin_ignores_the_credentials_endpoint_scope",
       "a provider credential reaches only the endpoint it was issued for",
       P, "        if auth.endpoint_id != deployment.endpoint_id:",
       "        if False and auth.endpoint_id != deployment.endpoint_id:",
       "split_contract__a_consumer_credential_cannot_reach_a_private_dev_endpoint"),
    _m("pin_ignores_the_credentials_provider",
       "a member of another provider cannot reach this provider's dev endpoint",
       P, "        if auth.provider_org_id != deployment.provider_org_id:",
       "        if False and auth.provider_org_id != deployment.provider_org_id:",
       "split_contract__a_consumer_credential_cannot_reach_a_private_dev_endpoint"),
    _m("catalog_ignores_the_endpoint_scope",
       "the catalog itself does not answer for a foreign endpoint",
       FAKE, "            if endpoint_id != deployment.endpoint_id:\n"
             "                return None",
       "            if endpoint_id != deployment.endpoint_id:\n"
       "                pass",
       "split_contract__a_provider_credential_cannot_borrow_another_endpoint"),
    _m("a_v1_payload_is_silently_accepted",
       "schema_version is explicit; a v1 body is refused, never reinterpreted",
       R, "        if self.schema_version != SCHEMA_VERSION:",
       "        if False:",
       "split_contract__an_internal_v1_payload_is_refused_not_upgraded"),
    _m("a_v1_price_snapshot_converts_to_credit",
       "there is no conversion from a v1 USD snapshot to a CREDIT rate card",
       R, '    raise ValueError(f"v1 PriceSnapshot {snapshot.price_version} is denominated in "',
       '    return None or ValueError(f"v1 PriceSnapshot {snapshot.price_version} is '
       'denominated in "',
       "split_contract__an_internal_v1_payload_is_refused_not_upgraded"),
    _m("model_revision_loses_its_revision",
       "r1 R62: the consumer-facing identifier keeps its <model>@<revision> form",
       R, '        return f"{self.public_model_id}@{self.revision_label}"',
       '        return f"{self.public_model_id}"',
       "split_contract__the_v1_model_revision_string_is_unchanged_r62"),
    _m("digest_provenance_claims_upstream_confirmation",
       "a served-bytes digest is never recorded as a confirmed registry oid",
       FX, "        digest_source=v2.DigestSource.served_bytes,",
       "        digest_source=v2.DigestSource.registry_oid_confirmed,",
       "split_contract__the_v1_model_revision_string_is_unchanged_r62"),
    _m("an_unpinned_runtime_image_reads_as_pinned",
       "image_is_pinned is False while serve.sh pins a moving tag",
       R, "        return self.runtime_image_digest is not None",
       "        return self.runtime_image_digest is None",
       "split_contract__the_v1_model_revision_string_is_unchanged_r62"),
    _m("digest_source_defaults_to_confirmed",
       "digest provenance is stated by the writer, never defaulted (review B2a)",
       R, "    digest_source: DigestSource\n",
       "    digest_source: DigestSource = DigestSource.registry_oid_confirmed\n",
       "split_contract__the_v1_model_revision_string_is_unchanged_r62"),
    _m("two_surface_versions",
       "the whole changed surface carries ONE reviewed version identifier",
       INIT, 'SURFACE_VERSION = "contracts-v2.0"', 'SURFACE_VERSION = "contracts-v2.1"',
       "split_contract__the_surface_carries_one_reviewed_version"),

    # --- CREDIT-UNITS --------------------------------------------------------
    _m("units_are_interchangeable",
       "arithmetic across two denominations is refused by construction",
       MU, "        return other._value if type(other) is type(self) else None",
       "        return other._value if isinstance(other, Amount) else None",
       "credit_units__mixed_unit_arithmetic_is_refused_by_construction"),
    _m("raw_answers_any_unit",
       "raw(unit) refuses unless the caller names the unit it already expects",
       MU, "        if unit != type(self).UNIT:", "        if False:",
       "credit_units__mixed_unit_arithmetic_is_refused_by_construction"),
    _m("history_totals_collapse_into_one_unit",
       "a mixed history reports one figure per unit and never a combined one",
       R, "        return {unit: str(total(amounts, UNIT_TYPES[unit]))\n"
          "                for unit, amounts in sorted(by_unit.items())}",
       '        return {"CREDIT": str(total(amounts, UNIT_TYPES[unit]))\n'
       "                for unit, amounts in sorted(by_unit.items())}",
       "credit_units__a_mixed_history_totals_per_unit_and_never_once"),
    _m("a_legacy_row_is_read_as_credit",
       "a row's regime fixes its unit; the pair is never inferred",
       R, "        expected = unit_of(self.accounting_regime.value)",
       '        expected = self.unit',
       "credit_units__a_mixed_history_totals_per_unit_and_never_once"),
    _m("a_credit_row_may_carry_a_legacy_price_version",
       "price_version is the legacy regime's field and only that",
       R, '                raise ValueError("price_version is the legacy regime\'s field")',
       "                pass",
       "credit_units__a_legacy_row_invents_none_of_the_new_fields"),
    _m("the_projection_invents_a_price_version",
       "an old row's fields are read, never defaulted into something plausible",
       R, '        price_version=row.get("price_version"), settled_at=row["settled_at"])',
       '        price_version=row.get("price_version", "pv_unknown"), '
       'settled_at=row["settled_at"])',
       "credit_units__a_legacy_row_invents_none_of_the_new_fields"),
    _m("a_negative_credit_rate_is_accepted",
       "a negative rate would turn a debit into a credit at settlement",
       R, "        if self.input_rate_per_million.is_negative or "
          "self.output_rate_per_million.is_negative:",
       "        if False:",
       "credit_units__a_wrong_unit_or_unpriced_rate_card_cannot_exist"),
    _m("an_unapproved_rate_card_is_accepted",
       "an approved rate card names its approver",
       R, '        if not self.approved_by.strip():', "        if False:",
       "credit_units__a_wrong_unit_or_unpriced_rate_card_cannot_exist"),
    _m("a_nonzero_legacy_balance_needs_no_hold",
       "a nonzero legacy USD balance is a rollout hold, not a silent write-off",
       R, "        if not self.balance.is_zero and not self.rollout_hold:",
       "        if False:",
       "credit_units__a_nonzero_legacy_balance_is_a_hold_not_a_conversion"),

    # --- CREDIT-IDENTITY -----------------------------------------------------
    _m("available_ignores_reservations",
       "available is the ledger total minus active reservations",
       R, "        return self.ledger_total - self.reserved_total",
       "        return self.ledger_total",
       "credit_identity__a_consumer_credential_resolves_its_own_user_wallet"),
    _m("a_provider_wallet_claims_a_signup_entitlement",
       "only an individual's consumer wallet has a signup entitlement",
       R, "        return self.kind is WalletKind.consumer", "        return True",
       "credit_identity__a_provider_dev_credential_resolves_a_zero_provider_wallet"),
    _m("a_provider_credential_spends_any_wallet",
       "a provider credential spends only its own provider's wallet",
       P, "    if wallet.owner_provider_org_id != auth.provider_org_id:",
       "    if False:",
       "credit_identity__a_provider_dev_credential_resolves_a_zero_provider_wallet"),
    _m("a_request_may_name_a_wallet",
       "extra=forbid is what stops a payload naming a wallet, price or identity",
       R, '    model_config = ConfigDict(frozen=True, extra="forbid")',
       '    model_config = ConfigDict(frozen=True, extra="ignore")',
       "credit_identity__no_request_field_can_select_a_wallet"),
    _m("a_consumer_credential_spends_a_copied_provider_wallet",
       "a consumer credential spends a consumer wallet only, even one model_copy'd "
       "with its owner fields (review B1, G2)",
       P, "        if wallet.kind is not WalletKind.consumer:", "        if False:",
       "credit_identity__a_foreign_wallet_is_forbidden_not_a_fallback"),
    _m("a_provider_credential_spends_a_copied_consumer_wallet",
       "a provider_dev credential never spends a consumer wallet, even one model_copy'd "
       "with the provider as owner (review B1, G3)",
       P, "    if wallet.kind is not WalletKind.provider_dev:", "    if False:",
       "credit_identity__a_provider_dev_credential_resolves_a_zero_provider_wallet"),
    _m("a_credential_spends_another_users_wallet",
       "a credential spends only its own user's wallet",
       P, "        if wallet.owner_user_id != auth.user_id:", "        if False:",
       "credit_identity__a_foreign_wallet_is_forbidden_not_a_fallback"),
    _m("the_personal_org_binding_is_not_checked",
       "the wallet's personal-org binding must match the authenticated org",
       P, "        if wallet.personal_org_id != auth.org_id:", "        if False:",
       "credit_identity__a_foreign_wallet_is_forbidden_not_a_fallback"),
    _m("a_missing_wallet_resolves_to_something",
       "no wallet provisioned is not_found, never a fallback",
       P, '        raise errors.NotFound("no wallet is provisioned for this credential")',
       "        return wallet or wallet",
       "credit_identity__a_foreign_wallet_is_forbidden_not_a_fallback"),
    _m("an_operator_credential_spends_a_wallet",
       "an operator credential resolves no spendable wallet",
       P, '        raise errors.Forbidden("an operator credential does not spend a wallet")',
       "        return wallet",
       "credit_identity__an_operator_credential_spends_no_wallet"),
    _m("a_provider_wallet_receives_the_signup_grant",
       "a provider_dev wallet has no signup entitlement",
       R, "    if not wallet.has_signup_entitlement:", "    if False:",
       "credit_identity__a_provider_wallet_has_no_grant_and_no_transfer"),
    _m("a_transfer_kind_exists",
       "the ledger vocabulary is closed and has no transfer",
       R, '    inference_debit = "inference_debit"',
       '    inference_debit = "inference_debit"\n    transfer = "transfer"',
       "credit_identity__a_provider_wallet_has_no_grant_and_no_transfer"),
    _m("a_provider_wallet_may_carry_a_signup_entry",
       "a signup grant entry is only ever on a consumer wallet",
       R, "            if self.wallet_kind is not WalletKind.consumer:", "            if False:",
       "credit_identity__a_provider_wallet_has_no_grant_and_no_transfer"),
    _m("the_grant_key_includes_the_campaign",
       "the grant key is the individual and the entitlement, nothing else",
       R, "        return (self.user_id, self.entitlement)",
       "        return (self.user_id, self.entitlement, self.campaign_version)",
       "credit_identity__campaign_and_membership_never_reset_the_grant_key"),

    # --- CREDIT-GRANT --------------------------------------------------------
    _m("the_grant_amount_changes",
       "the initial grant is exactly +10000.00000000 CREDIT",
       R, 'INITIAL_SIGNUP_GRANT = Credit("10000.00000000")',
       'INITIAL_SIGNUP_GRANT = Credit("1000.00000000")',
       "credit_grant__the_grant_is_exactly_ten_thousand_credit_once"),
    _m("an_unverified_grant_is_accepted",
       "the grant records the verification evidence it was issued against",
       R, "        if not self.verification_evidence_ref.strip():", "        if False:",
       "credit_grant__the_grant_is_exactly_ten_thousand_credit_once"),
    _m("the_grant_lands_in_another_users_wallet",
       "the initial grant lands in the individual's own wallet only",
       R, "    if wallet.owner_user_id != user_id:", "    if False:",
       "credit_grant__the_grant_lands_only_in_the_individuals_own_wallet"),

    # --- CREDIT-RATE ---------------------------------------------------------
    _m("an_admission_may_carry_a_foreign_card",
       "the attached rate card must be the pinned rate_card_version",
       R, "        if self.rate_card.rate_card_version != self.pins.rate_card_version:",
       "        if False:",
       "credit_rate__admission_pins_model_serving_deployment_and_rate_card"),
    _m("an_admission_may_price_another_deployment",
       "the attached card must price the pinned deployment revision",
       R, "        if self.rate_card.deployment_revision_id != "
          "self.pins.deployment_revision_id:",
       "        if False:",
       "credit_rate__admission_pins_model_serving_deployment_and_rate_card"),
    _m("the_pin_hardcodes_a_rate_card_version",
       "the pin records the card the catalog resolved, not a constant",
       P, "                         rate_card_version=rate_card.rate_card_version,",
       '                         rate_card_version="rc_marlin2b_2026_09_provisional",',
       "credit_rate__a_rate_published_after_acceptance_does_not_move_the_job",
       # the admission's own validator refuses pins that disagree with the attached card
       # (`records.py`, "not the pinned rate_card_version") - the invariant held
       # structurally, raised as pydantic's ValidationError
       dies_by=("ValidationError",)),
    _m("publishing_a_rate_does_nothing",
       "the case really does change the published rate before settling",
       FAKE, "        self.rate_cards[card.deployment_revision_id] = card", "        pass",
       "credit_rate__a_rate_published_after_acceptance_does_not_move_the_job"),
    _m("the_settlement_records_the_wrong_serving_revision",
       "a settlement records the serving revision the job was admitted with",
       R, "        serving_version_id=admission.pins.serving_version_id,",
       "        serving_version_id=admission.pins.model_id,",
       "credit_rate__an_alias_moved_after_acceptance_does_not_move_the_job"),
    _m("an_unpriced_deployment_is_admitted",
       "a deployment with no approved active CREDIT card is unserveable, not free",
       FAKE, "        rate_cards={prod.deployment_revision_id: card},",
       "        rate_cards={prod.deployment_revision_id: card,\n"
       "                    dev.deployment_revision_id: card},",
       "credit_rate__an_unknown_private_or_unpriced_model_is_refused"),
    _m("pin_admission_takes_a_caller_rate_card",
       "R45 extended: no parameter exists through which a caller supplies a price",
       P, "def pin_admission(*, auth: AuthContextV2, requested_model: str,",
       "def pin_admission(*, auth: AuthContextV2, requested_model: str,\n"
       "                  caller_rate_card_version: str | None = None,",
       "credit_rate__a_caller_supplied_price_or_identity_is_refused"),
    _m("the_hold_rounds_like_a_charge",
       "the maximum hold rounds up; equal roundings under-reserve",
       R, "        return Credit(money.maximum_hold(max_input_tokens, max_output_tokens, "
          "*self._rates()))",
       "        return Credit(money.debit(max_input_tokens, max_output_tokens, "
       "*self._rates()))",
       "credit_rate__the_hold_rounds_up_and_the_charge_rounds_half_up_once"),
    _m("the_charge_rounds_up",
       "the final charge rounds half up once, never away from the customer",
       R, "        return Credit(money.debit(prompt_tokens, completion_tokens, *self._rates()))",
       "        return Credit(money.maximum_hold(prompt_tokens, completion_tokens, "
       "*self._rates()))",
       "credit_rate__the_hold_rounds_up_and_the_charge_rounds_half_up_once"),
    _m("a_settlement_may_exceed_the_hold",
       "usage beyond the reserved envelope is a platform incident, not a debit",
       R, "    if charged > admission.maximum_hold:", "    if False:",
       "credit_rate__the_hold_rounds_up_and_the_charge_rounds_half_up_once"),
    _m("records_are_mutable",
       "admitted pins cannot be assigned in place (review B2b)",
       R, '    model_config = ConfigDict(frozen=True, extra="forbid")',
       '    model_config = ConfigDict(frozen=False, extra="forbid")',
       "credit_rate__admitted_pins_are_immutable_and_every_pin_is_required"),
    _m("a_pin_is_optional",
       "every identity pin is required; none is left to a default (review B2c)",
       R, "    deployment_revision_id: UuidStr\n    serving_version_id: UuidStr\n"
          "    rate_card_version: str\n    policy_version: str\n",
       "    deployment_revision_id: UuidStr\n    serving_version_id: UuidStr | None = None\n"
       "    rate_card_version: str\n    policy_version: str\n",
       "credit_rate__admitted_pins_are_immutable_and_every_pin_is_required"),
    _m("admission_pins_are_optional",
       "an admission always carries its pins (review B2c)",
       R, "    pins: AdmissionPins\n    rate_card: RateCardSnapshot\n",
       "    pins: AdmissionPins | None = None\n    rate_card: RateCardSnapshot\n",
       "credit_rate__admitted_pins_are_immutable_and_every_pin_is_required"),
    _m("settle_ignores_certainty",
       "only authoritative engine usage settles a debit",
       R, "    if usage.certainty is not records.UsageCertainty.authoritative:",
       "    if False:",
       "credit_rate__unknown_usage_is_never_settled"),

    # --- LAB-ACCESS ----------------------------------------------------------
    _m("a_role_grants_customer_content",
       "provider ownership alone yields no customer payload",
       R, "        ProviderCapability.manage_dev_deployment,\n"
          "        ProviderCapability.run_evaluation,\n    }),",
       "        ProviderCapability.manage_dev_deployment,\n"
       "        ProviderCapability.run_evaluation,\n"
       "        ProviderCapability.read_customer_content,\n    }),",
       "lab_access__provider_ownership_alone_yields_no_customer_payload"),
    _m("content_access_needs_only_a_membership",
       "a current membership AND a current grant, every time",
       R, "    if membership is None or grant is None:\n        return False",
       "    if membership is None:\n        return False\n    if grant is None:\n"
       "        return True",
       "lab_access__provider_ownership_alone_yields_no_customer_payload"),
    _m("revocation_is_ignored",
       "revocation blocks new access immediately, not at the next snapshot",
       R, "        if self.revoked_at is not None and now >= self.revoked_at:\n"
          "            return False",
       "        if False:\n            return False",
       "lab_access__a_revoked_grant_blocks_access_immediately"),
    _m("expiry_is_ignored",
       "an expired grant authorizes nothing",
       R, "        return self.expires_at is None or now < self.expires_at",
       "        return True",
       "lab_access__an_expired_grant_and_a_rival_provider_are_refused"),
    _m("the_recipient_provider_is_not_checked",
       "a grant names one recipient provider",
       R, "                and provider_org_id == self.recipient_provider_org_id",
       "                and True",
       "lab_access__an_expired_grant_and_a_rival_provider_are_refused"),
    _m("the_purpose_is_not_checked",
       "capture, sharing, external judging and training are four permissions",
       R, "                and purpose in self.purposes)", "                and True)",
       "lab_access__each_purpose_is_a_separate_permission"),
    _m("the_category_is_not_checked",
       "a grant names the data categories it covers",
       R, "                and category in self.categories", "                and True",
       "lab_access__each_purpose_is_a_separate_permission"),
    _m("a_revoked_membership_still_permits",
       "a revoked membership and a foreign provider permit nothing",
       R, "        if provider_org_id != self.provider_org_id or not self.is_current(now):\n"
          "            return False",
       "        if False:\n            return False",
       "lab_access__roles_default_deny_and_a_viewer_reaches_nothing"),
    _m("every_role_permits_everything",
       "a role permits exactly its own listed capabilities; unknown is denied",
       R, "        return capability in ROLE_CAPABILITIES[self.role]", "        return True",
       "lab_access__roles_default_deny_and_a_viewer_reaches_nothing"),
)


def run_mutant(mutant: Mutant) -> Result:
    return v1runner.run_mutant(mutant, RUNNER)


def main() -> int:
    return v1runner.main(MUTANTS, RUNNER, "run the contracts-v2 mutation list")


if __name__ == "__main__":
    raise SystemExit(main())
