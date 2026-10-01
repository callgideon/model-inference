"""AP-08 08a/08e over HTTP: `/lab/v1/judge/*` and `/lab/v1/traces/{id}/feedback|reviews`
mounted on a bare `FastAPI()` by their own `register(app, rt)` (rule 3: the coordinator mounts
them), the actor from `control.StaticActors`, the doors a fake that keeps 0037/0064's
idempotency rule in memory. The PostgreSQL half is `test_judge_routes_pg.py`.
"""
from __future__ import annotations

import types
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from infrx.contracts import api, errors
from infrx.gateway import control
from infrx.gateway.routes import lab_judge, lab_reviews
from infrx.lab.judge_api.service import JudgeApi

NEMO = "b0000001-0000-4000-8000-000000000001"
DEV = "c1000000-0000-4000-8000-000000000001"
OTHER_DEV = "c1000000-0000-4000-8000-000000000002"
CONFIG = "0c000000-0000-4000-8000-0000000000c1"
JOB = "1b000000-0000-4000-8000-000000000001"
PAYER = f"lab:payer:{NEMO}:0000003c-0000-4000-8000-00000000003c@sha256:{'a' * 64}"
SESSION = api.Actor(audience="session", user_id=DEV)
Q = {"provider_org_id": NEMO}


def run_row(run_id: str, config: str, payer: str, **over) -> dict:
    return {"run_id": run_id, "config_id": config, "payer_ref": payer,
            "requested_at": "2026-10-01T00:00:00+00:00", "grantor_org_id": NEMO,
            "model_id": NEMO, "judge_model": "judge-1", "rubric_version": 1, "sample_size": 20,
            "ledger_state": None, "price_version": None, "reserved": None, "actual": None,
            "selected": 0, "sent": 0, "media": 0, "accepted": 0, "rejected": 0,
            "updated_at": "2026-10-01T00:00:00+00:00", "cancel_requested_at": None, **over}


class FakeDoors:
    """0037's request_run (idempotent on the run id: the STORED request answers) and the
    0064 reads, in memory; `results`/`calibration` are what a test seeds."""

    def __init__(self) -> None:
        self.runs: dict[str, dict] = {}
        self.calls: list[tuple] = []
        self.results: list[dict] = []
        self.calibration = {"state": "uncalibrated", "labels": 0, "required": 30,
                            "agreement": None, "interval": None}
        self.refuse: errors.DomainError | None = None

    async def call(self, user, door, *args):
        self.calls.append((user, door, *args))
        if self.refuse is not None:
            raise self.refuse
        if door == "lab_judge_request_run":
            _, run, config, payer = args
            self.runs.setdefault(run, run_row(run, config, payer))
            return self.runs[run]
        if door == "lab_judge_run_list":
            _, run, after, limit = args
            rows = [r for k, r in sorted(self.runs.items())
                    if (run is None or k == run) and (after is None or k > after)]
            return rows[:limit]
        if door == "lab_judge_cancel":
            self.runs[args[1]]["cancel_requested_at"] = "2026-10-01T00:01:00+00:00"
            return self.runs[args[1]]
        if door == "lab_judge_run_results":
            return self.results[:args[3]]
        if door == "lab_judge_config_list":
            return [{"config_id": CONFIG, "grantor_org_id": NEMO, "model_id": NEMO,
                     "judge_model": "judge-1", "rubric_version": 1, "sample_size": 20,
                     "created_at": "2026-10-01T00:00:00+00:00",
                     "calibration": self.calibration}]
        if door == "lab_trace_review":
            a = args[0]          # 0064's answer: the stored row's keys, never the hash
            return {"review_id": a["review_id"], "request_id": a["request_id"],
                    "verdict": a["verdict"], "comment": a.get("comment"),
                    "run_id": a.get("run_id"), "rubric_version": a.get("rubric_version"),
                    "reviewer": user, "provenance": "human",
                    "created_at": "2026-10-01T00:00:00+00:00", "replayed": False}
        if door in ("lab_review_feedback", "lab_trace_reviews"):
            return []
        raise AssertionError(f"unexpected door {door}")


def client(doors: FakeDoors | None = None, actors=None, mounted: bool = True) -> TestClient:
    doors = doors or FakeDoors()
    rt = types.SimpleNamespace(actors=actors or control.StaticActors(SESSION),
                               lab_judge=JudgeApi(doors) if mounted else None)
    app = FastAPI()
    lab_judge.register(app, rt)
    lab_reviews.register(app, rt)
    return TestClient(app, raise_server_exceptions=False)


