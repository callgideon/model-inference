"""B1's fake world: D7's evaluation half in memory, a provider_dev dev endpoint that debits
a CREDIT wallet fixture, and the records of one run. `test_runner_pg.py` runs the drills on
the real `PgLabDataStore` (0029) and this endpoint over HTTP on the b1 model-fake port.

`FakeEvalStore` keeps 0029's rules the runner relies on (`lab_create_run` … `lab_recover`):
scheduling refuses a non-current grant; a run's cases are the first `max_cases` sample ids
(R168); a lease is one attempt (the case's attempt count + 1); every write is fenced (live
run, live attempt, same worker, unexpired on the store clock); a finish replays by digest;
one result per (run, case, evaluator); the run succeeds when no case is pending or leased;
`recover` expires leases and puts their cases back. `release` is WR-B-2(d)'s requested
`lab_release_attempt` (not in 0029 yet): a fenced 402 attempt is removed and its case is
pending with the attempt uncounted, so the next lease is the same attempt and key.

`DevWallet` is the provider_dev endpoint (L3 + D5 stand-in): a wallet that starts at 0 and
is funded by the test (an operator allocation), a debit of `rate_card.debit(usage)` per
admitted request, 402 when the wallet cannot cover the request's hold, and a replay (no
second debit) for a repeated Idempotency-Key.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from collections import Counter
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from infrx.contracts import errors
from infrx.contracts.lab import records
from datetime import timedelta

from infrx.contracts.fakes.support import DEFAULT_START
from infrx.contracts.v2 import fixtures as v2fixtures
from infrx.contracts.v2 import records as v2
from infrx.contracts.v2.money_units import Credit
from infrx.evaluation.runner import Completion, evaluator_ref
from infrx.lab.access import LabAccess
from infrx.lab.access.fakes import FakeAccessStore

from ...n.imports.world import NEMO, OTHER, FakeLabStore, grant_ref  # noqa: F401

RATE_CARD = v2fixtures.model("rate_card_marlin.json")          # 400 / 1200 CREDIT per 1M
DEPLOYMENT = v2fixtures.model("deployment_revision_private_dev.json")
EVALUATOR_ID = "e0000001-0000-4000-8000-000000000001"
SERVING = (f"lab:serving:{NEMO}:{DEPLOYMENT.deployment_revision_id}@sha256:" + "5" * 64)
SPEC = {"metric": "exact_match", "reference": "sample.original.answer",
        "max_requests": 4, "max_bytes": 100_000, "max_seconds": 60}
EVALUATOR = evaluator_ref(SPEC, provider_org_id=NEMO, evaluator_id=EVALUATOR_ID)


DEV, VIEWER, OUTSIDER = (f"d0000b1{n}-0000-4000-8000-000000000001" for n in (1, 2, 3))


def access() -> LabAccess:
    """The L2 port (WR-B-4): DEV develops for NEMO, VIEWER views NEMO, OUTSIDER develops for
    OTHER only. The clock is the store's."""
    store = FakeAccessStore(now=DEFAULT_START)
    for provider, user, role in ((NEMO, DEV, v2.ProviderRole.developer),
                                 (NEMO, VIEWER, v2.ProviderRole.viewer),
                                 (OTHER, OUTSIDER, v2.ProviderRole.developer)):
        store.memberships[(provider, user)] = v2.ProviderMembership(
            provider_org_id=provider, user_id=user, role=role, granted_by="ops",
            granted_at=DEFAULT_START - timedelta(days=1))
    return LabAccess(store)


def uid(n: int, tag: int = 0xb1) -> str:
    return f"{tag:08x}-0000-4000-8000-{n:012x}"


def digest_of(n: int) -> str:
    return f"sha256:{hashlib.sha256(str(n).encode()).hexdigest()}"


