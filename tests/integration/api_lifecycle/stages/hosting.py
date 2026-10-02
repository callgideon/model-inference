"""AP-11c: stages 04-07 by contracts.md §5/§6 - a private deployment and its readiness
(AP-05), the operator's private rate and dev funding, the bounded smoke and an endpoint-scoped
provider-dev key (AP-05/AP-06), one private finite-video call (AP-06), and the publication
proposal and its approval (AP-06). Their packages are not composed on this base, so in every
world today they are BLOCKED naming AP-05/AP-06 and never called; the code is the protocol the
runner will hold them to once mounted (proved on the layer-1 fake), and api-lifecycle-3 aligns
the bodies with the merged schemas.

Identities: `operator` is the operator's bootstrap credential (an operator-audience key from
the secrets file); `provider_dev` is the key minted in stage 05 for the private endpoint.
"""
from __future__ import annotations

import time

from infrx.contracts import api

from . import Blocked, accepted_operation
from .consumer import _json, upload, video
from .lab import ADMIN, _poll, _workspace

GPU = ("BLOCKED[GPU-TARGET] the deployment found no capacity (capacity_unavailable): an "
       "isolated GPU target and resource budget (verification.md prerequisite 3)")


def _operation(response) -> dict:
    doc = accepted_operation(response)
    return {"operation_id": doc.operation_id, "resource_id": doc.resource_id}


def _finished(ctx, operation_id: str) -> dict:
    return _json(_poll(ctx, "/lab/v1/operations/{id}", {"id": operation_id},
                       lambda d: d.get("state") in api.TERMINAL_STATES))


def s04(ctx) -> None:
    revision = ctx.outputs("03")["serving_version_id"]
    profiles = _json(ctx.call("GET", "/lab/v1/hosting-profiles", origin="lab", actor=ADMIN,
                              query=_workspace(ctx))).get("data") or []
    profile = next((p for p in profiles if p.get("eligible")), None)
    ctx.require("an eligible hosting profile is offered", profile is not None, len(profiles))
    made = ctx.mutate("04.deployment", "POST", "/lab/v1/control/deployments", origin="lab",
                      actor=ADMIN, query=_workspace(ctx), json={
                          "serving_version_id": revision, "hosting_profile_id":
                          profile["hosting_profile_id"], "environment": "dev",
                          "max_replicas": 1}, extract=_operation)
    final = _finished(ctx, made["operation_id"])
    if (final.get("error") or {}).get("code") == "capacity_unavailable":
        raise Blocked(GPU)
    ctx.require("the deployment operation succeeds", final.get("state") == "succeeded",
                {k: final.get(k) for k in ("state", "phase", "error")})
    ids = {"id": made["resource_id"]}
    detail = _json(ctx.call("GET", "/lab/v1/control/deployments/{id}", origin="lab", actor=ADMIN,
                            params=ids, query=_workspace(ctx)))
    ready = _json(ctx.call("GET", "/lab/v1/control/deployments/{id}/readiness", origin="lab",
                           actor=ADMIN, params=ids, query=_workspace(ctx)))
    ctx.check("readiness is the engine's report for exactly this serving revision",
              ready.get("state") == "ready" and ready.get("serving_version_id") == revision
              and (ready.get("engine") or {}).get("serving_version_id") == revision,
              {k: ready.get(k) for k in ("state", "serving_version_id")})
    ctx.check("the deployment observes the desired revision, never inferred from a record",
              detail.get("observed_serving_version_id") == revision
              and detail.get("desired_serving_version_id") == revision,
              {k: detail.get(k) for k in ("state", "observed_serving_version_id")})
    ctx.publish(deployment_id=made["resource_id"], endpoint_id=detail.get("endpoint_id"),
                private_model=detail.get("model"))