def post_run(c: TestClient, key: str = "k-1", **body):
    return c.post(f"{lab_judge.PREFIX}/runs", params=Q, headers={"Idempotency-Key": key},
                  json={"config_id": CONFIG, "payer_ref": PAYER, **body})


def test_ap08_routes__run_identity_comes_from_the_idempotency_key():
    """A replay of the key is the same run (202 + Location, no second request); another key
    is another run; the same key with another body is 409; no key is 422 R270.
    Failure oracle: a browser-minted or random run id, a replay that queues a second run."""
    doors = FakeDoors()
    c = client(doors)
    first, again = post_run(c), post_run(c)
    assert first.status_code == again.status_code == 202, first.text
    run_id = first.json()["operation_id"]
    assert again.json()["operation_id"] == run_id and len(doors.runs) == 1
    assert first.headers["location"] == f"{lab_judge.PREFIX}/runs/{run_id}?provider_org_id={NEMO}"
    assert first.headers["cache-control"] == "no-store"
    assert first.json()["state"] == "queued" and first.json()["kind"] == "judge.run"
    assert post_run(c, "k-2").json()["operation_id"] != run_id
    conflict = post_run(c, payer_ref=PAYER.replace("aaaa", "bbbb", 1))
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"
    missing = c.post(f"{lab_judge.PREFIX}/runs", params=Q,
                     json={"config_id": CONFIG, "payer_ref": PAYER})
    assert missing.status_code == 422
    assert [f["field"] for f in missing.json()["error"]["field_errors"]] == ["Idempotency-Key"]
    other = client(doors, control.StaticActors(api.Actor(audience="session", user_id=OTHER_DEV)))
    assert post_run(other).json()["operation_id"] != run_id, "a key is scoped to its actor"


def test_ap08_routes__only_a_verified_session_acts_and_identity_is_never_read_from_the_body():
    """No session is 401, a key audience is 403, a forged actor field is 422.
    Failure oracle: a consumer key judging, a body naming the requester."""
    assert post_run(client(actors=control.StaticActors())).status_code == 401
    key_actor = control.StaticActors(api.Actor(audience="consumer", user_id=DEV))
    refused = post_run(client(actors=key_actor))
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "forbidden"
    forged = post_run(client(), requested_by=OTHER_DEV)
    assert forged.status_code == 422, forged.text
    assert forged.json()["error"]["request_id"]


def test_ap08_routes__a_cancel_is_recorded_and_the_run_reports_cancel_requested():
    doors = FakeDoors()
    c = client(doors)
    run_id = post_run(c).json()["operation_id"]
    doors.runs[run_id]["ledger_state"] = "submitted"
    cancelled = c.post(f"{lab_judge.PREFIX}/runs/{run_id}/cancel", params=Q,
                       headers={"Idempotency-Key": "c-1"})
    assert cancelled.status_code == 200, cancelled.text
    body = cancelled.json()
    assert (body["domain_state"], body["cancel_requested"]) == ("submitted", True)
    assert body["operation"]["state"] == "cancel_requested"
    doors.runs[run_id]["ledger_state"] = None
    queued = c.get(f"{lab_judge.PREFIX}/runs/{run_id}", params=Q).json()
    assert (queued["domain_state"], queued["operation"]["state"]) == ("cancelled", "cancelled")