def manifest(n: int, grant: str, source: str, *, dataset: int = 1) -> dict:
    """`n` text samples; the last two are the holdout."""
    ids = [uid(i, 0x5a) for i in range(1, n + 1)]
    return {"schema": "lab.dataset_manifest.1", "provider_org_id": NEMO,
            "dataset_id": uid(dataset, 0xda), "version": 1, "created_at": "2026-09-27T10:00:00Z",
            "derivation": "import", "parent_refs": [],
            "samples": [{"sample_id": s, "modality": "text", "source_ref": source,
                         "grant_ref": grant, "content_digest": digest_of(i),
                         "group_key": f"g{i}"} for i, s in enumerate(ids, 1)],
            "splits": {"train": ids[:-2], "validation": [], "holdout": ids[-2:]}}


def content(i: int) -> bytes:
    """Sample i's content object (N1's shape): the question is `q<i>`, the answer `a<i>`."""
    return records.canonical({"modality": "text", "content": f"q{i}",
                              "original": {"q": f"q{i}", "answer": f"a{i}"},
                              "annotation": {"method": "imported", "method_version": "1"}})


def harness(**over) -> dict:
    return {"schema": "lab.harness_revision.1", "provider_org_id": NEMO,
            "harness_id": uid(1, 0xa7), "version": 1, "created_at": "2026-09-27T10:00:00Z",
            "adapter": "text", "prompt_template": "answer {{q}}", "processor_profile": "p1",
            "input_mapping": {"q": "sample.content"}, "tools": [], **over}


def eval_run(dataset_ref: str, harness_ref: str, *, run: int = 1, max_cases: int = 100,
             limit: str = "100.00000000", evaluator: str = EVALUATOR, budgets=None) -> dict:
    run_id = uid(run, 0x7e)
    return {"schema": "lab.eval_run.1", "provider_org_id": NEMO, "run_id": run_id,
            "created_at": "2026-09-27T10:00:00Z", "dataset_ref": dataset_ref,
            "harness_ref": harness_ref, "serving_ref": SERVING, "evaluator_ref": evaluator,
            "seed": 7, "environment": "dev", "max_cases": max_cases, "state": "queued",
            "idempotency_key": records.run_key(run_id),
            "budgets": [{"limit": {"unit": "CREDIT", "value": limit},
                         "reserved": {"unit": "CREDIT", "value": "0.00000000"}}]
            if budgets is None else budgets}


