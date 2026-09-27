"""B2: compare two evaluation runs with honest uncertainty (EVAL-COMPARE).

**B2.a pairing.** Both runs must share one case universe (H1's `compare`: provider,
dataset, evaluator, environment, max_cases); what differs is labelled `rerun`,
`single_factor` or - only with the protocol's tag - `multifactor`. Cases pair by exact id;
a case twice in one run, or outside the frozen universe, is refused.

**B2.b scoring.** A case record is `{case_id, score, error?, slices, cluster, latency_ms,
costs}` (B1's result plus its attempts' costs). Observed means are over the whole
universe: an error scores 0 and a missing or not comparable case (no score) counts 0, so
dropping failures never helps. The paired estimate is the mean candidate - baseline score
difference over cases scored in both runs, with a cluster-robust standard error (cases of
one source move together) and a Student t critical value on clusters - 1 degrees of freedom
(`t_quantile`: the stdlib has no t). Costs are totalled per unit (CREDIT, PROVIDER_USD) and
compared per unit, never converted; latency is a nearest-rank distribution.

**B2.c decision.** Every threshold is the protocol's (a workload input, no default): the
confidence, the overall non-inferiority margin and minimum cases, each required slice's
margin and minimum, and the metric's basis (deterministic metric or teacher judgment),
reported beside the estimates, apart from the observed facts. A verdict is `non_inferior`
(lower bound >= -margin), `inferior` (upper bound < -margin), `uncertain` or
`insufficient` (too few cases or clusters). Any inferior required slice or overall is
`reject` - an aggregate gain cannot hide it; incomplete coverage or any uncertain or
insufficient verdict is `inconclusive`; otherwise `accept`. `improved` is claimed only when
the overall lower bound is above 0. The report binds both run refs, the universe and the
protocol by digest and carries its own digest; storing it is lab-sql's (a schema request).
"""
from __future__ import annotations

import hashlib
import math
from statistics import NormalDist
from typing import Any, Literal

from pydantic import Field, ValidationError

from ...contracts import errors
from ...contracts.lab import records as lab
from ...harnesses.replay import compare as pair_runs


class SliceRule(lab.LabModel):
    margin: float = Field(ge=0)
    min_cases: int = Field(ge=2)


class Protocol(lab.LabModel):
    confidence: float = Field(gt=0.5, lt=1)
    margin: float = Field(ge=0)
    min_cases: int = Field(ge=2)
    metric_source: Literal["deterministic_metric", "teacher_judgment"]
    required_slices: dict[str, SliceRule]
    multifactor_tag: str | None = None


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(lab.canonical(value)).hexdigest()


def t_quantile(p: float, df: int) -> float:
    """Student t quantile by the Cornish-Fisher expansion of the normal one.
    ponytail: ~3% low at df=2 and worse at df=1 (refused upstream); scipy if ever needed."""
    z = NormalDist().inv_cdf(p)
    return (z + (z**3 + z) / (4 * df) + (5 * z**5 + 16 * z**3 + 3 * z) / (96 * df**2)
            + (3 * z**7 + 19 * z**5 + 17 * z**3 - 15 * z) / (384 * df**3))


