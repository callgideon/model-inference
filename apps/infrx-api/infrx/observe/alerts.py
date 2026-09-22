"""I3B.a: evaluate the alert rules (`infra/alerts/alerts.json`) against scraped metrics.

    python -m infrx.observe.alerts --rules infra/alerts/alerts.json \\
        --source http://127.0.0.1:8001/metrics --source /var/lib/infrx/metrics/worker.prom \\
        --state /var/lib/infrx/metrics/alert-state.json

Prints one JSON line per firing alert and exits 1 when anything fires, 0 when nothing
does - so a systemd timer (`OnFailure=`) or an SSM command is the whole delivery path, and
no alerting service has to exist on a single-GPU pilot. A source that cannot be read is
itself an alert (`ScrapeFailed`): silence must never mean "healthy".

A rule is data, not PromQL, so it can be checked here and in tests without a Prometheus:

    {"name", "severity": "page"|"ticket", "summary", "runbook", "threshold_status",
     "metric": sample name, "match": {label: value | [values]}, "agg": "sum"|"max"|"min",
     "increase": bool, "age": bool, "divide_by": {"metric", "match", "agg"},
     "op": ">"|">="|"<"|"<=", "threshold": number}

Without `agg` every matching series is judged on its own and fires with its labels. With
`increase` a counter is compared with the previous run's value (`--state`); a first run
has no previous value and judges nothing, and a counter that went down (a restart) counts
from zero. `age` turns a unix-time gauge into seconds since then.
"""
from __future__ import annotations

import argparse
import json
import operator
import re
import sys
import time
import urllib.request
from pathlib import Path

OPS = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le}
AGGREGATES = {"sum": sum, "max": max, "min": min}
_SAMPLE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(.*)\})?\s+(\S+)\s*$")
_LABEL = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\.)*)"')
_ESCAPED = re.compile(r"\\(.)")


def _unescape(match: re.Match) -> str:
    return "\n" if match.group(1) == "n" else match.group(1)


Key = tuple[str, tuple[tuple[str, str], ...]]


def parse(text: str) -> dict[Key, float]:
    """Prometheus text exposition -> {(name, sorted labels): value}."""
    samples: dict[Key, float] = {}
    for line in text.splitlines():
        match = _SAMPLE.match(line.strip())
        if not match or line.startswith("#"):
            continue
        name, inside, value = match.groups()
        labels = tuple(sorted((key, _ESCAPED.sub(_unescape, raw))
                              for key, raw in _LABEL.findall(inside or "")))
        samples[(name, labels)] = float(value)
    return samples


def _matches(labels: tuple[tuple[str, str], ...], match: dict) -> bool:
    have = dict(labels)
    for key, wanted in (match or {}).items():
        allowed = wanted if isinstance(wanted, list) else [wanted]
        if have.get(key) not in allowed:
            return False
    return True


def _series(samples, previous, metric: str, match: dict, *, increase: bool, age: bool,
            now: float) -> dict[tuple, float]:
    out = {}
    for (name, labels), value in samples.items():
        if name != metric or not _matches(labels, match):
            continue
        if increase:
            before = (previous or {}).get((name, labels))
            if previous is None:
                continue                            # first run: nothing to compare with
            value = value - before if before is not None and value >= before else value
        if age:
            value = now - value
        out[labels] = value
    return out


def evaluate(rules: list[dict], samples: dict[Key, float],
             previous: dict[Key, float] | None = None, *, now: float | None = None) -> list[dict]:
    """The firing instances of `rules` over `samples`. Pure: no I/O."""
    now = time.time() if now is None else now
    firing = []
    for rule in rules:
        flags = dict(increase=bool(rule.get("increase")), age=bool(rule.get("age")), now=now)
        series = _series(samples, previous, rule["metric"], rule.get("match"), **flags)
        if not series:
            continue
        agg = rule.get("agg")
        if agg:
            values = {(): AGGREGATES[agg](series.values())}
        else:
            values = series
        divisor = rule.get("divide_by")
        if divisor:
            below = _series(samples, previous, divisor["metric"], divisor.get("match"), **flags)
            if not below or not agg:
                continue
            total = AGGREGATES[divisor.get("agg", "sum")](below.values())
            if total == 0:
                continue
            values = {(): values[()] / total}
        for labels, value in values.items():
            if OPS[rule["op"]](value, rule["threshold"]):
                firing.append({"alert": rule["name"], "severity": rule["severity"],
                               "value": value, "labels": dict(labels),
                               "summary": rule["summary"], "runbook": rule["runbook"]})
    return firing


def _read(source: str) -> str:
    if source.startswith(("http://", "https://")):
        with urllib.request.urlopen(source, timeout=10) as answer:   # noqa: S310 - operator input
            return answer.read().decode()
    return Path(source).read_text()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rules", required=True, type=Path)
    parser.add_argument("--source", action="append", required=True,
                        help="a /metrics URL or an exposition file; repeatable")
    parser.add_argument("--state", type=Path, help="previous samples, for `increase` rules")
    args = parser.parse_args(argv)
    rules = json.loads(args.rules.read_text())["rules"]
    samples: dict[Key, float] = {}
    firing = []
    for source in args.source:
        try:
            samples.update(parse(_read(source)))
        except Exception as failure:                # noqa: BLE001 - reported, never silent
            firing.append({"alert": "ScrapeFailed", "severity": "page", "value": 1,
                           "labels": {"source": source},
                           "summary": f"could not read a metrics source: "
                                      f"{type(failure).__name__}",
                           "runbook": "infra/runbooks/restart.md"})
    previous = None
    if args.state and args.state.exists():
        stored = json.loads(args.state.read_text())
        previous = {(name, tuple(map(tuple, labels))): value for name, labels, value in stored}
    firing += evaluate(rules, samples, previous)
    if args.state:
        args.state.write_text(json.dumps([[name, labels, value]
                                          for (name, labels), value in samples.items()]))
    for alert in firing:
        print(json.dumps(alert, sort_keys=True))
    return 1 if firing else 0


if __name__ == "__main__":
    sys.exit(main())
