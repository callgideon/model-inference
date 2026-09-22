#!/usr/bin/env python3
"""SPLIT-CONTRACT parity: the Python and console halves of contracts v2 agree.

The v1 equivalent is `tests/contracts/test_parity_console.py`, and this reuses its
TypeScript literal parser rather than writing a second one. Reads the console
sources by relative path; no Node needed. A literal the parser cannot find is a
failure, never a skip: a renamed constant must break this test rather than
silently stop being compared.

    uv run --frozen pytest -q tests/contracts/v2/test_parity_v2.py
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from infrx.contracts.v2 import SURFACE_VERSION, fixtures as v2fix, money_units as mu, records as v2

from ..test_parity_console import ts_string_array

API = Path(__file__).resolve().parents[3]              # apps/infrx-api
CONSOLE = API.parent / "app"
TYPES = CONSOLE / "lib" / "contracts" / "v2" / "types.ts"
UNITS = CONSOLE / "lib" / "contracts" / "v2" / "money-units.ts"

TYPES_SOURCE = TYPES.read_text()
UNITS_SOURCE = UNITS.read_text()

# console constant -> the Python vocabulary it mirrors. Every v2 enum both halves
# declare is here; `test_every_v2_enum_is_compared` fails if one is left out.
SHARED_ENUMS = {
    "CREDENTIAL_AUDIENCES": v2.CredentialAudience,
    "WALLET_KINDS": v2.WalletKind,
    "LEDGER_ENTRY_KINDS_V2": v2.LedgerEntryKind,
    "PROVIDER_ROLES": v2.ProviderRole,
    "PROVIDER_CAPABILITIES": v2.ProviderCapability,
    "DATA_CATEGORIES": v2.DataCategory,
    "DATA_PURPOSES": v2.DataPurpose,
    "ENVIRONMENTS": v2.Environment,
    "VISIBILITIES": v2.Visibility,
    "DEPLOYMENT_STATES": v2.DeploymentState,
    "DIGEST_SOURCES": v2.DigestSource,
}

UNIT_ENUMS = {
    "MONEY_UNITS": mu.UNITS,
    "ACCOUNTING_REGIMES": mu.ACCOUNTING_REGIMES,
}


def ts_literal(source: str, name: str) -> str:
    """One exported string constant. A renamed or non-string constant fails."""
    match = re.search(rf'export const {name}\s*(?::[^=]+)?=\s*"([^"]*)"', source)
    assert match, f"{name}: string constant not found"
    return match.group(1)


def ts_number(source: str, name: str) -> int:
    match = re.search(rf"export const {name}\s*(?::[^=]+)?=\s*([0-9_]+)\s*;", source)
    assert match, f"{name}: numeric constant not found"
    return int(match.group(1).replace("_", ""))


@pytest.mark.parametrize("name", sorted(SHARED_ENUMS))
def test_the_console_declares_the_same_vocabulary(name):
    """Values AND order: the console's list is the enum's declaration order, so a
    reordered list is a visible change rather than a silent one."""
    assert ts_string_array(TYPES_SOURCE, name) == [member.value
                                                   for member in SHARED_ENUMS[name]]


@pytest.mark.parametrize("name", sorted(UNIT_ENUMS))
def test_the_console_declares_the_same_units_and_regimes(name):
    assert ts_string_array(UNITS_SOURCE, name) == list(UNIT_ENUMS[name])


def test_every_v2_enum_is_compared():
    """A vocabulary added to Python and forgotten in TypeScript must fail here, so
    the list of compared enums is itself checked against the module."""
    import enum
    declared = {name for name, value in vars(v2).items()
                if isinstance(value, type) and issubclass(value, enum.StrEnum)}
    compared = {enum_type.__name__ for enum_type in SHARED_ENUMS.values()}
    # `AccountingRegime` is compared through the money-units half instead.
    assert declared - compared == {"AccountingRegime"}, sorted(declared - compared)


def test_the_surface_and_schema_versions_are_one_value_in_both_halves():
    """F2P item 7: ONE reviewed version identifier for the whole changed surface."""
    assert ts_literal(UNITS_SOURCE, "SURFACE_VERSION") == SURFACE_VERSION
    assert ts_number(UNITS_SOURCE, "V2_SCHEMA_VERSION") == v2.SCHEMA_VERSION == 2
    assert v2fix.load("map.json")["surface_version"] == SURFACE_VERSION


def test_the_grant_amount_and_entitlement_are_identical_in_both_halves():
    assert ts_literal(TYPES_SOURCE, "INITIAL_SIGNUP_GRANT_CREDIT") == \
        str(v2.INITIAL_SIGNUP_GRANT) == "10000.00000000"
    assert ts_literal(TYPES_SOURCE, "INITIAL_SIGNUP_ENTITLEMENT") == \
        v2.INITIAL_SIGNUP_ENTITLEMENT


def test_the_meter_vocabulary_is_the_same_single_value():
    assert ts_string_array(TYPES_SOURCE, "BILLING_METERS") == [v2.METER_TOKENS_V1]


def test_the_console_role_capability_sets_are_identical():
    """The default-deny table is the thing a UI would be tempted to widen locally."""
    for role, capabilities in v2.ROLE_CAPABILITIES.items():
        match = re.search(rf"^\s*{role.value}: \[(.*?)\],$", TYPES_SOURCE,
                          re.S | re.M)
        assert match, f"{role.value}: no capability list in {TYPES}"
        listed = re.findall(r'"([a-z_]+)"', match.group(1))
        assert set(listed) == {c.value for c in capabilities}, role.value
        assert "read_customer_content" not in listed, \
            "no role's capability set may contain read_customer_content"


def test_the_console_has_no_conversion_between_units():
    """The absence is the contract, in both languages."""
    for source, path in ((UNITS_SOURCE, UNITS), (TYPES_SOURCE, TYPES)):
        lowered = source.lower()
        for forbidden in ("convert", "exchangerate", "tocredit(", "tousd(", "creditperusd",
                          "usdpercredit"):
            assert forbidden not in lowered, f"{path.name} mentions {forbidden}"


def test_the_console_reads_the_python_fixture_base_rather_than_a_copy():
    """No byte-identical copies to diff by hand: the console's v2 suites read the
    generated fixtures from the Python package, so the two halves cannot drift."""
    for test_file in sorted((CONSOLE / "tests" / "contracts" / "v2").glob("*.test.ts")):
        source = test_file.read_text()
        if "fixtures/v2" not in source:
            continue
        assert "infrx-api/infrx/contracts/fixtures/v2" in source, test_file
    # `mutants-v2.json` is the console's own mutation list, not a fixture copy; every
    # other JSON file here would be one, and a copy is a thing that can drift.
    copies = [path.name for path in (CONSOLE / "tests" / "contracts" / "v2").glob("*.json")
              if path.name != "mutants-v2.json"]
    assert copies == [], f"v2 fixtures must not be copied into the console: {copies}"


def test_the_v1_parity_surface_is_untouched():
    """F-BASE: the v1 console DTO file still declares v1's own vocabulary, and v2 reaches
    it only as the `v2` namespace (wire-in item 9) - never as flat declarations there."""
    v1_types = (CONSOLE / "lib" / "contracts" / "types.ts").read_text()
    assert ts_string_array(v1_types, "JOB_STATES")[0] == "preparing"
    assert "CREDENTIAL_AUDIENCES" not in v1_types, \
        "v2 vocabulary belongs in lib/contracts/v2/, not in the v1 file"
