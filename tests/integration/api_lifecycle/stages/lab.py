"""AP-11c: the provider-side stages - 02/03 model project, artifact intake and serving revision
(AP-04), 13 the Lab trace read (AP-07), 14/15 the judge and its accounting (AP-08), 16 human
review and calibration (AP-08), 17 revocation (AP-07/08). Each drives only FastAPI routes
through `ctx` (runner.Context) as the provider administrator's session, in its workspace
(`?provider_org_id=`, contracts.md §4 "verified provider_org_id context").

The judge runs as the config's `judge` says: `live` (P-10 configured) waits for a terminal run
and its results; `dry_run` proves the API's half - accepted, accounted, nothing sent or spent,
nothing ever `scored` - and the stage carries the label "dry-run", never a judge result. With
neither the stage is BLOCKED on P-10.
"""
from __future__ import annotations

import hashlib
import mimetypes
import time
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path

from infrx.contracts import api

from . import Blocked, accepted_operation
from .consumer import TEXT, _json

ADMIN = "provider_admin"
DRY_RUN = ("dry-run: the judge run was accepted and accounted through the API; nothing was "
           "sent to a judge, nothing spent, no result scored (JUDGE_MODE=dry_run; a live judge "
           "is P-10)")
NOT_SCORED = ("queued", "reserved", "completed", "cancelled")


def _workspace(ctx) -> dict:
    return {"provider_org_id": ctx.identity(ADMIN)["provider_org_id"]}


def _poll(ctx, route: str, params: dict, done, *, timeout_key: str = "poll_timeout_s",
          actor: str = ADMIN):
    """GET `route` until `done(json)` or the deadline; the last answer."""
    deadline = time.monotonic() + float(ctx.config.get(timeout_key, 120))
    while True:
        answer = ctx.call("GET", route, origin="lab", actor=actor, params=params,
                          query=_workspace(ctx))
        if done(_json(answer)) or time.monotonic() > deadline:
            return answer
        time.sleep(float(ctx.config.get("poll_interval_s", 0.5)))


def _money(doc) -> Decimal | None:
    try:
        return Decimal(str((doc or {}).get("amount")))
    except (InvalidOperation, TypeError):
        return None


# ------------------------------------------------------------------ 02/03 (AP-04)

def manifest(root: Path) -> list[dict]:
    """Every file under the artifact directory: path, bytes, SHA-256, media type."""
    return [{"relative_path": path.relative_to(root).as_posix(), "bytes": path.stat().st_size,
             "sha256": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
             "media_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream"}
            for path in sorted(p for p in root.rglob("*") if p.is_file())]