# --------------------------------------------------------------------- the D7 fake
class FakeEvalStore(FakeLabStore):
    def __init__(self) -> None:
        super().__init__()
        self.now = 0.0
        self.runs: dict[str, dict] = {}
        self.cases: dict[tuple[str, str], dict] = {}
        self.attempts: dict[tuple[str, str, int], dict] = {}
        self.results: dict[tuple[str, str, str], dict] = {}

    async def publish(self, payload, *, provider_org_id, actor) -> str:
        """N's fake publishes manifests; a harness or run record is only content-addressed."""
        if payload.get("schema") == "lab.dataset_manifest.1":
            return await super().publish(payload, provider_org_id=provider_org_id, actor=actor)
        if records.parse(payload).provider_org_id != provider_org_id:
            raise errors.Forbidden("a provider publishes only its own records")
        return self.catalog.publish(payload)

    def _run(self, run_id, provider) -> dict:
        run = self.runs.get(run_id)
        if run is None or run["provider"] != provider:
            raise errors.NotFound("no such run for this provider")
        return run

    async def create_run(self, run_ref, *, provider_org_id):
        record = self.catalog.resolve(run_ref, provider_org_id=provider_org_id)
        dataset = self.catalog.resolve(record.dataset_ref, provider_org_id=provider_org_id)
        if any(not self.grants[s.grant_ref]["current"] or
               "provider_sharing" not in self.grants[s.grant_ref]["purposes"]
               for s in dataset.samples):
            raise errors.Forbidden("schedule: a sample's grant is not in force")
        if record.run_id not in self.runs:
            self.runs[record.run_id] = {"provider": provider_org_id, "state": "queued",
                                        "ref": run_ref}
            for sid in sorted(s.sample_id for s in dataset.samples)[:record.max_cases]:
                self.cases[(record.run_id, sid)] = {"state": "pending", "attempts": 0}
        return await self.run_status(record.run_id, provider_org_id=provider_org_id)

    async def run_status(self, run_id, *, provider_org_id):
        run = self._run(run_id, provider_org_id)
        mine = [a for k, a in self.attempts.items() if k[0] == run_id]
        costs: dict[str, Decimal] = {}
        for a in mine:
            if a["cost"]:
                costs[a["cost"]["unit"]] = costs.get(a["cost"]["unit"], 0) + Decimal(a["cost"]["value"])
        return {"run_id": run_id, "run_ref": run["ref"], "state": run["state"],
                "cases": dict(Counter(c["state"] for k, c in self.cases.items() if k[0] == run_id)),
                "attempts": dict(Counter(a["state"] for a in mine)),
                "costs": {u: f"{v:.8f}" for u, v in costs.items()}}

    async def cancel_run(self, run_id, *, provider_org_id):
        run = self._run(run_id, provider_org_id)
        if run["state"] not in ("queued", "running"):
            raise errors.StateConflict("run: terminal")
        run["state"] = "cancelled"
        return await self.run_status(run_id, provider_org_id=provider_org_id)

    async def lease_case(self, run_id, *, provider_org_id, worker_id, lease_s):
        run = self._run(run_id, provider_org_id)
        if run["state"] not in ("queued", "running"):
            raise errors.AlreadyTerminal(f"run {run_id} is {run['state']}")
        pending = sorted(k[1] for k, c in self.cases.items()
                         if k[0] == run_id and c["state"] == "pending")
        if not pending:
            return None
        case = self.cases[(run_id, pending[0])]
        case["state"], case["attempts"] = "leased", case["attempts"] + 1
        n = case["attempts"]
        self.attempts[(run_id, pending[0], n)] = {"worker": worker_id, "state": "leased",
                                                  "expires": self.now + lease_s,
                                                  "digest": None, "cost": None}
        run["state"] = "running"
        return {"provider_org_id": provider_org_id, "run_id": run_id, "case_id": pending[0],
                "attempt": n, "worker_id": worker_id, "state": "leased",
                "idempotency_key": records.attempt_key(run_id, pending[0], n)}

    def _fence(self, lease) -> dict:
        run = self._run(lease["run_id"], lease["provider_org_id"])
        a = self.attempts.get((lease["run_id"], lease["case_id"], lease["attempt"]))
        if a is None or a["state"] != "leased" or a["worker"] != lease["worker_id"] \
                or run["state"] not in ("queued", "running") or self.now >= a["expires"]:
            raise errors.StaleLease("the attempt is not live")
        return a

    async def heartbeat(self, lease, *, lease_s):
        self._fence(lease)["expires"] = self.now + lease_s
        return lease

    async def finish(self, lease, *, outcome, results, cost=None):
        key = (lease["run_id"], lease["case_id"], lease["attempt"])
        digest = json.dumps([outcome, results, cost], sort_keys=True)
        a = self.attempts.get(key)
        if a is not None and a["digest"] is not None:
            if a["digest"] != digest:
                raise errors.IdempotencyConflict("the attempt finished with another outcome")
            return lease
        if outcome not in ("succeeded", "failed") or (outcome == "failed" and results):
            raise errors.InvalidRequest("succeed with results or fail with none")
        a = self._fence(lease)
        for r in results:
            if len(r["body"]) > 65536 or (key[0], key[1], r["evaluator_ref"]) in self.results:
                raise errors.InvalidRequest("one result per evaluator, a bounded body")
        for r in results:
            self.results[(key[0], key[1], r["evaluator_ref"])] = {"attempt": key[2],
                                                                  "body": r["body"]}
        a.update(state=outcome, digest=digest, cost=cost)
        self.cases[key[:2]]["state"] = "done" if outcome == "succeeded" else "failed"
        run = self.runs[key[0]]
        if not any(c["state"] in ("pending", "leased") for k, c in self.cases.items()
                   if k[0] == key[0]):
            run["state"] = "succeeded"
        return lease

    async def release(self, lease):
        self._fence(lease)
        key = (lease["run_id"], lease["case_id"], lease["attempt"])
        del self.attempts[key]
        case = self.cases[key[:2]]
        case["state"], case["attempts"] = "pending", case["attempts"] - 1
        return lease

    async def recover(self) -> int:
        n = 0
        for key, a in self.attempts.items():
            if a["state"] == "leased" and a["expires"] <= self.now:
                a["state"], n = "expired", n + 1
                self.cases[key[:2]]["state"] = "pending"
        return n


