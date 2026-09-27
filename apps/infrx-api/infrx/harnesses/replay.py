"""H1: versioned harness revisions and bounded, recorded-only replay.

A harness revision is the F3 record `lab.harness_revision.1`: prompt template, processor
profile, input mapping, tool schemas and reference output, identified by the sha256 of
its canonical JSON (`revision_ref`). There is no code field and the adapter is one of the
three built-ins, so an uploaded program cannot be a harness.

`Replayer` runs cases against **one dev deployment of the harness's own provider** and
nothing else:

* the model is called through the port it is given; tools are never called at all. A
  tool call is answered from a recording (`recording_key`) if the tool is declared
  `read_only`; a `network_mutation` or `actuator` tool is `blocked`, an undeclared tool
  or a missing recording is `unsupported`. Blocked/unsupported cases are not comparable.
* requests, bytes and wall time are bounded for the whole run; the first breach raises
  `ReplayBoundExceeded` and the run stays stopped.
* the revision is re-derived before each case: a harness edited mid-run is refused.

`bind` pins a revision into an F3 evaluation run after the rights port (L2, the one
membership seam of R156) authorizes scheduling for the calling user; `compare` says what two runs differ in (single factor, tagged multifactor,
or a rerun) and refuses runs over different case universes.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from ..contracts import errors
from ..contracts.lab import records as lab
from ..contracts.v2 import records as v2


def revision_ref(payload: dict[str, Any]) -> str:
    """The immutable ref of a harness revision; refuses anything F3 refuses."""
    if payload.get("schema") != "lab.harness_revision.1":
        raise lab.LabRejected("unknown_schema", "not a harness revision")
    return lab.ref_of(payload)


def recording_key(name: str, arguments: Any) -> str:
    return f"{name}:{hashlib.sha256(lab.canonical(arguments)).hexdigest()}"


class RightsPort(Protocol):
    """L2's gate evaluator: raises `Forbidden` unless `user_id` holds a current membership
    of the provider AND the provider a current grant for the dataset at this gate."""

    def authorize(self, gate: lab.Gate, *, user_id: str, provider_org_id: str,
                  dataset_ref: str) -> None: ...


def bind(run: dict[str, Any], harness_ref: str, *, rights: RightsPort,
         user_id: str) -> dict[str, Any]:
    """A copy of `run` pinned to `harness_ref`, validated, then authorized for scheduling.

    `user_id` is the server-derived caller (the session subject), never a request field.
    """
    bound = {**run, "harness_ref": harness_ref}
    record = lab.parse(bound)          # mutable or foreign refs stop here, before any read
    rights.authorize(lab.Gate.schedule, user_id=user_id, provider_org_id=record.provider_org_id,
                     dataset_ref=record.dataset_ref)
    return bound


UNIVERSE = ("provider_org_id", "dataset_ref", "evaluator_ref", "environment", "max_cases")
FACTORS = ("serving_ref", "harness_ref", "seed")


def compare(a: dict[str, Any], b: dict[str, Any], *,
            multifactor_tag: str | None = None) -> tuple[str, tuple[str, ...]]:
    """("rerun" | "single_factor" | "multifactor", the differing factors)."""
    ra, rb = lab.parse(a), lab.parse(b)
    if any(getattr(ra, f) != getattr(rb, f) for f in UNIVERSE):
        raise errors.InvalidRequest("runs over different case universes are not comparable")
    differ = tuple(f for f in FACTORS if getattr(ra, f) != getattr(rb, f))
    if len(differ) > 1 and not multifactor_tag:
        raise errors.InvalidRequest(f"{differ} all differ: tag the multifactor comparison")
    return ({0: "rerun", 1: "single_factor"}.get(len(differ), "multifactor"), differ)


@dataclass(frozen=True)
class Bounds:
    max_requests: int
    max_bytes: int
    max_seconds: float


class ReplayBoundExceeded(errors.BudgetExceeded):
    def __init__(self, bound: str) -> None:
        self.bound = bound
        super().__init__(f"replay bound exceeded: {bound}")


@dataclass(frozen=True)
class Outcome:
    status: str                    # complete | failed | unsupported | blocked
    output: Any
    reasons: tuple[str, ...]

    @property
    def comparable(self) -> bool:
        return self.status in ("complete", "failed")


def coverage(outcomes: list[Outcome]) -> dict[str, Any]:
    """Every case counted; every reason a case is not comparable, visible."""
    return {"cases": len(outcomes), "comparable": sum(o.comparable for o in outcomes),
            "not_comparable": dict(Counter(r for o in outcomes if not o.comparable for r in o.reasons))}


# model(prompt, media refs, tool results) -> {"text": str, "tool_calls": [{"name", "arguments"}]}
ModelPort = Callable[[str, list[str], list[dict[str, Any]]], dict[str, Any]]
_PLACEHOLDER = re.compile(r"\{\{([a-z_][a-z0-9_]*)\}\}")
_MISSING = object()


def _at(case: dict[str, Any], path: str) -> Any:
    node: Any = case
    for part in path.split("."):
        node = node.get(part, _MISSING) if isinstance(node, dict) else _MISSING
    return node


class Replayer:
    def __init__(self, harness: dict[str, Any], deployment: v2.DeploymentRevision,
                 model: ModelPort, recordings: dict[str, Any], bounds: Bounds,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if deployment.environment is not v2.Environment.dev:
            raise errors.Forbidden("replay targets dev deployments only")
        self._payload, self._ref = harness, revision_ref(harness)
        self._revision: lab.HarnessRevision = lab.parse(harness)
        if deployment.provider_org_id != self._revision.provider_org_id:
            raise errors.Forbidden("replay targets the harness provider's own deployment only")
        self._model, self._recordings, self._bounds, self._clock = model, recordings, bounds, clock
        self._start, self._requests, self._bytes = clock(), 0, 0
        self._stopped: str | None = None

    def _stop(self, bound: str) -> ReplayBoundExceeded:
        self._stopped = bound
        return ReplayBoundExceeded(bound)

    def _call(self, prompt: str, media: list[str], results: list[dict[str, Any]]) -> dict[str, Any]:
        if self._requests >= self._bounds.max_requests:
            raise self._stop("requests")
        if self._clock() - self._start > self._bounds.max_seconds:
            raise self._stop("seconds")
        self._requests += 1
        answer = self._model(prompt, media, results)
        self._bytes += len(prompt.encode()) + len(lab.canonical(results)) + len(lab.canonical(answer))
        if self._bytes > self._bounds.max_bytes:
            raise self._stop("bytes")
        return answer

    def replay(self, case: dict[str, Any]) -> Outcome:
        if self._stopped:
            raise ReplayBoundExceeded(self._stopped)
        if revision_ref(self._payload) != self._ref:
            raise errors.StateConflict("the harness changed mid-run; start a new run")
        rev = self._revision
        values = {name: _at(case, path) for name, path in rev.input_mapping.items()}
        media: list[str] = []
        if rev.adapter == "finite_video":
            values["media_ref"], values["duration_ms"] = (_at(case, "sample.media_ref"),
                                                          _at(case, "sample.duration_ms"))
        missing = [f"missing_input:{rev.input_mapping.get(k, 'sample.' + k)}"
                   for k, v in values.items() if v is _MISSING]
        if missing:
            return Outcome("unsupported", None, tuple(missing))
        if rev.adapter == "finite_video":
            if not isinstance(values["duration_ms"], int) or not 1 <= values["duration_ms"] <= lab.MAX_VIDEO_MS:
                return Outcome("unsupported", None, ("video_over_cap",))
            media = [values["media_ref"]]
        prompt = _PLACEHOLDER.sub(lambda m: str(values.get(m.group(1), m.group(0))), rev.prompt_template)
        tools = {tool.name: tool for tool in rev.tools}
        results: list[dict[str, Any]] = []
        while True:
            answer = self._call(prompt, media, results)
            calls = answer.get("tool_calls") or []
            if not calls:
                break
            for call in calls:
                tool = tools.get(call["name"])
                if tool is None:
                    return Outcome("unsupported", None, (f"undeclared_tool:{call['name']}",))
                if tool.effect != "read_only":
                    return Outcome("blocked", None, (f"{tool.effect}:{tool.name}",))
                recorded = self._recordings.get(recording_key(tool.name, call.get("arguments", {})), _MISSING)
                if recorded is _MISSING:
                    return Outcome("unsupported", None, (f"missing_recording:{tool.name}",))
                results.append({"name": tool.name, "result": recorded})
        text = answer.get("text", "")
        if rev.adapter != "structured":
            return Outcome("complete", text, ())
        try:
            output = json.loads(text)
        except ValueError:
            return Outcome("failed", None, ("output_not_json",))
        return Outcome("complete", output, ()) if isinstance(output, dict) else \
            Outcome("failed", None, ("output_not_object",))
