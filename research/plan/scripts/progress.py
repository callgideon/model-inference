#!/usr/bin/env python3
"""Render the backend-first progress tracker from tasks.json + the coordinator's state overlay.

    python3 research/plan/scripts/progress.py            # writes PROGRESS.md and progress.html next to the state file

The manifest is the source of task status; progress-state.json carries what the manifest does not
(lanes in flight, reviews, blocked inputs, checkpoints). ETA is a cadence projection from recorded
merge windows, labelled provisional; GPU-gated work has no ETA until its input is allocated.
"""
import datetime as dt
import html
import json
import sys
from pathlib import Path

PLAN = Path(__file__).resolve().parents[1]
EVID = PLAN / "evidence" / "coordinator"
sys.path.insert(0, str(PLAN / "scripts"))
from validate_plan import closure  # noqa: E402

BANDS = [
    ("B0", "Baseline & contracts", ["S1", "F2R", "I0", "E2R", "S2M", "F2P"]),
    ("B1", "Durable endpoint", ["D1R", "D2", "D3", "D4", "D5", "A1", "M2", "M3", "Q2", "Q3", "W2", "W3", "G1R", "G2", "G3", "G4U", "G6B"]),
    ("B2", "Integrate & deploy", ["E3B", "I2B", "I3B", "E1B"]),
    ("B3", "Measured tuning", ["M4", "W4"]),
    ("B4", "Endpoint gate", ["E4B"]),
]
GPU_GATED = {"I2B", "I3B", "E1B", "M4", "W4", "E4B"}
DONE = {"integrated", "implemented"}


def load():
    manifest = json.loads((PLAN / "tasks.json").read_text())
    tasks = {t["id"]: t for t in manifest["tasks"]}
    state = json.loads((EVID / "progress-state.json").read_text())
    return manifest, tasks, state


def parse(ts):
    return dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))


def cadence(state):
    hours = tasks = 0.0
    for w in state["cadence_windows"]:
        hours += (parse(w["end"]) - parse(w["start"])).total_seconds() / 3600
        tasks += w["tasks"]
    return (tasks / hours) if hours else 0.0, hours, tasks


def status_of(tid, tasks, state):
    if tid in state["in_progress"]:
        return "in-progress"
    s = tasks[tid]["status"]
    if s in DONE:
        return "done"
    return "remaining"


def build():
    manifest, tasks, state = load()
    backend = closure(tasks, ["E4B"])
    banded = {tid for _, _, ids in BANDS for tid in ids}
    other = sorted(backend - banded)  # foundations already integrated (F1, F2, E1, I1, D1 ...)
    rows, counts = [], {"done": 0, "in-progress": 0, "remaining": 0}
    for code, name, ids in BANDS:
        for tid in ids:
            if tid not in tasks:
                continue
            st = status_of(tid, tasks, state)
            counts[st] += 1
            rows.append((code, name, tid, tasks[tid]["title"], st, tasks[tid]["status"], tid in GPU_GATED))
    found = [tid for tid in other if tasks[tid]["status"] in DONE]
    rate, hours, n = cadence(state)
    remaining_local = [r for r in rows if r[4] != "done" and not r[6]]
    remaining_gpu = [r for r in rows if r[4] != "done" and r[6]]
    # projection: observed cadence (tasks/hour) at 4–6 lanes; wave-3 packages are larger and D is serial,
    # so show a range: optimistic = observed cadence, conservative = half of it.
    eta = {}
    if rate:
        opt = len(remaining_local) / rate
        eta = {"rate": rate, "window_hours": hours, "window_tasks": n,
               "local_optimistic_h": opt, "local_conservative_h": opt * 2,
               "serial_floor_h": 5 * 3.5 + 1 * 4}  # D1R→D2→D3→D4→D5 then E3B, ~3.5–4 h per loop observed
    return manifest, tasks, state, rows, counts, found, remaining_local, remaining_gpu, eta


def fmt_h(h):
    return f"~{h/24:.1f} days" if h >= 24 else f"~{h:.0f} h"