def s02(ctx) -> None:
    from infrx.lab.artifacts.manifest import Manifest
    where = (ctx.config.get("artifact") or {}).get("dir")
    if not where:
        raise Blocked("BLOCKED[config] no artifact directory to upload (config artifact.dir)")
    root, files = Path(where), manifest(Path(where))
    digest = Manifest.model_validate({"files": files}).digest
    slug = "ap11-" + hashlib.sha256(str(ctx.config["target"]).encode()).hexdigest()[:12]
    project = ctx.mutate("02.project", "POST", "/lab/v1/control/model-projects", origin="lab",
                         actor=ADMIN, query=_workspace(ctx),
                         json={"name": "AP-11 lifecycle test model", "slug": slug,
                               "description": "AP-11 runner's own test project"},
                         extract=lambda r: {"status": r.status_code,
                                            "project_id": _json(r).get("project_id")})
    ctx.require("the project is created in the workspace", project["status"] == 201
                and project["project_id"], project)
    ctx.own("model_project", project["project_id"], None)
    upload = ctx.mutate("02.upload", "POST", "/lab/v1/artifacts/uploads", origin="lab",
                        actor=ADMIN, query=_workspace(ctx),
                        json={"project_id": project["project_id"], "files": files},
                        extract=lambda r: {"status": r.status_code,
                                           "upload_id": _json(r).get("upload_id")})
    ctx.require("the upload session is issued", upload["status"] == 201 and upload["upload_id"],
                upload)
    ids = {"id": upload["upload_id"]}
    for entry in files:
        grant = ctx.call("POST", "/lab/v1/artifacts/uploads/{id}/parts", origin="lab",
                         actor=ADMIN, params=ids, query=_workspace(ctx),
                         json={"relative_path": entry["relative_path"]})
        ctx.served(grant, "POST /lab/v1/artifacts/uploads/{id}/parts", "AP-04")
        url = _json(grant).get("url")
        ctx.require(f"a scoped PUT is granted for {entry['relative_path']}",
                    grant.status_code == 200 and url, grant.status_code)
        put = ctx.call(_json(grant).get("method") or "PUT", "{part}", origin="lab",
                       params={"part": url}, content=(root / entry["relative_path"]).read_bytes())
        ctx.require(f"{entry['relative_path']}'s bytes are stored", put.status_code in (200, 204),
                    put.status_code)

    def operation(r) -> dict:
        doc = accepted_operation(r)
        return {"operation_id": doc.operation_id, "location": r.headers.get("Location")}
    done = ctx.mutate("02.complete", "POST", "/lab/v1/artifacts/uploads/{id}/complete",
                      origin="lab", actor=ADMIN, params=ids, query=_workspace(ctx),
                      json={"manifest_sha256": digest}, extract=operation)
    op = {"id": done["operation_id"]}
    final = _json(_poll(ctx, "/lab/v1/operations/{id}", op,
                        lambda d: d.get("state") in api.TERMINAL_STATES))
    ctx.require("the verification operation succeeds", final.get("state") == "succeeded",
                {k: final.get(k) for k in ("state", "phase", "error")})
    again = ctx.call("POST", "/lab/v1/artifacts/uploads/{id}/complete", origin="lab",
                     actor=ADMIN, params=ids, query=_workspace(ctx),
                     json={"manifest_sha256": digest},
                     headers={"Idempotency-Key": ctx.key("02.complete")})
    later = _json(ctx.call("GET", "/lab/v1/operations/{id}", origin="lab", actor=ADMIN,
                           params=op, query=_workspace(ctx)))
    ctx.check("the operation completes once: a replay is the same operation, unchanged",
              _json(again).get("operation_id") == done["operation_id"]
              and (later.get("state"), later.get("resource_id"))
              == (final.get("state"), final.get("resource_id")), later.get("state"))
    ctx.publish(project_id=project["project_id"], artifact_id=final.get("resource_id"),
                manifest=files, manifest_sha256=digest)


