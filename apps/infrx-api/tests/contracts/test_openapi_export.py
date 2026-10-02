"""AP-00 00a/00b: the route inventory and the checked OpenAPI artifacts (R270, R271).

    uv run --frozen pytest -q tests/contracts/test_openapi_export.py
    uv run --frozen python -m infrx.contracts.openapi.export      # rewrite the artifacts

The artifacts (`apps/infrx-api/openapi/*.json`, the route inventory under
`research/plan/api-lifecycle/evidence/`) are regenerated here and diffed: a route change
without its regenerated artifact fails. `baseline.json` lists today's schema-less routes; a
route outside it must declare its body and response schemas, and the list may only shrink.
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI, Request
from pydantic import BaseModel

from infrx.contracts.openapi import export, inventory

#: The number of schema-less routes AP-00 started from (00b). It may only go down: a lane
#: that adds a route without schemas fails `legacy == baseline`, and one that adds it to the
#: baseline instead fails this pin.
BASELINE_ENTRIES = 60


@pytest.fixture(scope="module")
def apps() -> dict[str, FastAPI]:
    return export.compositions()


def exported(apps: dict[str, FastAPI]) -> dict[str, FastAPI]:
    return {name: apps[name] for name in export.ARTIFACTS}


def mounted(app) -> set[str]:
    """Every (method, path) the app serves, included routers walked like the export does."""
    return {f"{method} {route.path_format}" for route in export.routes(app) for method in route.methods or ()}


# --- 00a: the inventory -----------------------------------------------------------------------
def test_ap00_inventory_names_every_mounted_route_of_every_composition(apps):
    """Oracle: each composition's rows are exactly its mounted (method, path) pairs - a route
    the inventory forgets, or one it invents, fails."""
    doc = inventory.build(apps)
    for name, app in apps.items():
        rows = {f"{row['method']} {row['path']}" for row in doc["routes"]
                if name in row["compositions"]}
        assert rows == mounted(app), name
    launched = {f"{row['method']} {row['path']}" for row in doc["routes"]
                if "consumer-launched" in row["compositions"]}
    assert "POST /v1/feedback" not in launched and "POST /v1/chat/completions" in launched


def test_ap00_inventory_joins_web_actions_to_target_operations(apps):
    """Oracle (R271): every web action names its target operation, the target table says
    whether that operation is mounted today, and every action that reaches the database or
    the service-role client directly is flagged."""
    doc = inventory.build(apps)
    targets = {f"{t['method']} {t['path']}": t for t in doc["targets"]}
    every_route = {(row["method"], inventory.shape_of(row["path"])) for row in doc["routes"]}
    for target in doc["targets"]:
        mounted_now = (target["method"], inventory.shape_of(target["path"])) in every_route
        assert target["state"] == ("existing" if mounted_now else "target"), target
    for action in doc["web_actions"]:
        for wanted in action["targets"]:
            assert wanted in targets, (action["action"], wanted)
        assert action["flag"] == (action["transport"] in inventory.BYPASS), action["action"]
    assert targets["GET /v1/models"]["state"] == "existing"
    assert targets["GET /lab/v1/traces/{id}"]["state"] == "existing"     # {request_id} mounted
    assert targets["POST /console/v1/keys"]["state"] == "existing"           # AP-03 mounted (merge #88)
    assert targets["POST /lab/v1/control/deployments"]["state"] == "target"  # AP-05, batch 2
    flagged = {a["action"] for a in doc["web_actions"] if a["flag"]}
    assert {"createConsumerKey", "claimSignupGrant"} <= flagged


def test_ap00_inventory_artifact_is_current(apps):
    """Oracle: the committed inventory is what the compositions produce now."""
    doc = inventory.build(apps)
    assert json.loads(inventory.JSON_PATH.read_text()) == doc
    assert inventory.MD_PATH.read_text() == inventory.markdown(doc)


# --- 00b: the export -----------------------------------------------------------------------
def test_ap00_export_artifacts_are_current(apps):
    """Oracle: the checked OpenAPI artifact of each enabled composition is regenerated and
    compared; a route or schema change without `export` rerun fails."""
    for name in export.ARTIFACTS:
        assert (export.ARTIFACT_DIR / f"{name}.json").read_text() == \
            export.render(export.document(apps[name], name)), name


def test_ap00_every_operation_is_named_secured_and_present(apps):
    """Oracle: every mounted route is one documented operation with a unique operationId
    and a security declaration (an explicit empty list for the public few)."""
    for name in export.ARTIFACTS:
        doc = export.document(apps[name], name)
        ops = [(path, method, op) for path, item in doc["paths"].items()
               for method, op in item.items()]
        assert {f"{m.upper()} {p}" for p, m, _ in ops} == mounted(apps[name]), name
        ids = [op["operationId"] for _, _, op in ops]
        assert len(ids) == len(set(ids)), name
        assert all("security" in op for _, _, op in ops), name
        schemes = set(doc["components"]["securitySchemes"])
        assert {s for _, _, op in ops for req in op["security"] for s in req} <= schemes
    consumer = export.document(apps["consumer"], "consumer")["paths"]
    assert consumer["/v1/jobs"]["post"]["security"] == [{"ApiKeyAuth": []}]
    assert consumer["/v1/models"]["get"]["security"] == []
    lab = export.document(apps["lab-control"], "lab-control")["paths"]
    assert lab["/lab/v1/control/models"]["get"]["security"] == [{"SessionBearer": []}]
    assert lab["/lab/v1/checkpoints"]["post"]["security"] == [{"CheckpointSignature": []}]


def test_ap00_an_unclassified_route_family_refuses_the_export():
    """Oracle (missing auth schema): a route outside every known family, declaring no
    security of its own, is an export error - never an undocumented operation."""
    app = FastAPI()

    @app.get("/elsewhere/v1/things", response_model=dict)
    async def things() -> dict:
        return {}

    with pytest.raises(ValueError, match="/elsewhere/v1/things"):
        export.document(app, "x")
    assert export.operation_id("post", "/lab/v1/control/deployments/{deployment_revision_id}/smoke") \
        == "postLabV1ControlDeploymentsByDeploymentRevisionIdSmoke"


def test_ap00_a_schema_less_route_is_legacy_and_a_typed_one_is_not():
    """Oracle: a handler reading the raw `Request`, or answering without a response model,
    is legacy; a typed body plus a response model is not."""
    app = FastAPI()

    class Body(BaseModel):
        name: str

    @app.post("/console/v1/raw", response_model=Body)    # answers typed, reads raw
    async def raw(request: Request):
        return {}

    @app.get("/console/v1/untyped")
    async def untyped():
        return {}

    @app.post("/console/v1/typed", response_model=Body)
    async def typed(body: Body):
        return body

    assert export.legacy({"x": app}) == {"x": ["GET /console/v1/untyped", "POST /console/v1/raw"]}


def test_ap00_the_baseline_is_exact_and_only_shrinks(apps):
    """Oracle: the schema-less routes are exactly the committed baseline (a new one fails, a
    fixed one must leave it), and the baseline is no longer than AP-00's start."""
    baseline = json.loads(export.BASELINE_PATH.read_text())
    assert export.legacy(exported(apps)) == baseline
    assert sum(len(entries) for entries in baseline.values()) <= BASELINE_ENTRIES


