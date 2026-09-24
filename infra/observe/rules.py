#!/usr/bin/env python3
"""The rule set the evaluator runs: infra/alerts/alerts.json (I3B) + operations.json (I8).

    python3 infra/observe/rules.py infra/alerts > /run/infrx-observe/rules.json

operations.json adds rules and may override fields of an alerts.json rule by name (each
override carries its reason); a rule name defined twice, or an override of a rule that
does not exist, is refused - the merged set must be exactly what the two files say. The
merged document's `version` is "a<alerts version>+o<operations version>" (versioned alarms:
a delivery names the rule set it came from).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def merge(directory: Path) -> dict:
    alerts = json.loads((directory / "alerts.json").read_text())
    ops = json.loads((directory / "operations.json").read_text())
    rules = {rule["name"]: dict(rule) for rule in alerts["rules"]}
    for name, override in ops.get("overrides", {}).items():
        if name not in rules:
            raise SystemExit(f"override of an unknown rule: {name}")
        rules[name].update({k: v for k, v in override.items() if k != "reason"})
    for rule in ops["rules"]:
        if rule["name"] in rules:
            raise SystemExit(f"rule defined twice: {rule['name']}")
        rules[rule["name"]] = rule
    return {"version": f"a{alerts['version']}+o{ops['version']}", "rules": list(rules.values())}


if __name__ == "__main__":
    print(json.dumps(merge(Path(sys.argv[1] if len(sys.argv) > 1 else "infra/alerts"))))