def s03(ctx) -> None:
    out = ctx.outputs("02")
    found = ctx.call("GET", "/lab/v1/artifacts/{id}", origin="lab", actor=ADMIN,
                     params={"id": out["artifact_id"]}, query=_workspace(ctx))
    artifact = _json(found)
    ctx.require("the verified artifact is read", found.status_code == 200, found.status_code)
    ctx.check("its manifest covers every uploaded file by hash",
              sorted((f.get("relative_path"), f.get("sha256")) for f in artifact.get("files") or [])
              == sorted((f["relative_path"], f["sha256"]) for f in out["manifest"])
              and artifact.get("manifest_sha256") == out["manifest_sha256"],
              len(artifact.get("files") or []))
    ctx.check("the artifact names its source and compatibility",
              artifact.get("source") in ("upload", "import", "adopted")
              and isinstance(artifact.get("compatibility"), dict), artifact.get("source"))
    made = ctx.mutate("03.revision", "POST", "/lab/v1/control/model-projects/{id}/revisions",
                      origin="lab", actor=ADMIN, params={"id": out["project_id"]},
                      query=_workspace(ctx), json={"artifact_id": out["artifact_id"]},
                      extract=lambda r: {"status": r.status_code, "revision": _json(r)})
    if made["status"] == 422:
        reasons = [e.get("code") or e.get("message") for e in
                   ((made["revision"].get("error") or {}).get("field_errors") or [])]
        raise Blocked("BLOCKED[artifact] the uploaded artifact is not a supported serving "
                      f"profile ({', '.join(map(str, reasons)) or 'unsupported'}): a supported "
                      "revision needs the measured Marlin bytes (a live target)")
    revision = made["revision"]
    ctx.require("the serving revision is created", made["status"] == 201, made["status"])
    ctx.check("the revision pins its artifact, model revision and profile",
              revision.get("artifact_id") == out["artifact_id"]
              and revision.get("serving_version_id") and revision.get("model_revision")
              and revision.get("profile"), {k: revision.get(k) for k in (
                  "serving_version_id", "model_revision", "revision_label")})
    again = _json(ctx.call("POST", "/lab/v1/control/model-projects/{id}/revisions",
                           origin="lab", actor=ADMIN, params={"id": out["project_id"]},
                           query=_workspace(ctx), json={"artifact_id": out["artifact_id"]},
                           headers={"Idempotency-Key": ctx.key("03.revision")}))
    ctx.check("the revision is immutable: a replay is the same serving version",
              again.get("serving_version_id") == revision.get("serving_version_id"),
              again.get("serving_version_id"))
    ctx.version("serving_revision", {k: revision.get(k) for k in (
        "serving_version_id", "model_revision", "revision_label", "public_model_id")})
    ctx.publish(serving_version_id=revision.get("serving_version_id"))


# ------------------------------------------------------------------ 13 (AP-07)

def trace(ctx, request_id: str, actor: str = ADMIN):
    return ctx.call("GET", "/lab/v1/traces/{id}", origin="lab", actor=actor,
                    params={"id": request_id}, query=_workspace(ctx))


def s13(ctx) -> None:
    rid = ctx.outputs("12")["captured_request_id"]
    deadline = time.monotonic() + float(ctx.config.get("trace_timeout_s", 90))
    while True:
        listed = _json(ctx.call("GET", "/lab/v1/traces", origin="lab", actor=ADMIN,
                                query={**_workspace(ctx), "limit": 50})).get("data") or []
        row = next((r for r in listed if r.get("request_id") == rid), None)
        if row is not None or time.monotonic() > deadline:
            break
        time.sleep(float(ctx.config.get("poll_interval_s", 0.5)) * 4)
    ctx.require("the captured request is listed in the Lab", row is not None, len(listed))
    detail = _json(trace(ctx, rid))
    ctx.check("same request id and pins as the call",
              detail.get("request_id") == rid and detail.get("serving_version_id")
              and detail.get("price_version") and detail.get("model_id")
              == ctx.config["model_uuid"], {k: detail.get(k) for k in (
                  "serving_version_id", "model_revision", "price_version", "model_id")})
    ctx.check("the authorized content is the request's real capture",
              detail.get("access") == "content" and detail.get("access_state") == "content"
              and TEXT[0]["content"] in str(detail.get("content") or ""),
              detail.get("access_state"))
    ctx.check("timing is measured or labelled unavailable, never zero",
              detail.get("elapsed_ms") is None or detail["elapsed_ms"] > 0, detail.get("elapsed_ms"))
    try:
        earlier = ctx.outputs("11").get("sync_request_id")
    except Blocked:
        earlier = None
    if earlier:
        before = trace(ctx, earlier)
        ctx.check("the uncaptured earlier request has no content in the Lab",
                  before.status_code == 404 or _json(before).get("access_state") == "not_captured",
                  before.status_code)
    outsider = trace(ctx, rid, actor="outsider")
    ctx.check("an outsider cannot read the trace", outsider.status_code in (403, 404),
              outsider.status_code)
    ctx.version("trace", {k: detail.get(k) for k in ("mode", "serving_version_id",
                                                     "rate_card_version", "price_version")})
    ctx.publish(request_id=rid)


