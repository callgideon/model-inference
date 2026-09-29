#!/usr/bin/env python3
"""0-F1 fix round, finding 0-F2: `0045_lab_serving_ref_identity.sql`'s own header comment
promises that `tests/d/test_d9_rollout_serving_ref.py` "pins the current shape so it fails
loudly" if `ServingRevision`/`CapabilityRecord` (`infrx/contracts/v2/records.py`) drift from
`infrx.lab_serving_ref_digest`'s hand-written JCS-in-SQL reimplementation - that file did not
exist. This is it: it builds the same row two ways (a real `ServingRevision`, read back through
`PgCatalogDirectory.serving_revision` - the same path every production caller uses - and the
SQL function of the same id) and asserts the two digests match byte-for-byte, for the two field
combinations the migration's header comment claims were checked (every optional field null with
a zero-microsecond timestamp; every optional field set, two weight shards, every
`CapabilityRecord` flag non-default, a microsecond timestamp).

    INFRX_D_TASK=dlab uv run --frozen pytest -q tests/d/test_d9_rollout_serving_ref.py
"""
from __future__ import annotations

import asyncio
import hashlib

import pytest
from infrx.contracts.lab.records import canonical
from infrx.state import migrations
from infrx.state.catalog import PgCatalogDirectory
from infrx.state.jobstore import connector
from psycopg.types.json import Jsonb

from . import pgharness
from . import test_l3sql_control as t

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_d9servingref"
NEMO = t.NEMO

MINIMAL_CAPABILITY = {"api_family": "chat_completions", "billing_meter": "tokens-v1",
                      "input_modalities": ["text"], "output_modalities": ["text"],
                      "stream_output": False, "input_schema_ref": "s",
                      "output_schema_ref": "s", "preprocessing_profile_ref": "s"}
MAXIMAL_CAPABILITY = {"api_family": "chat_completions", "billing_meter": "tokens-v1",
                      "input_modalities": ["text", "video"], "output_modalities": ["text"],
                      "stream_output": True, "stream_input": True, "tools": True,
                      "structured_output": True, "input_schema_ref": "s2",
                      "output_schema_ref": "s2", "preprocessing_profile_ref": "p2",
                      "schema_version": 2}
ROWS = {
    "minimal (nulls, zero-microsecond timestamp)": {
        "model": "e0000017-0000-4000-8000-000000000001",
        "version": "e0000017-0000-4000-8000-000000000002",
        "serving": "e0000017-0000-4000-8000-000000000003",
        "model_repo": "nemo/min", "commit": "a" * 40, "shards": ["sha256:" + "a" * 64],
        "adapter": None, "tokenizer": "sha256:" + "b" * 64,
        "chat_template": "sha256:" + "c" * 64, "digest_source": "served_bytes",
        "revision_label": "min-1", "runtime_image_digest": None,
        "engine_options_digest": "sha256:" + "2" * 64, "capability": MINIMAL_CAPABILITY,
        "created_at": "2026-01-01T00:00:00+00:00"},
    "maximal (every optional field set, two shards, microsecond timestamp)": {
        "model": "e0000017-0000-4000-8000-000000000004",
        "version": "e0000017-0000-4000-8000-000000000005",
        "serving": "e0000017-0000-4000-8000-000000000006",
        "model_repo": "nemo/max", "commit": "d" * 40,
        "shards": ["sha256:" + "d" * 64, "sha256:" + "e" * 64],
        "adapter": "sha256:" + "f" * 64, "tokenizer": "sha256:" + "0" * 64,
        "chat_template": "sha256:" + "1" * 64, "digest_source": "registry_oid_confirmed",
        "revision_label": "max-1", "runtime_image_digest": "sha256:" + "3" * 64,
        "engine_options_digest": "sha256:" + "4" * 64, "capability": MAXIMAL_CAPABILITY,
        "created_at": "2026-01-01T00:00:00.123456+00:00"}}


def seed(conn) -> None:
    t.seed(conn)
    for label, row in ROWS.items():
        conn.execute(
            "insert into public.models (id, name, provider, description, status, base_url, "
            "served_model, input_usd_per_m, output_usd_per_m, context_tokens, "
            "input_modalities, output_modalities, model_uuid, provider_org_id) values "
            "(%(id)s, 's', 's', 's', 'live', 'https://s.example', 's', 1, 1, 1024, '{text}', "
            "'{text}', %(model)s, %(p)s)",
            {"id": f"nemostation/ref-{row['model'][-1]}", "model": row["model"], "p": NEMO})
        conn.execute(
            "insert into infrx.model_versions (model_version_id, model_id, provider_org_id, "
            "model_repo, model_commit, weight_shard_digests, adapter_digest, "
            "tokenizer_digest, chat_template_digest, digest_source, created_by) values "
            "(%(version)s, %(model)s, %(p)s, %(model_repo)s, %(commit)s, %(shards)s, "
            "%(adapter)s, %(tokenizer)s, %(chat_template)s, %(digest_source)s, 'ops')",
            {**row, "p": NEMO})
        conn.execute(
            "insert into infrx.serving_versions (serving_version_id, model_version_id, "
            "model_id, provider_org_id, revision_label, prompt_harness_ref, "
            "preprocessor_profile_version, runtime_image_ref, runtime_image_digest, "
            "engine_options_digest, precision, capability, created_by, created_at) values "
            "(%(serving)s, %(version)s, %(model)s, %(p)s, %(revision_label)s, 'harness', "
            "'profile', %(image)s, %(runtime_image_digest)s, %(engine_options_digest)s, "
            "'bf16', %(capability)s, 'ops', %(created_at)s)",
            {**row, "p": NEMO, "image": f"image:{row['revision_label']}",
             "capability": Jsonb(row["capability"])})


def check_the_sql_digest_matches_python_byte_for_byte(conn) -> str:
    """0-F2: `infrx.lab_serving_ref_digest` and `operations.serving_ref`'s digest half must
    agree on the same row - the safety net 0045's header comment promises against a future
    `ServingRevision`/`CapabilityRecord` field-set drift."""
    catalog = PgCatalogDirectory(connector(pgharness.dsn(conn.info.dbname)))
    mismatches = {}
    for label, row in ROWS.items():
        serving = asyncio.run(catalog.serving_revision(row["serving"]))
        assert serving is not None, f"{label}: the row was not read back"
        python_digest = hashlib.sha256(canonical(serving.model_dump(mode="json"))).hexdigest()
        sql_digest = conn.execute("select infrx.lab_serving_ref_digest(%s)",
                                  (row["serving"],)).fetchone()[0]
        if python_digest != sql_digest:
            mismatches[label] = (python_digest, sql_digest)
    assert not mismatches, mismatches
    return f"{len(ROWS)} field combinations match byte-for-byte"


CHECKS = {c.__name__: c for c in (check_the_sql_digest_matches_python_byte_for_byte,)}


@pytest.fixture(scope="module")
def conn():
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    with pgharness.connect(DB) as connection:
        seed(connection)
        yield connection


@pytest.mark.parametrize("name", list(CHECKS))
def test_d9_rollout_serving_ref(conn, name) -> None:
    print(f"{name}: {CHECKS[name](conn)}")