def _index(records: list[dict[str, Any]], universe: set[str]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for record in records:
        cid = record["case_id"]
        if cid in out:
            raise errors.InvalidRequest(f"duplicate case {cid}")
        if cid not in universe:
            raise errors.InvalidRequest(f"case {cid} is outside the frozen universe")
        out[cid] = record
    return out


def _value(record: dict[str, Any] | None) -> float | None:
    """The case's score, 0 for an error, None when missing or not comparable."""
    if record is None:
        return None
    return 0.0 if record.get("error") else record.get("score")


def _costs(records) -> dict[str, Any]:
    totals: dict[str, Any] = {}
    for record in records:
        for amount in record.get("costs", []):
            try:
                money = lab.Amount.model_validate(amount)
            except ValidationError as refused:
                raise errors.InvalidRequest(f"a cost is a unit-tagged CREDIT or PROVIDER_USD "
                                            f"amount: {refused}") from None
            totals[money.unit] = totals[money.unit] + money.amount if money.unit in totals \
                else money.amount
    return totals


def _latency(records) -> dict[str, int]:
    seen = sorted(r["latency_ms"] for r in records if r.get("latency_ms") is not None)
    if not seen:
        return {"n": 0}
    rank = {p: seen[math.ceil(p / 100 * len(seen)) - 1] for p in (50, 90, 99)}
    return {"n": len(seen), "p50": rank[50], "p90": rank[90], "p99": rank[99], "max": seen[-1]}


def _estimate(pairs: list[tuple[str, float, float]], margin: float, min_cases: int,
              confidence: float) -> dict[str, Any]:
    n = len(pairs)
    clusters: dict[str, float] = {}
    diffs = [c - b for _, b, c in pairs]
    mean = math.fsum(diffs) / n if n else 0.0
    for (cluster, _, _), d in zip(pairs, diffs):
        clusters[cluster] = clusters.get(cluster, 0.0) + d - mean
    g = len(clusters)
    if n < min_cases or g < 2:
        return {"n": n, "clusters": g, "diff": None, "low": None, "high": None,
                "verdict": "insufficient", "improved": False}
    se = math.sqrt(g / (g - 1) * math.fsum(s * s for s in clusters.values()) / n**2)
    half = t_quantile((1 + confidence) / 2, g - 1) * se
    low, high = mean - half, mean + half
    verdict = "non_inferior" if low >= -margin else "inferior" if high < -margin else "uncertain"
    return {"n": n, "clusters": g, "diff": mean, "low": low, "high": high, "verdict": verdict,
            "improved": low > 0}


def compare(baseline_run: dict[str, Any], baseline: list[dict[str, Any]],
            candidate_run: dict[str, Any], candidate: list[dict[str, Any]], *,
            universe: list[str], protocol: dict[str, Any]) -> dict[str, Any]:
    try:
        rules = Protocol.model_validate(protocol)
    except ValidationError as refused:
        raise errors.InvalidRequest(f"protocol: {refused}") from None
    kind, factors = pair_runs(baseline_run, candidate_run, multifactor_tag=rules.multifactor_tag)
    cases = set(universe)
    runs = {"baseline": _index(baseline, cases), "candidate": _index(candidate, cases)}
    observed: dict[str, Any] = {"universe": len(cases)}
    for name, recs in runs.items():
        values = [_value(recs.get(cid)) for cid in cases]
        observed[name] = {
            "mean": math.fsum(v or 0.0 for v in values) / len(cases),
            "missing": len(cases) - len(recs),
            "errors": sum(bool(r.get("error")) for r in recs.values()),
            "not_comparable": sum(_value(r) is None for r in recs.values()),
            "costs": {u: str(v) for u, v in _costs(recs.values()).items()},
            "latency_ms": _latency(recs.values())}
    base_costs, cand_costs = _costs(runs["baseline"].values()), _costs(runs["candidate"].values())
    observed["cost_delta"] = {u: str(cand_costs[u] - base_costs[u])
                              for u in base_costs if u in cand_costs}
    pairs = {cid: (runs["baseline"][cid].get("cluster") or cid,
                   _value(runs["baseline"][cid]), _value(runs["candidate"][cid]))
             for cid in sorted(cases) if cid in runs["baseline"] and cid in runs["candidate"]}
    pairs = {cid: p for cid, p in pairs.items() if p[1] is not None and p[2] is not None}
    observed["paired"] = len(pairs)
    overall = _estimate(list(pairs.values()), rules.margin, rules.min_cases, rules.confidence)
    slices = {name: _estimate([p for cid, p in pairs.items()
                               if name in runs["baseline"][cid].get("slices", [])],
                              rule.margin, rule.min_cases, rules.confidence)
              for name, rule in sorted(rules.required_slices.items())}
    verdicts = [("overall", overall["verdict"])] + [(f"slice {name}", e["verdict"])
                                                    for name, e in slices.items()]
    inferior = [f"{name} inferior" for name, v in verdicts if v == "inferior"]
    unsure = [f"{name} {v}" for name, v in verdicts if v in ("uncertain", "insufficient")]
    if len(pairs) < len(cases):
        unsure.insert(0, f"coverage {len(pairs)}/{len(cases)}")
    decision = {"outcome": "reject", "reasons": inferior} if inferior else \
        {"outcome": "inconclusive", "reasons": unsure} if unsure else \
        {"outcome": "accept", "reasons": []}
    report = {"schema": "infrx.eval_report.1", "baseline_run": lab.ref_of(baseline_run),
              "candidate_run": lab.ref_of(candidate_run), "universe_digest": _digest(sorted(cases)),
              "protocol": protocol, "protocol_digest": _digest(protocol),
              "pairing": {"kind": kind, "factors": list(factors), "tag": rules.multifactor_tag},
              "observed": observed,
              "estimates": {"basis": rules.metric_source, "overall": overall, "slices": slices},
              "decision": decision}
    return {**report, "report_digest": _digest(report)}