# ------------------------------------------------------------------ 14-17 (AP-08)

def payer(ctx) -> str:
    """This provider's own payer reference for the run (0037: `lab:payer:<provider>:...`)."""
    provider = _workspace(ctx)["provider_org_id"]
    seed = f"ap11:{ctx.config['target']}"
    return (f"lab:payer:{provider}:{uuid.uuid5(uuid.NAMESPACE_URL, seed)}"
            f"@sha256:{hashlib.sha256(seed.encode()).hexdigest()}")


def s14(ctx) -> None:
    mode = ctx.config.get("judge")
    if mode not in ("dry_run", "live"):
        raise Blocked("BLOCKED[P-10] an approved media-capable judge, its secret reference, "
                      "payer and spend limit (config `judge`: live), or an isolated dry run")
    if mode == "dry_run":
        ctx.label(DRY_RUN)
    q12, rid = ctx.outputs("12"), ctx.outputs("13")["request_id"]
    if ctx.composed("AP-02"):
        ctx.publish(credits_before=_json(ctx.call("GET", "/console/v1/credits",
                                                  actor="consumer_a_web")))
    models = _json(ctx.call("GET", "/lab/v1/judge/models", origin="lab", actor=ADMIN,
                            query=_workspace(ctx)))
    offered = (models.get("availability") or {}).get("state")
    ctx.check("the judge models are truthful: none listed means unavailable",
              (offered == "configured") == bool(models.get("data")), offered)
    rubrics = _json(ctx.call("GET", "/lab/v1/judge/rubrics", origin="lab", actor=ADMIN,
                             query=_workspace(ctx))).get("data") or []
    rubric = max(rubrics, key=lambda r: r.get("version") or 0, default={})
    ctx.require("a reviewed rubric version with criteria and an output schema",
                rubric.get("criteria") and rubric.get("output_schema"), len(rubrics))
    body = {"grantor_org_id": q12["grantor_org_id"], "model_id": ctx.config["model_uuid"],
            "judge_model": ctx.config.get("judge_model", "ap11-judge"),
            "rubric_version": rubric["version"], "sample_size": 1}
    config = ctx.mutate("14.config", "POST", "/lab/v1/judge/configs", origin="lab", actor=ADMIN,
                        query=_workspace(ctx), json=body, extract=lambda r: {
                            "status": r.status_code, "config_id": _json(r).get("config_id")})
    ctx.require("the judge configuration is created", config["status"] in (200, 201)
                and config["config_id"], config)
    estimate = _json(ctx.call("POST", "/lab/v1/judge/estimates", origin="lab", actor=ADMIN,
                              query=_workspace(ctx), json={"config_id": config["config_id"]}))
    ctx.check("the estimate is a report, never an authority to spend",
              estimate.get("authorizes_spend") is False
              and estimate.get("samples_max") == body["sample_size"], estimate.get("priced"))
    ref = payer(ctx)
    limit = ctx.config.get("judge_budget") or {"amount": "1.00", "unit": "PROVIDER_USD"}
    budget = ctx.mutate("14.budget", "PUT", "/lab/v1/judge/budgets/{payer}", origin="lab",
                        actor=ADMIN, params={"payer": ref}, query=_workspace(ctx),
                        json={"limit": limit}, extract=lambda r: {
                            "status": r.status_code, "limit": _json(r).get("limit")})
    ctx.require("the PROVIDER_USD budget is set", budget["status"] == 200
                and (budget["limit"] or {}).get("unit") == "PROVIDER_USD", budget)

    def accepted(r) -> dict:
        doc = accepted_operation(r)
        return {"run_id": doc.resource_id or doc.operation_id, "kind": doc.kind}
    run = ctx.mutate("14.run", "POST", "/lab/v1/judge/runs", origin="lab", actor=ADMIN,
                     query=_workspace(ctx), json={"config_id": config["config_id"],
                                                  "payer_ref": ref}, extract=accepted)
    replay = ctx.call("POST", "/lab/v1/judge/runs", origin="lab", actor=ADMIN,
                      query=_workspace(ctx), json={"config_id": config["config_id"],
                                                   "payer_ref": ref},
                      headers={"Idempotency-Key": ctx.key("14.run")})
    ctx.check("a replayed run request is the same run", replay.status_code == 202
              and (_json(replay).get("resource_id") or _json(replay).get("operation_id"))
              == run["run_id"], replay.status_code)
    ids = {"id": run["run_id"]}
    if mode == "live":
        doc = _json(_poll(ctx, "/lab/v1/judge/runs/{id}", ids, lambda d: (
            (d.get("operation") or {}).get("state") in api.TERMINAL_STATES),
            timeout_key="judge_timeout_s"))
        ctx.require("the live run finishes", (doc.get("operation") or {}).get("state")
                    == "succeeded", doc.get("domain_state"))
    else:
        doc = _json(ctx.call("GET", "/lab/v1/judge/runs/{id}", origin="lab", actor=ADMIN,
                             params=ids, query=_workspace(ctx)))
        ctx.check("dry run: the run is durable and sends nothing, spends nothing",
                  doc.get("domain_state") in NOT_SCORED and doc.get("sent") == 0
                  and not _money(doc.get("settled")), {k: doc.get(k) for k in (
                      "domain_state", "sent", "settled")})
    results = _json(ctx.call("GET", "/lab/v1/judge/runs/{id}/results", origin="lab",
                             actor=ADMIN, params=ids, query=_workspace(ctx))).get("data")
    if mode == "live":
        ctx.check("a real judge result or an honest abstention per sample", results and all(
            r.get("state") in ("scored", "quarantined") for r in results), len(results or []))
    else:
        ctx.check("dry run: no result is ever scored",
                  results is not None and not [r for r in results if r.get("state") == "scored"],
                  len(results or []))
    ctx.version("judge", {"mode": mode, "judge_model": body["judge_model"],
                          "rubric_version": body["rubric_version"]})
    ctx.publish(config_id=config["config_id"], run_id=run["run_id"], payer_ref=ref,
                rubric_version=body["rubric_version"], request_id=rid)