def test_ap08_routes__lists_page_with_an_opaque_cursor():
    doors = FakeDoors()
    c = client(doors)
    for i in range(3):
        post_run(c, f"k-{i}")
    first = c.get(f"{lab_judge.PREFIX}/runs", params={**Q, "limit": 2}).json()
    assert len(first["data"]) == 2 and first["next_cursor"]
    rest = c.get(f"{lab_judge.PREFIX}/runs",
                 params={**Q, "limit": 2, "cursor": first["next_cursor"]}).json()
    assert len(rest["data"]) == 1 and rest["next_cursor"] is None
    ids = [r["run_id"] for r in first["data"] + rest["data"]]
    assert ids == sorted(ids) and len(set(ids)) == 3
    bad = c.get(f"{lab_judge.PREFIX}/runs", params={**Q, "cursor": "not-ours"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_cursor"
    assert c.get(f"{lab_judge.PREFIX}/runs", params={**Q, "limit": 101}).status_code == 422


def test_ap08_routes__the_estimate_is_a_report_and_no_unpriced_model_is_offered():
    """P-10: the approved rate table is empty - the models read says so, an estimate is
    unpriced and never authorizes spend. Failure oracle: an unpriced estimate shown as a
    reservation, an empty table shown as a configured judge."""
    c = client()
    models = c.get(f"{lab_judge.PREFIX}/models", params=Q).json()
    assert models["data"] == [] and models["availability"]["state"] == "unavailable"
    estimate = c.post(f"{lab_judge.PREFIX}/estimates", params=Q, json={"config_id": CONFIG})
    assert estimate.status_code == 200, estimate.text
    e = estimate.json()
    assert (e["priced"], e["authorizes_spend"], e["worst_case"], e["samples_max"]) == \
        (False, False, None, 20)
    rubrics = c.get(f"{lab_judge.PREFIX}/rubrics", params=Q).json()
    assert [r["version"] for r in rubrics["data"]] == [1]


def test_ap08_routes__a_configuration_names_a_graded_rubric_and_calibration_is_honest():
    doors = FakeDoors()
    c = client(doors)
    bad = c.post(f"{lab_judge.PREFIX}/configs", params=Q, headers={"Idempotency-Key": "c"},
                 json={"grantor_org_id": NEMO, "model_id": NEMO, "judge_model": "judge-1",
                       "rubric_version": 7, "sample_size": 5})
    assert bad.status_code == 422 and not any(d == "lab_judge_configure_keyed"
                                              for _, d, *_ in doors.calls)
    doors.calibration = {"state": "calibrated", "labels": 12, "required": 30,
                         "agreement": 0.9, "interval": [0.8, 0.95]}
    shown = c.get(f"{lab_judge.PREFIX}/calibration", params={**Q, "config_id": CONFIG}).json()
    assert (shown["state"], shown["labels"]) == ("insufficient", 12)


def test_ap08_routes__a_refused_door_is_an_r270_envelope_and_a_bug_hides_its_message():
    doors = FakeDoors()
    doors.refuse = errors.Forbidden("the session's provider role does not hold this action")
    refused = client(doors).get(f"{lab_judge.PREFIX}/runs", params=Q)
    assert refused.status_code == 403 and refused.headers["cache-control"] == "no-store"
    assert set(refused.json()["error"]) >= {"code", "message", "request_id", "retryable"}
    doors.refuse = RuntimeError("dsn=postgres://secret")
    bug = client(doors).get(f"{lab_judge.PREFIX}/runs", params=Q)
    assert bug.status_code == 500 and "secret" not in bug.text
    missing = client(FakeDoors()).get(f"{lab_judge.PREFIX}/runs/not-a-uuid", params=Q)
    assert missing.status_code == 404


def test_ap08_routes__reviews_are_human_and_keyed_and_nothing_mounts_when_off():
    doors = FakeDoors()
    c = client(doors)
    path = f"/lab/v1/traces/{JOB}/reviews"
    first = c.post(path, params=Q, headers={"Idempotency-Key": "r-1"},
                   json={"verdict": "fail", "comment": "missed step 3"})
    assert first.status_code == 201, first.text
    assert (first.json()["provenance"], first.json()["reviewer"]) == ("human", DEV)
    (_, _, sent), = [(u, d, *a) for u, d, *a in doors.calls if d == "lab_trace_review"]
    assert sent["review_id"] == str(uuid.UUID(sent["review_id"]))
    assert sent["input_hash"].startswith("sha256:") and "reviewer" not in sent
    again = c.post(path, params=Q, headers={"Idempotency-Key": "r-1"},
                   json={"verdict": "fail", "comment": "missed step 3"})
    assert again.json()["review_id"] == first.json()["review_id"]
    forged = c.post(path, params=Q, headers={"Idempotency-Key": "r-2"},
                    json={"verdict": "pass", "provenance": "teacher"})
    assert forged.status_code == 422
    feedback = c.get(f"/lab/v1/traces/{JOB}/feedback", params=Q)
    assert feedback.status_code == 200 and feedback.json() == {"signals": [], "reviews": []}
    off = client(mounted=False)
    assert off.get(f"{lab_judge.PREFIX}/runs", params=Q).status_code == 404
    assert off.get(f"/lab/v1/traces/{JOB}/feedback", params=Q).status_code == 404


@pytest.mark.parametrize("path", ["/models", "/rubrics", "/runs", "/configs", "/budgets"])
def test_ap08_routes__every_read_takes_the_session(path):
    assert client(actors=control.StaticActors()).get(
        f"{lab_judge.PREFIX}{path}", params=Q).status_code == 401
