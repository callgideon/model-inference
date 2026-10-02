"""AP-10 10d: the SOP benchmark of a private candidate - one repeatable report.

    apps/infrx-api/.venv/bin/python -m infrx.lab.improve.sop \\
        --manifest items.jsonl --dataset-version ds-1 --base-url http://127.0.0.1:<port>/v1 \\
        --model marlin2b --serving-revision <AP-05 revision id> --seed 7 --target candidate \\
        [--sop sop.json --gold gold.json] --out report.json        # a key only in INFRX_API_KEY

The manifest is `models/marlin2b/dataset.py`'s JSONL (`id`, `video` relative to the manifest
or null, `prompt`, `max_tokens`, `start_s`, `end_s`). Every item is sent once, in manifest
order, at concurrency 1, with `temperature` 0 and the run's `seed`. The report pins the
dataset (version + the manifest's sha256 + the case ids' digest), the model and serving
revision (requested, listed by `/v1/models`, answered per response), the harness and parser
versions and the seed, and lists every item: answered, failed (with its class) or abstained
(`no_media`: an item without its video is never sent; `insufficient_evidence`: an answer with
no timed event). Quality and performance are separate sections:

- **quality**: output validity (answers whose events parse) is always counted; task agreement
  is `BLOCKED[P-07]` until the operator's SOP definition (versioned, with its matching
  tolerance) AND a human-reviewed gold set of exactly this dataset arrive. With both, the
  agreement is computed (one-to-one, in order, start and end within the tolerance) and never
  otherwise: teacher or model labels are not ground truth, and a gold set of another manifest
  is refused. No number is invented;
- **performance**: counts, latency p50/p95 (refused below 6 / 60 samples, `bench.py`'s rule)
  and token totals, labelled `fake` when the endpoint answers the fake engine's control
  surface (`tests/integration/fake_vllm.py`'s `GET /_control`) or the target is declared
  `fake`, and `meas.` only for a declared candidate that does not.

It replaces nothing: `bench.py` measures load and `dataset.py` runs resumable paid sweeps;
this is the small fixed-case quality run neither does.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import mimetypes
import os
import pathlib
import re
import statistics
import sys
import time
from collections.abc import Callable, Sequence
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

HARNESS = "infrx.sop_benchmark.1"
PARSER = "sop-events.1"
#: caption mode's `<X.X - Y.Y> text` event lines and find mode's `From X.X to Y.Y.`
#: (research/workloads/marlin-sop.md §1.4)
EVENT = re.compile(r"<\s*(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*>|"
                   r"From\s+(\d+(?:\.\d+)?)\s+to\s+(\d+(?:\.\d+)?)")
MIN_P50, MIN_P95 = 6, 60
TIMEOUT_S = 600.0


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Item(_Strict):
    id: str = Field(min_length=1, max_length=200)
    video: str | None
    prompt: str = Field(min_length=1)
    max_tokens: int = Field(default=512, ge=1, le=2048)
    start_s: float = Field(default=0, ge=0)
    end_s: float | None = None


class Span(_Strict):
    start_s: float = Field(ge=0)
    end_s: float = Field(ge=0)


class Definition(_Strict):
    """The operator's SOP definition (P-07): versioned, reviewed, with its tolerance."""

    sop_id: str = Field(min_length=1)
    version: int = Field(ge=1, le=1000)
    review_ref: str = Field(min_length=1)
    tolerance_s: float = Field(gt=0)
    steps: tuple[str, ...] = Field(min_length=1)


class GoldSet(_Strict):
    """Reference spans per item, of exactly one dataset manifest (P-07)."""

    dataset_version: str
    manifest_sha256: str
    provenance: Literal["human_reviewed", "teacher", "model"]
    reviewed_by: str = Field(min_length=1)
    review_ref: str = Field(min_length=1)
    labels: dict[str, tuple[Span, ...]] = Field(min_length=1)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_manifest(path: pathlib.Path) -> tuple[list[Item], str]:
    data = path.read_bytes()
    items = [Item.model_validate_json(line) for line in data.splitlines() if line.strip()]
    if len({i.id for i in items}) != len(items):
        raise ValueError("a manifest names each item once")
    return items, sha256(data)


def parse_events(text: str) -> list[tuple[float, float]]:
    """Every timed event of a caption- or find-mode answer, in order of appearance."""
    out = []
    for m in EVENT.finditer(text):
        a, b = (m.group(1), m.group(2)) if m.group(1) is not None else (m.group(3), m.group(4))
        out.append((float(a), float(b)))
    return out