def render_md(m, tasks, state, rows, counts, found, rem_local, rem_gpu, eta):
    total = sum(counts.values())
    out = [f"# Backend-first progress tracker", "",
           f"Generated {state['updated']} from `tasks.json` (manifest v{m['schema_version']}) and `progress-state.json`. "
           f"Integration branch `{state['integration_branch']}`, base `{state['base']}`. Scope: the E4B backend closure "
           f"({len(closure(tasks, ['E4B']))} tasks, of which {len(found)} foundations and wave-2 modules are already merged and reused: {', '.join(found)}).", "",
           f"**Backend packages: {counts['done']} done · {counts['in-progress']} in progress · {counts['remaining']} remaining (of {total}).**", "",
           "| Band | Task | Title | Status | Manifest | Note |", "|---|---|---|---|---|---|"]
    for code, name, tid, title, st, ms, gpu in rows:
        note = state["in_progress"].get(tid, {}).get("note", "") if st == "in-progress" else ("needs allocated GPU/staging (P-04)" if gpu else "")
        out.append(f"| {code} {name} | {tid} | {title} | **{st}** | {ms} | {note} |")
    out += ["", "## Gates", ""]
    for g, v in state["gates"].items():
        out.append(f"- **{g}** (requires {v['requires']}): {v['state']}" + (f" — blocked by: {'; '.join(v['blocked_by'])}" if v["blocked_by"] else ""))
    out += ["", "## Inputs that block specific gates (not the coding)", "", "| Input | What | Blocks | Owner |", "|---|---|---|---|"]
    for i in state["inputs"]:
        out.append(f"| {i['id']} | {i['what']} | {i['blocks']} | {i['owner']} |")
    out += ["", "## ETA (provisional, cadence-based — not a commitment)", ""]
    if eta:
        out += [f"- Observed cadence: {eta['window_tasks']:.0f} tasks integrated in {eta['window_hours']:.1f} h of wall clock "
                f"({eta['rate']:.2f} tasks/h at 4–6 concurrent lanes, each task 2–4 review rounds), incl. two rate-limit interruptions.",
                f"- Local software to BACKEND-LOCAL/E3B and the software half of the rest ({len(rem_local)} packages): "
                f"{fmt_h(eta['local_optimistic_h'])} at observed cadence, {fmt_h(eta['local_conservative_h'])} if wave-3 packages run at half that rate "
                f"(they are larger and the D lane is serial); the serial critical path alone (D1R→D2→D3→D4→D5→E3B) is at least {fmt_h(eta['serial_floor_h'])}.",
                f"- GPU-gated packages ({', '.join(r[2] for r in rem_gpu)}): **no ETA until P-04 is allocated**; their software (harnesses, scripts, runbooks) proceeds inside the local estimate.",
                "- Continuous coordinator time is assumed; interruptions (rate limits, restarts) extend wall clock, not work."]
    out += ["", "## In flight", ""]
    for tid, v in state["in_progress"].items():
        out.append(f"- {tid}: {v['lane']} — {v['phase']} since {v['since']} — {v['note']}")
    for tid, v in state["reviews"].items():
        out.append(f"- review {tid}: {v['phase']} since {v['since']}")
    out += ["", "## Checkpoints", ""] + [f"- {c['at']}: {c['what']}" for c in state["checkpoints"]]
    out += ["", "## Authorizations", ""] + [f"- authorized: {a}" for a in state["authorizations"]] + [f"- NOT authorized: {a}" for a in state["not_authorized"]]
    return "\n".join(out) + "\n"


