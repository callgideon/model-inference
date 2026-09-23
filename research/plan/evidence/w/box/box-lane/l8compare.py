#!/usr/bin/env python3
"""E1B L8: caption-event parity, reference.py (transformers .caption()) vs the served engine
(smoke.py, same canonical prompt). Events = the `<start - end>` spans in order, found with
decide.py's EVENT regex in each side's text (leading <think> block stripped, as smoke.py does).
Wording is not compared (E1B-protocol §4). Parity, not accuracy (P-07).

    python3 l8compare.py <L8 dir> <repo>/models/marlin2b/measure
"""
import json, pathlib, re, sys

sys.path.insert(0, sys.argv[2])
from decide import EVENT  # noqa: E402

THINK = re.compile(r"^\s*<think>.*?(</think>|$)", re.S)
d = pathlib.Path(sys.argv[1])


def events(text):
    return [(float(a), float(b)) for a, b in EVENT.findall(THINK.sub("", text, count=1))]


def ref_text(path):
    raw = path.read_text(errors="replace")
    try:
        return json.loads(raw)["raw"]
    except (ValueError, KeyError, TypeError):
        return None


print("clip | reference events | served default: events, equal | served v1: events, equal")
for ref in sorted(d.glob("ref-*.json")):
    cid = ref.stem[len("ref-"):]
    r = ref_text(ref)
    cells = [cid, "no output" if r is None else f"{len(events(r))}"]
    for budget in ("default", "v1"):
        s = d / f"served-{cid}-{budget}.txt"
        text = s.read_text(errors="replace") if s.exists() else ""
        if not text.strip():
            cells.append("no answer (see .err)")
        elif r is None:
            cells.append(f"{len(events(text))}, unknown")
        else:
            cells.append(f"{len(events(text))}, {'yes' if events(text) == events(r) else 'no'}")
    print(" | ".join(cells))
    if r is not None:
        print(f"  ref:    {events(r)}")
        for budget in ("default", "v1"):
            s = d / f"served-{cid}-{budget}.txt"
            if s.exists() and s.read_text().strip():
                print(f"  {budget:7} {events(s.read_text(errors='replace'))}")
