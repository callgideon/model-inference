"""WR-AP06-2: the Lab unit mounts the publication door only with LAB_PUBLICATION, and refuses
to start with it while AP-01's session actors are not composed on the unit."""
from __future__ import annotations

import pytest

from infrx.config import RuntimeMisconfigured
from infrx.contracts.openapi import export


def paths(app):   # FastAPI 0.141 keeps an included router as one `_IncludedRouter` entry
    return {getattr(r, "path", "") for entry in app.routes
            for r in getattr(getattr(entry, "original_router", None), "routes", [entry])}


def test_composed__the_lab_unit_mounts_publication_only_with_lab_publication():
    off = paths(export._lab_control(publication=False))
    assert not {p for p in off if p.startswith(("/operator/v1/publication", "/lab/v1/control/endpoints"))}
    on = paths(export._lab_control())
    assert "/operator/v1/publication-proposals/{proposal_id}/approve" in on
    assert "/lab/v1/control/endpoints/{endpoint_id}/keys" in on


def test_composed__lab_publication_without_session_actors_refuses_startup(monkeypatch):
    from infrx.lab.control import app as control
    for name in ("CLICKHOUSE_URL", "S3_TRACE_BUCKET", "LAB_CHECKPOINT_KEYS"):
        monkeypatch.delenv(name, raising=False)
    for name, value in {control.DATABASE_URL: "postgresql://t@127.0.0.1:1/t",
                        control.SUPABASE_URL: "http://127.0.0.1:1", control.SUPABASE_KEY: "t",
                        "LAB_PUBLICATION": "1"}.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeMisconfigured, match="LAB_PUBLICATION"):
        control.create_app()


def test_composed__the_lab_unit_composes_0068s_dev_credentials(monkeypatch):
    """WR-AS3-2: with LAB_PUBLICATION the dev-key listing/revocation and the dev wallet stand
    on `PgDevCredentials` over the unit's own login - not 503."""
    from infrx.gateway.routes import operator_publication
    from infrx.lab.publication import PgDevCredentials
    seen, real = [], operator_publication.register
    monkeypatch.setattr(operator_publication, "register",
                        lambda app, rt: seen.append(rt) or real(app, rt))
    export._lab_control()
    [rt] = seen
    assert isinstance(rt.lab_publication.credentials, PgDevCredentials)
