#!/usr/bin/env python3
"""The v2 fixture base and the v1 -> v2 field map (F2P item 1).

Three things are checked, and each is a way a fixture base rots:

1. every file under `fixtures/v2/` is claimed by a model or declared a table — an
   unclassified fixture is a hole in the contract;
2. every file is byte-identical to what `fixtures.build()` produces now — so a
   record change that nobody regenerated fails here rather than at a track's
   integration;
3. `map.json`'s field lists are checked against the live `model_fields` of BOTH
   revisions — so the map cannot describe a field that does not exist, omit one
   that does, or quietly stop covering a record.

    uv run --frozen pytest -q tests/contracts/v2/test_fixtures_v2.py
    uv run --frozen python -m infrx.contracts.v2.fixtures --write   # regenerate
"""
from __future__ import annotations

import json

import pytest
from infrx.contracts import codec, records as v1
from infrx.contracts.v2 import SURFACE_VERSION, fixtures as v2fix, records as v2

NAMES = v2fix.names()
BUILT = v2fix.build()


def test_every_committed_fixture_is_claimed():
    claimed = set(v2fix.MODELS) | set(v2fix.TABLES)
    assert set(NAMES) == claimed, (
        f"unclaimed: {sorted(set(NAMES) - claimed)}; "
        f"declared but missing: {sorted(claimed - set(NAMES))}")
    assert set(v2fix.BUILDERS) == set(v2fix.MODELS), "a builder without a model, or the reverse"


@pytest.mark.parametrize("name", NAMES)
def test_every_fixture_is_exactly_what_the_records_produce_now(name):
    assert BUILT[name] == v2fix.load_bytes(name), (
        f"{name} is stale: run `uv run --frozen python -m infrx.contracts.v2.fixtures --write`")


@pytest.mark.parametrize("name", sorted(v2fix.MODELS))
def test_every_single_model_fixture_round_trips_to_identical_bytes(name):
    model = v2fix.model(name)
    assert codec.canonical_bytes(model) == v2fix.load_bytes(name)
    assert model.schema_version == 2


def test_the_canonical_form_is_the_house_style():
    for name in v2fix.MODELS:
        raw = v2fix.load_bytes(name).decode()
        assert raw.endswith("}\n"), name
        assert "null" not in json.dumps(json.loads(raw)), f"{name} carries a null"
        assert json.dumps(json.loads(raw), sort_keys=True) == \
            json.dumps(json.loads(raw)), f"{name} is not key-sorted"


# --- the map ----------------------------------------------------------------
MAP = v2fix.load("map.json")
ENTRIES = {entry["record"]: entry for entry in MAP["records"]}
V2_RECORDS = {name: value for name, value in vars(v2).items()
              if isinstance(value, type) and issubclass(value, v2.RecordV2)
              and value is not v2.RecordV2}


def test_the_map_declares_the_one_reviewed_surface_version():
    assert MAP["surface_version"] == SURFACE_VERSION == "contracts-v2.1"
    assert MAP["schema_version"] == v2.SCHEMA_VERSION == 2
    assert MAP["appendix"] == "research/plan/01a-contracts-v2-map.md"
    assert MAP["database_map"] == "research/plan/06a-database-map-v2.md"


def test_the_map_covers_every_v2_record():
    """A record absent from the map is a field nobody has to explain to D1R."""
    assert set(ENTRIES) == set(V2_RECORDS), (
        f"unmapped records: {sorted(set(V2_RECORDS) - set(ENTRIES))}; "
        f"mapped but nonexistent: {sorted(set(ENTRIES) - set(V2_RECORDS))}")
    assert len(ENTRIES) == len(MAP["records"]), "a record is mapped twice"


@pytest.mark.parametrize("record", sorted(ENTRIES))
def test_each_map_entry_matches_the_live_field_sets(record):
    entry = ENTRIES[record]
    v2_fields = set(V2_RECORDS[record].model_fields) - {"schema_version"}
    assert set(entry["same_name"]) | set(entry["added"]) == v2_fields, (
        f"{record}: the map's same_name + added is not the record's field set")
    assert not set(entry["same_name"]) & set(entry["added"]), f"{record}: a field in both lists"
    assert entry["note"].strip(), f"{record}: no note"
    if entry["v1"] is None:
        assert entry["same_name"] == [] and entry["dropped"] == [], \
            f"{record}: a new record cannot keep or drop a v1 field"
        return
    v1_model = getattr(v1, entry["v1"])
    v1_fields = set(v1_model.model_fields) - {"schema_version"}
    assert set(entry["same_name"]) == v2_fields & v1_fields, f"{record}: same_name is wrong"
    assert set(entry["dropped"]) == v1_fields - v2_fields, f"{record}: dropped is wrong"


def test_the_map_records_the_composition_that_keeps_public_fields_unchanged():
    """The two records that nest their v1 predecessor say so, so a reader knows the
    public fields were not restated (and therefore cannot drift)."""
    nesting = {name for name, entry in ENTRIES.items() if entry["nests_v1_as"]}
    assert nesting == {"NormalizedRequestV2", "WorkV2"}, nesting
    for name in nesting:
        assert ENTRIES[name]["nests_v1_as"] == "request"
    assert v2.NormalizedRequestV2.model_fields["request"].annotation is v1.NormalizedRequest


def test_the_renamed_money_record_is_mapped_from_the_v1_snapshot():
    card = ENTRIES["RateCardSnapshot"]
    assert card["v1"] == "PriceSnapshot"
    assert "currency" in card["dropped"], "the USD currency field is gone, not renamed"
    assert "price_version" in card["dropped"]
    assert "rate_card_version" in card["added"] and "meter" in card["added"]
    assert "unit" in card["added"]


# --- the provisional rate ----------------------------------------------------
def test_the_marlin_rate_card_is_labelled_provisional():
    """P-01 (the operator-approved rate) is a pending input. The fixture must say so
    in the record, not only in a comment, so a run that used it is identifiable."""
    card = v2.RateCardSnapshot.model_validate(v2fix.load("rate_card_marlin.json"))
    assert "provisional" in card.approved_by and "P-01" in card.approved_by
    assert str(card.input_rate_per_million) == "400.00000000"
    assert str(card.output_rate_per_million) == "1200.00000000"
    # the documented derivation: one 2-minute clip at the S2M video operating point
    assert str(card.debit(23500, 512)) == "10.01440000"
    # ... so the 10,000 CREDIT grant buys ~998 of them, and is not "10,000 requests"
    clips = v2.INITIAL_SIGNUP_GRANT.raw("CREDIT") / card.debit(23500, 512).raw("CREDIT")
    assert 950 < clips < 1050, clips


def test_the_fixture_identities_are_lowercase_uuidv4_and_distinct():
    ids = {name: value for name, value in vars(v2fix.IDS).items()
           if not name.startswith("_") and isinstance(value, str)}
    assert len(set(ids.values())) == len(ids), "two fixture identities collide"
    for name, value in ids.items():
        assert v1.ids.is_request_id(value), f"{name} is not a lowercase UUIDv4: {value}"