def s15(ctx) -> None:
    out = ctx.outputs("14")
    if ctx.config.get("judge") == "dry_run":
        ctx.label(DRY_RUN)
    if out.get("credits_before") is not None:
        after = _json(ctx.call("GET", "/console/v1/credits", actor="consumer_a_web"))
        before = out["credits_before"]
        ctx.check("the consumer is not charged for the provider's judging",
                  all(_money(after.get(k)) == _money(before.get(k)) and _money(after.get(k))
                      is not None for k in ("available", "ledger_total", "reserved_total")),
                  {k: (after.get(k) or {}).get("amount") for k in ("available", "ledger_total")})

    def held() -> dict:
        rows = _json(ctx.call("GET", "/lab/v1/judge/budgets", origin="lab", actor=ADMIN,
                              query=_workspace(ctx))).get("data") or []
        return next((r for r in rows if r.get("payer_ref") == out["payer_ref"]), {})
    first = held()
    ctx.require("the payer's budget is read", first, None)
    ctx.check("spend is PROVIDER_USD, reserved and settled kept apart",
              all((first.get(k) or {}).get("unit") == "PROVIDER_USD"
                  for k in ("limit", "reserved", "settled")), None)
    if ctx.config.get("judge") == "dry_run":
        ctx.check("dry run: nothing reserved, nothing settled",
                  _money(first.get("reserved")) == 0 and _money(first.get("settled")) == 0,
                  {k: (first.get(k) or {}).get("amount") for k in ("reserved", "settled")})
    ctx.call("POST", "/lab/v1/judge/runs", origin="lab", actor=ADMIN, query=_workspace(ctx),
             json={"config_id": out["config_id"], "payer_ref": out["payer_ref"]},
             headers={"Idempotency-Key": ctx.key("14.run")})
    second = held()
    ctx.check("a replayed run never duplicates spend",
              _money(second.get("reserved")) == _money(first.get("reserved"))
              and _money(second.get("settled")) == _money(first.get("settled")), None)