def test_ap00_a_get_reading_the_request_with_a_declared_response_is_documented(apps):
    """Oracle: an R270 read (raw `Request` for the request id + actor, `response_model`
    declared) is not legacy; a body method with a raw body still is."""
    legacy = export.legacy(exported(apps))
    assert "GET /console/v1/credits" not in legacy["consumer"]
    assert any(entry.startswith("POST ") for entry in legacy["consumer"])


def test_ap00_a_typed_body_answering_204_no_content_is_documented(apps):
    """Oracle (WR-AP01-1): a typed body answered by a declared 204 No Content (the auth
    facade's password change) is not legacy - there is no response body to model."""
    legacy = export.legacy(exported(apps))
    assert "POST /auth/v1/password" not in legacy["consumer"]


def test_ap00_a_declared_no_body_post_and_an_included_router_are_documented(apps):
    """Oracle: a body method that declares `x-infrx-no-body` (sign-out reads only headers) is
    not legacy; routes mounted through `app.include_router` are walked by the export."""
    legacy = export.legacy(exported(apps))
    assert "POST /auth/v1/sign-out" not in legacy["consumer"]
    paths = {f"{m} {r.path_format}" for r in export.routes(exported(apps)["consumer"]) for m in r.methods}
    assert "POST /console/v1/keys" in paths and "DELETE /console/v1/keys/{key_id}" in paths


def test_ap00_lab_control_documents_its_bodies_responses_and_refusals(apps):
    """Oracle (the worked example): the control family's operations carry their pydantic
    request and response schemas, the provider query parameter and the `{refusal}` answers -
    no generic `Request`, no FastAPI validation shape it never sends."""
    doc = export.document(apps["lab-control"], "lab-control")
    paths, schemas = doc["paths"], doc["components"]["schemas"]
    register = paths["/lab/v1/control/register"]["post"]
    body = register["requestBody"]["content"]["application/json"]["schema"]
    assert body == {"$ref": "#/components/schemas/Registration"}
    assert set(schemas["Registration"]["required"]) == {"name", "artifact_digest",
                                                        "schema_version", "runtime"}
    assert register["responses"]["201"]["content"]["application/json"]["schema"] == \
        {"$ref": "#/components/schemas/Deployment"}
    assert [p["name"] for p in register["parameters"]] == ["provider_org_id"]
    refused = register["responses"]["422"]["content"]["application/json"]["schema"]
    assert refused == {"$ref": "#/components/schemas/Refusal"}
    control = [op for path, item in paths.items() if path.startswith("/lab/v1/control/")
               for op in item.values()]
    # L3's eight + AP-06's four dev-key/dev-wallet operations (WR-AP06-3, LAB_PUBLICATION)
    assert len(control) == 12 and "HTTPValidationError" not in json.dumps(control)
    models = paths["/lab/v1/control/models"]["get"]["responses"]["200"]
    listed = models["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[1]
    assert schemas[listed]["properties"]["data"]["items"] == {"$ref": "#/components/schemas/Model"}
    reject = paths["/lab/v1/control/proposals/{proposal_id}/reject"]["post"]
    assert reject["requestBody"]["content"]["application/json"]["schema"] == \
        {"$ref": "#/components/schemas/Rejection"}
    assert not [e for e in export.legacy(exported(apps))["lab-control"] if "/lab/v1/control/" in e]