def s05(ctx) -> None:
    out = ctx.outputs("04")
    ids = {"id": out["deployment_id"]}
    rate = ctx.mutate("05.rate", "POST", "/operator/v1/deployments/{id}/dev-rate",
                      actor="operator", params=ids, json={"reason": "AP-11 private meter"},
                      extract=lambda r: {"status": r.status_code,
                                         "unit": (_json(r).get("rate") or {}).get("unit")})
    ctx.require("the operator approves a private CREDIT meter", rate["status"] in (200, 201)
                and rate["unit"] == "CREDIT", rate)
    funds = ctx.config.get("dev_funding") or {"amount": "100", "unit": "CREDIT"}
    granted = ctx.mutate("05.fund", "POST", "/operator/v1/dev-wallet-grants", actor="operator",
                         json={**_workspace(ctx), "amount": funds,
                               "reason": "AP-11 private smoke"},
                         extract=lambda r: {"status": r.status_code})
    ctx.require("the dev wallet is funded", granted["status"] in (200, 201), granted)
    wallet = _json(ctx.call("GET", "/lab/v1/control/dev-wallet", origin="lab", actor=ADMIN,
                            query=_workspace(ctx)))
    ctx.check("the dev wallet is private CREDIT, apart from PROVIDER_USD judge spend",
              (wallet.get("balance") or {}).get("unit") == "CREDIT", wallet.get("balance"))
    smoke = ctx.mutate("05.smoke", "POST", "/lab/v1/control/deployments/{id}/smoke",
                       origin="lab", actor=ADMIN, params=ids, query=_workspace(ctx),
                       extract=_operation)
    receipt = _finished(ctx, smoke["operation_id"])
    ctx.require("the bounded smoke succeeds with a receipt", receipt.get("state") == "succeeded",
                {k: receipt.get(k) for k in ("state", "error")})

    def extract(r) -> dict:
        body = _json(r)
        key = body.get("key") or {}
        if r.status_code == 201 and body.get("secret") and key.get("key_id"):
            ctx.keep(ctx.identity("provider_dev")["secret"], body["secret"], key["key_id"])
        return {"status": r.status_code, "key_id": key.get("key_id"),
                "audience": key.get("audience"), "endpoint_id": key.get("endpoint_id")}
    key = ctx.mutate("05.key", "POST", "/lab/v1/control/endpoints/{id}/keys", origin="lab",
                     actor=ADMIN, params={"id": out["endpoint_id"]}, query=_workspace(ctx),
                     json={"name": "ap11 provider dev"}, extract=extract)
    ctx.require("an endpoint-scoped provider-dev key is issued", key["status"] in (200, 201)
                and ctx.minted("provider_dev") == key["key_id"], key)
    ctx.check("the key's audience and scope are the private endpoint's",
              key["audience"] == "provider_dev" and key["endpoint_id"] == out["endpoint_id"], key)


def s06(ctx) -> None:
    out = ctx.outputs("04")
    model = out.get("private_model")
    ctx.require("the private endpoint names its model", model, None)
    listed = {row.get("id") for row in _json(ctx.call("GET", "/v1/models")).get("data") or []}
    ctx.check("the private model is not in public discovery", model not in listed, model)
    body = {"model": model, "messages": video(upload(ctx, "provider_dev", "06"))}
    answer = ctx.mutate("06.private", "POST", "/v1/chat/completions", actor="provider_dev",
                        json=body, inference=True, extract=lambda r: {
                            "status": r.status_code, "usage": bool(_json(r).get("usage")),
                            "model": _json(r).get("model"),
                            "request_id": r.headers.get("Inference-Id")})
    ctx.require("the private finite-video call is answered", answer["status"] == 200, answer)
    ctx.check("the private answer names its model and reports usage",
              answer["model"] == model and answer["usage"], answer)
    public = ctx.call("POST", "/v1/chat/completions", actor="provider_dev", inference=True,
                      json={"model": ctx.config["model"], "messages": body["messages"],
                            "max_tokens": 4})
    ctx.check("a dev key cannot spend at the public endpoint", public.status_code in (401, 403),
              public.status_code)


def s07(ctx) -> None:
    out = ctx.outputs("04")
    proposed = ctx.mutate("07.propose", "POST", "/lab/v1/control/proposals", origin="lab",
                          actor=ADMIN, query=_workspace(ctx), json={
                              "kind": "publish", "deployment_revision_id": out["deployment_id"]},
                          extract=lambda r: {"status": r.status_code,
                                             "proposal_id": _json(r).get("proposal_id")})
    ctx.require("the provider administrator proposes publication",
                proposed["status"] in (200, 201) and proposed["proposal_id"], proposed)
    found = _json(ctx.call("GET", "/operator/v1/publication-proposals/{id}", actor="operator",
                           params={"id": proposed["proposal_id"]}))
    version = found.get("listing_version")
    ctx.require("the operator reads the exact candidate and readiness",
                found.get("deployment_revision_id") == out["deployment_id"]
                and found.get("readiness"), {k: found.get(k) for k in ("state", "listing_version")})
    stale = ctx.call("POST", "/operator/v1/publication-proposals/{id}/approve", actor="operator",
                     params={"id": proposed["proposal_id"]},
                     json={"expected_listing_version": (version or 0) - 1, "reason": "AP-11"},
                     headers={"Idempotency-Key": ctx.key("07.propose") + ".stale"})
    ctx.check("an approval on a stale listing version is a 409", stale.status_code == 409,
              stale.status_code)
    approved = ctx.mutate("07.approve", "POST", "/operator/v1/publication-proposals/{id}/approve",
                          actor="operator", params={"id": proposed["proposal_id"]},
                          json={"expected_listing_version": version, "reason": "AP-11"},
                          extract=lambda r: {"status": r.status_code,
                                             "audit": _json(r).get("audit_id")})
    ctx.require("the operator approves through the API", approved["status"] == 200, approved)
    ctx.check("the decision is audited", bool(approved["audit"]), None)
    deadline = time.monotonic() + float(ctx.config.get("poll_timeout_s", 120))
    while time.monotonic() < deadline:                 # route/cache propagation is an outbox
        listed = {row.get("id") for row in _json(ctx.call("GET", "/v1/models")).get("data") or []}
        if out.get("private_model") in listed:
            break
        time.sleep(float(ctx.config.get("poll_interval_s", 0.5)))
    ctx.check("the approved listing appears in public discovery",
              out.get("private_model") in listed, None)