def s16(ctx) -> None:
    out = ctx.outputs("14")
    rid, ids = out["request_id"], {"id": out["request_id"]}
    body = {"verdict": "pass", "comment": "AP-11 lifecycle review", "run_id": out["run_id"],
            "rubric_version": out["rubric_version"]}
    review = ctx.mutate("16.review", "POST", "/lab/v1/traces/{id}/reviews", origin="lab",
                        actor=ADMIN, params=ids, query=_workspace(ctx), json=body,
                        extract=lambda r: {"status": r.status_code, **{
                            k: _json(r).get(k) for k in ("review_id", "provenance", "verdict")}})
    ctx.require("the human review is stored", review["status"] in (200, 201)
                and review["provenance"] == "human", review)
    again = _json(ctx.call("POST", "/lab/v1/traces/{id}/reviews", origin="lab", actor=ADMIN,
                           params=ids, query=_workspace(ctx), json=body,
                           headers={"Idempotency-Key": ctx.key("16.review")}))
    ctx.check("the review is immutable: a replay is the same review",
              again.get("review_id") == review["review_id"], again.get("review_id"))
    feedback = _json(ctx.call("GET", "/lab/v1/traces/{id}/feedback", origin="lab", actor=ADMIN,
                              params=ids, query=_workspace(ctx)))
    mine = [r for r in feedback.get("reviews") or [] if r.get("review_id") == review["review_id"]]
    ctx.check("the review is read back once, provenance human, apart from customer signals",
              len(mine) == 1 and mine[0].get("provenance") == "human"
              and mine[0].get("request_id") == rid and isinstance(feedback.get("signals"), list),
              len(mine))
    calibration = _json(ctx.call("GET", "/lab/v1/judge/calibration", origin="lab", actor=ADMIN,
                                 query={**_workspace(ctx), "config_id": out["config_id"]}))
    ctx.check("an inadequate reference sample is never calibrated",
              calibration.get("state") in ("insufficient", "uncalibrated")
              and (calibration.get("labels") or 0) < (calibration.get("required") or 1),
              {k: calibration.get(k) for k in ("state", "labels", "required")})


def s17(ctx) -> None:
    grant, out = ctx.outputs("12")["grant_id"], ctx.outputs("14")
    revoked = ctx.mutate("17.revoke", "DELETE", "/console/v1/data-grants/{id}",
                         actor="consumer_a_web", params={"id": grant},
                         extract=lambda r: {"status": r.status_code,
                                            "state": _json(r).get("state")})
    ctx.require("the grant is revoked through the API", revoked["status"] == 200
                and revoked["state"] == "revoked", revoked)
    after = _json(trace(ctx, out["request_id"]))
    ctx.check("the trace's content is no longer readable; its metadata follows its own policy",
              after.get("request_id") == out["request_id"] and "content" not in after
              and after.get("access") == "metadata" and after.get("access_state") == "revoked",
              {k: after.get(k) for k in ("access", "access_state")})
    follow = ctx.call("POST", "/lab/v1/judge/runs", origin="lab", actor=ADMIN,
                      query=_workspace(ctx), json={"config_id": out["config_id"],
                                                   "payer_ref": out["payer_ref"]},
                      headers={"Idempotency-Key": f"ap11-17-{uuid.uuid4()}"})
    ctx.check("a follow-up judge run is refused without a current grant",
              follow.status_code in (403, 409, 422), follow.status_code)
