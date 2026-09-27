"""Mechanical half of VIDEO-/ROBOT-/BACKEND-CONTRACT for the X1/X3/X5 discovery documents.

A contract field is either sourced or `BLOCKED: <pending input> — <exact missing input>`;
any blocked field keeps the trial blocked; a field the trial status
says was not supplied must be BLOCKED; the manifest status stays `planned`; the audit
log is the last section. Judgement (is the source right?) stays with the reviewer.

    python3 research/workloads/video/check_discovery.py     # exit 1 on any error
"""
import re
import sys
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parent.parent  # research/workloads


class Spec(NamedTuple):
    inputs: tuple  # pending-input ids a BLOCKED marker may cite
    fields: tuple  # rows the failure oracle requires


SPECS = {
    "video/live-video-contract.md": Spec(
        inputs=("P-13",),
        fields=("Task", "Input cadence and cameras", "Window and hop", "Clock alignment", "Retention",
                "Response shape", "Deployment location", "Causal labelling rule", "Ground truth", "Deadline",
                "Freshness threshold", "Quality threshold", "Throughput threshold", "Duplicate-event policy",
                "Overload policy", "Serving capability", "Transport", "Allocated capacity")),
    "robotics/robot-freshness-contract.md": Spec(
        inputs=("P-14",),
        fields=("Policy artifact", "Robot and task", "Camera schema", "Proprioception and state schema",
                "Action schema", "Normalization", "Action horizon", "Clocks", "ROS2 and recording versions",
                "End-to-end latency", "Freshness target", "Placement", "Stale-action rejection",
                "Local control owner", "Proposed-versus-executed evidence", "Offline and simulator acceptance",
                "Hardware trial authorization", "Adapter and MCAP mapping")),
    "hardware/non-nvidia-backend.md": Spec(
        inputs=("P-15",),
        fields=("Chip", "Runtime", "Model", "Workload", "Supported operators", "Supported dtypes", "Licensing",
                "Driver and toolchain", "Artifact conversion", "Engine port", "Load profile", "Quality parity",
                "Capacity", "Cost including idle capacity", "Failure recovery", "Prototype scope",
                "Resource allocation", "Go/no-go criteria", "Trial access")),
}

BLOCKED = re.compile(r"BLOCKED: (P-\d+) — (.{12,})")
EMPTY = {"", "—", "-"}


def rows(text):
    """Field → (value, source) from the table under `## Contract fields`."""
    match = re.search(r"^## (?:\d+\. )?Contract fields\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    if not match:
        return None
    out = {}
    for line in match.group(1).splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.startswith("|") and len(cells) >= 3 and cells[0] not in ("Field", "") and not cells[0].startswith("---"):
            out[cells[0]] = (cells[1], cells[2])
    return out


def check(text, spec):
    errors = []
    if not re.search(r"^\*\*Manifest status:\*\* planned\b", text, re.M):
        errors.append("Manifest status must stay `planned` until the owner approves")
    table = rows(text)
    if table is None:
        return errors + ["no `## Contract fields` table"]
    blocked = False
    trial = re.search(r"^\*\*Trial status:\*\*(.*)$", text, re.M)
    said_missing = trial.group(1).lower() if trial else ""  # inputs the document itself says were not supplied
    for field in spec.fields:
        if field not in table:
            errors.append(f"{field}: required contract field missing")
            continue
        value, source = table[field]
        if value.startswith("BLOCKED"):
            marker = BLOCKED.match(value)
            if not marker or marker.group(1) not in spec.inputs:
                errors.append(f"{field}: BLOCKED must be `BLOCKED: <one of {spec.inputs}> — <exact missing input>`")
            blocked = True
        elif re.search(rf"\b{re.escape(field.lower())}\b", said_missing):
            errors.append(f"{field}: the trial status says it was not supplied, so the row must be BLOCKED")
        elif source in EMPTY:
            errors.append(f"{field}: value has no source and is not BLOCKED (never invent a target)")
    if blocked and not re.search(r"^\*\*Trial status:\*\* BLOCKED", text, re.M):
        errors.append("Trial status must be BLOCKED while any contract field is BLOCKED")
    headings = re.findall(r"^## (.+)$", text, re.M)
    if not headings or not re.fullmatch(r"(Audit|Verification) log.*", headings[-1]):
        errors.append("the audit log must be the last section")
    return errors


def main():
    bad = 0
    for path, spec in SPECS.items():
        for error in check((ROOT / path).read_text(), spec):
            bad += 1
            print(f"{path}: {error}")
    print(f"{len(SPECS)} documents, {bad} errors")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