# ------------------------------------------------------------------ the dev endpoint
class Crash(BaseException):
    """A worker process dying mid-attempt (after the endpoint charged, before its answer)."""


class DevWallet:
    """The provider_dev endpoint as the runner sees it (the `DevEndpoint` port)."""

    def __init__(self, funded: str = "0", *, answer=None) -> None:
        self.balance = Credit(funded)                  # provider_dev starts at 0 (15:30)
        self.debited = Credit("0")
        self.replies: dict[str, dict] = {}             # Idempotency-Key -> first answer
        self.calls: list[dict] = []                    # every request received, replays too
        self.fail: list = []                           # exceptions to raise, in order
        self.crash_after_charge = False
        self.answer = answer or (lambda prompt: "a" + prompt.split("q")[-1])
        self.lock = threading.Lock()

    def reply(self, *, prompt, media, tool_results, seed, idempotency_key) -> dict:
        with self.lock:
            self.calls.append({"prompt": prompt, "seed": seed, "key": idempotency_key,
                               "tool_results": tool_results})
            if self.fail and (failure := self.fail.pop(0)) is not None:
                raise failure
            if idempotency_key in self.replies:
                return self.replies[idempotency_key]
        answer = self.answer(prompt)                   # outside the lock: calls overlap
        with self.lock:
            usage = (len(prompt) * 100, 1000)
            charged = RATE_CARD.debit(*usage)
            if self.balance < RATE_CARD.maximum_hold(*usage):
                raise errors.InsufficientCredit("the provider_dev wallet is exhausted")
            self.balance, self.debited = self.balance - charged, self.debited + charged
            reply = {"text": answer if isinstance(answer, str) else "",
                     "tool_calls": answer if isinstance(answer, list) else [],
                     "prompt_tokens": usage[0], "completion_tokens": usage[1]}
            self.replies[idempotency_key] = reply
        if self.crash_after_charge:
            raise Crash(idempotency_key)
        return reply

    async def complete(self, **request) -> Completion:
        r = await asyncio.to_thread(self.reply, **request)
        return Completion(text=r["text"], tool_calls=r["tool_calls"],
                          prompt_tokens=r["prompt_tokens"],
                          completion_tokens=r["completion_tokens"],
                          charged=RATE_CARD.debit(r["prompt_tokens"], r["completion_tokens"]))


def serve(wallet: DevWallet, port: int, api_key: str) -> ThreadingHTTPServer:
    """`wallet` as an OpenAI-compatible `/v1/chat/completions` on 127.0.0.1:`port`
    (402 `insufficient_quota`, 503 for an injected transient failure)."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, status: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):  # noqa: N802
            if self.path != "/v1/chat/completions" or \
                    self.headers.get("Authorization") != f"Bearer {api_key}":
                return self._send(401, {"error": {"code": "invalid_api_key"}})
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            messages = body["messages"]
            try:
                r = wallet.reply(prompt=messages[0]["content"], media=[], seed=body.get("seed"),
                                 tool_results=[json.loads(m["content"]) for m in messages[1:]],
                                 idempotency_key=self.headers.get("Idempotency-Key"))
            except errors.DomainError as failed:
                status = errors.HTTP_ERRORS.get(failed.code, (503, ""))[0]
                return self._send(status, {"error": {"code": failed.code}})
            except Crash:
                self.connection.close()
                return None
            calls = [{"id": f"c{i}", "type": "function",
                      "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])}}
                     for i, c in enumerate(r["tool_calls"])]
            return self._send(200, {"choices": [{"index": 0, "message": {
                "role": "assistant", "content": r["text"], "tool_calls": calls or None}}],
                "usage": {"prompt_tokens": r["prompt_tokens"],
                          "completion_tokens": r["completion_tokens"]}})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