def _messages(item: Item, base: pathlib.Path) -> list[dict[str, Any]]:
    assert item.video is not None
    path = base / item.video
    mime = mimetypes.guess_type(path.name)[0] or "video/mp4"
    url = f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()
    return [{"role": "user", "content": [{"type": "video_url", "video_url": {"url": url}},
                                         {"type": "text", "text": item.prompt}]}]


async def run(client: httpx.AsyncClient, items: Sequence[Item], *, model: str, seed: int,
              base: pathlib.Path, clock: Callable[[], float] = time.monotonic) -> list[dict]:
    """One row per item. ponytail: concurrency 1 - a quality run, not a load test
    (`bench.py` is the load test)."""
    rows = []
    for item in items:
        row: dict[str, Any] = {"id": item.id}
        if item.video is None:
            rows.append({**row, "outcome": "abstained", "reason": "no_media"})
            continue
        body = {"model": model, "messages": _messages(item, base),
                "max_tokens": item.max_tokens, "temperature": 0, "seed": seed}
        t0 = clock()
        try:
            response = await client.post("chat/completions", json=body)
        except httpx.HTTPError as failed:
            rows.append({**row, "outcome": "failed", "reason": type(failed).__name__})
            continue
        row["latency_s"] = round(clock() - t0, 6)
        try:
            reply = response.json() if response.status_code == 200 else None
            text = reply["choices"][0]["message"]["content"] if reply else None
            usage = reply.get("usage") if reply else None
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            reply = text = usage = None
        if response.status_code != 200:
            reason = f"http_{response.status_code}"
        elif not isinstance(text, str) or not isinstance(usage, dict):
            reason = "malformed_answer"
        elif reply.get("model") != model:   # type: ignore[union-attr]
            reason = "identity_mismatch"
        else:
            reason = None
        if reason is not None:
            rows.append({**row, "outcome": "failed", "reason": reason})
            continue
        assert isinstance(text, str) and isinstance(usage, dict)
        events = parse_events(text)
        rows.append({**row, "outcome": "answered" if events else "abstained",
                     "reason": None if events else "insufficient_evidence",
                     "answer_sha256": sha256(text.encode()), "events": events,
                     "prompt_tokens": usage.get("prompt_tokens"),
                     "completion_tokens": usage.get("completion_tokens"),
                     "video_s": None if item.end_s is None else item.end_s - item.start_s})
    return rows


def _match(predicted: Sequence[tuple[float, float]], gold: Sequence[Span],
           tolerance_s: float) -> int:
    """One-to-one, in order: each gold span takes the next unused prediction whose start and
    end are both within the tolerance."""
    matched, at = 0, 0
    for g in gold:
        for k in range(at, len(predicted)):
            start, end = predicted[k]
            if abs(start - g.start_s) <= tolerance_s and abs(end - g.end_s) <= tolerance_s:
                matched, at = matched + 1, k + 1
                break
    return matched


def quality(rows: Sequence[dict], *, dataset_version: str, manifest_sha256: str,
            definition: Definition | None, gold: GoldSet | None) -> dict:
    answered = [r for r in rows if r["outcome"] == "answered"]
    sent = [r for r in rows if r["outcome"] != "failed" and r.get("reason") != "no_media"]
    validity = {"answers": len(sent), "with_timed_events": len(answered)}
    blocked = None
    if definition is None or gold is None:
        blocked = "P-07: the SOP definition and a human-reviewed gold set are not supplied"
    elif gold.provenance != "human_reviewed":
        blocked = f"P-07: {gold.provenance} labels are not human ground truth"
    elif (gold.dataset_version, gold.manifest_sha256) != (dataset_version, manifest_sha256):
        blocked = "the gold set is of another dataset manifest"
    elif set(gold.labels) - {r["id"] for r in rows}:
        blocked = "the gold set labels items this manifest does not have"
    if blocked is not None:
        return {"verdict": "BLOCKED", "blocked": blocked, "output_validity": validity}
    assert definition is not None and gold is not None
    by_id = {r["id"]: r for r in rows}
    matched = missed = hallucinated = 0
    unanswered = []
    for item_id, spans in sorted(gold.labels.items()):
        predicted = [tuple(e) for e in by_id[item_id].get("events", ())]
        hit = _match(predicted, spans, definition.tolerance_s)  # type: ignore[arg-type]
        matched, missed = matched + hit, missed + len(spans) - hit
        hallucinated += len(predicted) - hit
        if by_id[item_id]["outcome"] != "answered":
            unanswered.append(item_id)
    return {"verdict": "COMPUTED", "sop": {"sop_id": definition.sop_id,
                                           "version": definition.version,
                                           "review_ref": definition.review_ref,
                                           "tolerance_s": definition.tolerance_s},
            "gold": {"review_ref": gold.review_ref, "reviewed_by": gold.reviewed_by,
                     "items": len(gold.labels)},
            "matched": matched, "missed": missed, "hallucinated": hallucinated,
            "precision": round(matched / (matched + hallucinated), 6)
            if matched + hallucinated else None,
            "recall": round(matched / (matched + missed), 6) if matched + missed else None,
            "unanswered_gold_items": unanswered, "output_validity": validity}