def render_html(m, tasks, state, rows, counts, found, rem_local, rem_gpu, eta):
    e = html.escape
    total = sum(counts.values())
    pct = lambda n: (100.0 * n / total) if total else 0
    band_rows = []
    for code, name, ids in BANDS:
        rs = [r for r in rows if r[0] == code]
        d = sum(r[4] == "done" for r in rs); p = sum(r[4] == "in-progress" for r in rs); q = len(rs) - d - p
        band_rows.append((code, name, d, p, q, rs))
    css = """
<title>Marlin Backend Tracker</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root{--ground:#F2F4F7;--panel:#FFFFFF;--ink:#1B2230;--muted:#5B6472;--line:#D6DBE3;--accent:#2F6DB5;--done:#2E7D4F;--prog:#B7791F;--rem:#8A93A0;--blk:#B33A3A;--tile:#E9EDF3}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--ground:#0F141B;--panel:#161D26;--ink:#E6EAF0;--muted:#9AA5B4;--line:#2A3441;--accent:#7FB0EA;--done:#5CBF85;--prog:#E0A94A;--rem:#6E7887;--blk:#E06B6B;--tile:#1D2733}}
:root[data-theme="dark"]{--ground:#0F141B;--panel:#161D26;--ink:#E6EAF0;--muted:#9AA5B4;--line:#2A3441;--accent:#7FB0EA;--done:#5CBF85;--prog:#E0A94A;--rem:#6E7887;--blk:#E06B6B;--tile:#1D2733}
body{background:var(--ground);color:var(--ink);font-family:"IBM Plex Sans",system-ui,sans-serif;padding-inline:16px;padding-block:24px 48px;line-height:1.45}
.wrap{max-width:1100px;margin:0 auto;display:grid;gap:28px}
h1{font-size:1.6rem;font-weight:600;margin:0;text-wrap:balance}h2{font-size:1.05rem;font-weight:600;margin:0 0 10px;letter-spacing:.01em}
.meta{color:var(--muted);font-size:.9rem;font-family:"IBM Plex Mono",ui-monospace,monospace}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.tile{background:var(--tile);border-radius:8px;padding:14px 16px}.tile b{display:block;font-size:1.9rem;font-weight:600;font-variant-numeric:tabular-nums;line-height:1.1}.tile span{color:var(--muted);font-size:.85rem;text-transform:uppercase;letter-spacing:.06em}
.tile.done b{color:var(--done)}.tile.prog b{color:var(--prog)}.tile.rem b{color:var(--rem)}.tile.blk b{color:var(--blk)}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:18px 20px}
.bar{display:flex;height:12px;border-radius:6px;overflow:hidden;background:var(--tile)}.bar i{display:block;height:100%}.bar .d{background:var(--done)}.bar .p{background:var(--prog)}.bar .r{background:var(--rem)}
.band{display:grid;grid-template-columns:150px 1fr auto;gap:12px;align-items:center;padding:8px 0;border-top:1px solid var(--line)}.band:first-of-type{border-top:0}
.band .n{font-weight:500}.band .c{font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:.85rem;color:var(--muted);white-space:nowrap}
table{width:100%;border-collapse:collapse;font-size:.9rem}th{text-align:left;color:var(--muted);font-weight:500;font-size:.78rem;text-transform:uppercase;letter-spacing:.06em;padding:8px 8px;border-bottom:1px solid var(--line)}td{padding:8px 8px;border-bottom:1px solid var(--line);vertical-align:top}
.scroll{overflow-x:auto}
.pill{display:inline-block;padding:2px 8px;border-radius:999px;font-size:.78rem;font-weight:500;letter-spacing:.02em;white-space:nowrap}
.pill.done{background:color-mix(in srgb,var(--done) 18%,transparent);color:var(--done)}.pill.prog{background:color-mix(in srgb,var(--prog) 20%,transparent);color:var(--prog)}.pill.rem{background:color-mix(in srgb,var(--rem) 18%,transparent);color:var(--muted)}.pill.gpu{background:color-mix(in srgb,var(--blk) 14%,transparent);color:var(--blk)}
code{font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:.85em}
ul{margin:0;padding-left:18px}li{margin:4px 0}
.eta p{margin:6px 0}.eta .big{font-size:1.15rem;font-weight:500}
.note{color:var(--muted);font-size:.85rem}
@media (max-width:600px){.band{grid-template-columns:1fr;gap:6px}}
</style>"""
    h = [css, '<div class="wrap">',
         f'<header><h1>Marlin backend — progress tracker</h1><p class="meta">generated {e(state["updated"])} · branch {e(state["integration_branch"])} · base {e(state["base"])} · manifest v{m["schema_version"]}</p></header>',
         '<section class="tiles">',
         f'<div class="tile done"><b>{counts["done"]}</b><span>done</span></div>',
         f'<div class="tile prog"><b>{counts["in-progress"]}</b><span>in progress</span></div>',
         f'<div class="tile rem"><b>{counts["remaining"]}</b><span>remaining</span></div>',
         f'<div class="tile blk"><b>{len(rem_gpu)}</b><span>GPU-gated</span></div>',
         f'<div class="tile"><b>{pct(counts["done"]):.0f}%</b><span>of {total} backend packages</span></div>',
         '</section>',
         '<section class="panel"><h2>Bands (E4B dependency closure)</h2>']
    for code, name, d, p, q, rs in band_rows:
        n = max(len(rs), 1)
        h.append(f'<div class="band"><div class="n">{code} · {e(name)}</div><div class="bar"><i class="d" style="width:{100*d/n:.1f}%"></i><i class="p" style="width:{100*p/n:.1f}%"></i><i class="r" style="width:{100*q/n:.1f}%"></i></div><div class="c">{d} done · {p} in progress · {q} left</div></div>')
    h.append(f'<p class="note">Foundations and wave-2 modules already merged and reused by the closure: {e(", ".join(found))}.</p></section>')
    h.append('<section class="panel"><h2>Packages</h2><div class="scroll"><table><thead><tr><th>Band</th><th>Task</th><th>Title</th><th>Status</th><th>Note</th></tr></thead><tbody>')
    for code, name, tid, title, st, ms, gpu in rows:
        cls = {"done": "done", "in-progress": "prog", "remaining": "rem"}[st]
        note = state["in_progress"].get(tid, {}).get("note", "") if st == "in-progress" else ""
        gpu_pill = ' <span class="pill gpu">needs GPU target</span>' if gpu and st != "done" else ""
        h.append(f'<tr><td>{code}</td><td><code>{e(tid)}</code></td><td>{e(title)}</td><td><span class="pill {cls}">{e(st)}</span>{gpu_pill}</td><td class="note">{e(note)}</td></tr>')
    h.append('</tbody></table></div></section>')
    h.append('<section class="panel eta"><h2>ETA — provisional, cadence-based</h2>')
    if eta:
        h.append(f'<p class="big">Local software (through E3B and the software half of the rest, {len(rem_local)} packages): {e(fmt_h(eta["local_optimistic_h"]))} at the observed cadence, {e(fmt_h(eta["local_conservative_h"]))} at half of it.</p>')
        h.append(f'<p>Observed cadence: {eta["window_tasks"]:.0f} tasks integrated in {eta["window_hours"]:.1f} h ({eta["rate"]:.2f} tasks/h at 4–6 concurrent lanes, each task 2–4 review rounds, two rate-limit interruptions included). Wave-3 packages are larger and the D lane is serial, hence the range; the serial critical path D1R→D2→D3→D4→D5→E3B alone is at least {e(fmt_h(eta["serial_floor_h"]))}.</p>')
        h.append(f'<p><b>GPU-gated packages ({e(", ".join(r[2] for r in rem_gpu))}): no ETA until P-04 is allocated.</b> Their harnesses, scripts and runbooks are built inside the local estimate; measurements wait for the target.</p>')
        h.append('<p class="note">Assumes continuous coordination. Interruptions extend wall clock, not work. This is a projection, not a commitment; it is regenerated at every checkpoint.</p>')
    h.append('</section>')
    h.append('<section class="panel"><h2>Gates and blocking inputs</h2><ul>')
    for g, v in state["gates"].items():
        h.append(f'<li><b>{e(g)}</b> (requires {e(v["requires"])}): {e(v["state"])}' + (f' — blocked by: {e("; ".join(v["blocked_by"]))}' if v["blocked_by"] else "") + '</li>')
    h.append('</ul><div class="scroll"><table><thead><tr><th>Input</th><th>What</th><th>Blocks</th><th>Owner</th></tr></thead><tbody>')
    for i in state["inputs"]:
        h.append(f'<tr><td><code>{e(i["id"])}</code></td><td>{e(i["what"])}</td><td>{e(i["blocks"])}</td><td>{e(i["owner"])}</td></tr>')
    h.append('</tbody></table></div></section>')
    h.append('<section class="panel"><h2>In flight now</h2><ul>')
    for tid, v in state["in_progress"].items():
        h.append(f'<li><code>{e(tid)}</code> — {e(v["lane"])} — {e(v["phase"])} since {e(v["since"])} — {e(v["note"])}</li>')
    for tid, v in state["reviews"].items():
        h.append(f'<li>review <code>{e(tid)}</code> — {e(v["phase"])} since {e(v["since"])}</li>')
    h.append('</ul></section><section class="panel"><h2>Checkpoints</h2><ul>')
    for c in state["checkpoints"]:
        h.append(f'<li><span class="meta">{e(c["at"])}</span> — {e(c["what"])}</li>')
    h.append('</ul></section><section class="panel"><h2>Authorizations</h2><ul>')
    for a in state["authorizations"]:
        h.append(f'<li>authorized: {e(a)}</li>')
    for a in state["not_authorized"]:
        h.append(f'<li>not authorized: {e(a)}</li>')
    h.append('</ul></section></div>')
    return "\n".join(h) + "\n"


def main():
    data = build()
    (EVID / "PROGRESS.md").write_text(render_md(*data))
    (EVID / "progress.html").write_text(render_html(*data))
    counts = data[4]
    print(f"done {counts['done']} · in progress {counts['in-progress']} · remaining {counts['remaining']}; wrote PROGRESS.md and progress.html")


if __name__ == "__main__":
    main()