def _pct(values: list[float], q: int, need: int) -> float | None:
    if len(values) < need:
        return None
    return round(statistics.quantiles(sorted(values), n=100, method="inclusive")[q - 1], 6)


def performance(rows: Sequence[dict], label: str) -> dict:
    latency = [r["latency_s"] for r in rows if r.get("outcome") == "answered"
               or r.get("reason") == "insufficient_evidence"]
    count = {o: sum(r["outcome"] == o for r in rows) for o in ("answered", "failed", "abstained")}
    return {"label": label, **count,
            "latency_s": {"n": len(latency), "p50": _pct(latency, 50, MIN_P50),
                          "p95": _pct(latency, 95, MIN_P95)},
            "prompt_tokens": sum(r.get("prompt_tokens") or 0 for r in rows),
            "completion_tokens": sum(r.get("completion_tokens") or 0 for r in rows),
            "video_s": sum(r.get("video_s") or 0 for r in rows)}


async def is_fake(client: httpx.AsyncClient) -> bool:
    """The fake engine's control surface answers; a real vLLM (or gateway) has none."""
    try:
        probe = await client.get(client.base_url.copy_with(path="/_control"))
    except httpx.HTTPError:
        return False
    return probe.status_code == 200


async def benchmark(client: httpx.AsyncClient, manifest: pathlib.Path, *, dataset_version: str,
                    model: str, serving_revision: str, seed: int,
                    target: Literal["fake", "candidate"], definition: Definition | None = None,
                    gold: GoldSet | None = None) -> dict:
    items, digest = load_manifest(manifest)
    fake = target == "fake" or await is_fake(client)
    listed = await client.get("models")
    served = sorted(m.get("id") for m in listed.json().get("data", ())) \
        if listed.status_code == 200 else None
    rows = await run(client, items, model=model, seed=seed, base=manifest.parent)
    return {"harness": HARNESS, "parser": PARSER, "seed": seed,
            "dataset": {"version": dataset_version, "manifest_sha256": digest,
                        "cases_sha256": sha256("\n".join(i.id for i in items).encode()),
                        "items": len(items)},
            "model": {"requested": model, "serving_revision": serving_revision,
                      "listed": served, "target": target},
            "items": rows,
            "failures": [{"id": r["id"], "reason": r["reason"]} for r in rows
                         if r["outcome"] == "failed"],
            "abstentions": [{"id": r["id"], "reason": r["reason"]} for r in rows
                            if r["outcome"] == "abstained"],
            "quality": quality(rows, dataset_version=dataset_version, manifest_sha256=digest,
                               definition=definition, gold=gold),
            "performance": performance(rows, "fake" if fake else "meas.")}


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--manifest", type=pathlib.Path, required=True)
    ap.add_argument("--dataset-version", required=True)
    ap.add_argument("--base-url", required=True, help="the candidate's OpenAI base, …/v1")
    ap.add_argument("--model", required=True)
    ap.add_argument("--serving-revision", required=True)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--target", choices=("fake", "candidate"), required=True)
    ap.add_argument("--sop", type=pathlib.Path)
    ap.add_argument("--gold", type=pathlib.Path)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    a = ap.parse_args(argv)
    definition = Definition.model_validate_json(a.sop.read_bytes()) if a.sop else None
    gold = GoldSet.model_validate_json(a.gold.read_bytes()) if a.gold else None
    key = os.environ.get("INFRX_API_KEY")          # never argv, never written anywhere
    headers = {"Authorization": f"Bearer {key}"} if key else {}

    async def go() -> dict:
        async with httpx.AsyncClient(base_url=a.base_url.rstrip("/") + "/", headers=headers,
                                     timeout=TIMEOUT_S) as client:
            return await benchmark(client, a.manifest, dataset_version=a.dataset_version,
                                   model=a.model, serving_revision=a.serving_revision,
                                   seed=a.seed, target=a.target, definition=definition,
                                   gold=gold)
    report = asyncio.run(go())
    a.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"quality": report["quality"]["verdict"],
                      "performance": report["performance"]["label"],
                      "failures": len(report["failures"]),
                      "abstentions": len(report["abstentions"])}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
