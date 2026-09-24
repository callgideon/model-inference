#!/usr/bin/env python3
"""Validate the documentation task graph and render its task ledger; no runtime claims."""
from __future__ import annotations
import argparse
import collections
import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[3]
PLAN = ROOT / 'research/plan'


def closure(tasks, roots):
    found = set()
    def visit(task_id):
        if task_id in found:
            return
        found.add(task_id)
        t = tasks[task_id]
        for dep in t.get('start_dependencies', []) + t.get('integration_dependencies', []):
            visit(dep)
    for task_id in roots:
        visit(task_id)
    return found


def markdown_destinations(source):
    """Read inline destinations including Next.js route names with parentheses."""
    source = re.sub(r'^```.*?^```', '', source, flags=re.M | re.S)   # code is not a link
    for match in re.finditer(r'\[[^\]\n]+\]\(', source):
        start = match.end()
        depth = 1
        pos = start
        while pos < len(source) and depth:
            char = source[pos]
            if char == '\\':
                pos += 2
                continue
            if char == '(':
                depth += 1
            elif char == ')':
                depth -= 1
            if depth:
                pos += 1
        if depth == 0:
            value = source[start:pos].strip()
            if value.startswith('<') and '>' in value:
                yield value[1:value.index('>')]
            else:
                yield value.split(' "', 1)[0]


def ledger(manifest, tasks):
    app = closure(tasks, ['E4', 'I2A'])
    backend = closure(tasks, ['E4B'])
    counts = collections.Counter(t['status'] for t in tasks.values())
    retired = sum(t['status'].startswith('superseded') for t in tasks.values())
    lines = ['# Complete task ledger', '',
             'Generated from [manifest v4](tasks.json) by `python3 research/plan/scripts/validate_plan.py --write-ledger`. Update the manifest only after evidence, then regenerate this file. Task status is separate from current dispatch priority.', '',
             f"**{len(tasks)} records; {len(tasks)-retired} active; {retired} retired; {counts['planned']} planned; {counts['implemented']} implemented; {counts['integrated']} integrated.** Original v1 statuses are preserved and do not establish product-v2 readiness. See [the audit](10-wave2-platform-audit.md).", '',
             '**Current scope:** complete the robust and measured Marlin endpoint backend first. The E4B dependency closure is the immediate implementation set; App/browser work follows backend acceptance and Lab follows App. See [backend-first handoffs](18-marlin-backend-first.md), [the full plan](12-complete-build-plan.md), [pending inputs](15-pending-inputs.md) and [fresh-session prompt](16-fresh-session-handoff.md).', '']
    groups = [
        ('Backend endpoint gate closure — current scope', lambda t: t['id'] in backend),
        ('App launch additions — after backend acceptance', lambda t: t['id'] in app and t['id'] not in backend),
        ('Later core platform work and preserved module baselines', lambda t: t['id'] not in app and t.get('execution_class') == 'core'),
        ('Conditional work — activation required', lambda t: t.get('execution_class') == 'conditional'),
        ('Retired mixed tasks — never dispatch', lambda t: t['status'].startswith('superseded')),
    ]
    for title, pred in groups:
        lines += ['## ' + title, '', '| ID | Status / owner | Deliverable / brief | Start dependencies | Real integration dependencies |', '|---|---|---|---|---|']
        for t in tasks.values():
            if not pred(t):
                continue
            brief = t.get('handoff', next(x['handoff'] for x in manifest['tracks'] if x['id'] == t['track']))
            lines += [f"| {t['id']} | {t['status']} / {t['track']} | [{t['title']}]({brief}) | {', '.join(t.get('start_dependencies', [])) or '—'} | {', '.join(t.get('integration_dependencies', [])) or '—'} |"]
        lines += ['']
    lines += ['## Scheduling rules', '',
              'Start edges require committed reviewed interfaces/fixtures; F2R/F2P/D1R additionally require their acceptance evidence. Integration edges require actual adapters and tests before integration status. Same-path tasks stay serial even if graph edges permit parallel development. D owns every migration; the coordinator owns common wiring and this manifest. New package slices are in their briefs; split oversized units before dispatch, preserving parent acceptance.', '',
              'Conditional task activation is specified in the manifest and [expansion gates](14-expansion-gates.md). G5 callbacks and I4 fleet are not baseline launch dependencies. LAB M2–M4 local gates differ from hosted/model-quality certification. Dates, reviewer identity, branch assignments and live results belong in appended coordinator evidence, not invented here.', '']
    return '\n'.join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--write-ledger', action='store_true', help='Regenerate the tracked Markdown ledger before checking it')
    args = ap.parse_args()
    manifest = json.loads((PLAN / 'tasks.json').read_text())
    rows = manifest['tasks']
    tasks = {t['id']: t for t in rows}
    errors = []
    if len(tasks) != len(rows):
        errors.append('Duplicate task IDs')
    if manifest['schema_version'] != 4:
        errors.append('Expected schema version 4')
    tracks = {t['id'] for t in manifest['tracks']}
    oracles = set(re.findall(r'^\| ([A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+) \|', (PLAN/'04-verification.md').read_text(), re.M))
    for t in rows:
        if t['track'] not in tracks:
            errors.append(f"Unknown owner for {t['id']}")
        if not (PLAN/t['handoff']).is_file():
            errors.append(f"Missing handoff for {t['id']}: {t['handoff']}")
        if t['status'].startswith('superseded'):
            for successor in t.get('replaced_by', []):
                if successor not in tasks or tasks[successor]['status'].startswith('superseded'):
                    errors.append(f"Invalid successor {t['id']} -> {successor}")
            continue
        for dep in t.get('start_dependencies', []) + t.get('integration_dependencies', []):
            if dep not in tasks or tasks[dep]['status'].startswith('superseded'):
                errors.append(f"Invalid dependency {t['id']} -> {dep}")
        for test_id in t.get('test_ids', []):
            if test_id not in oracles:
                errors.append(f"Undefined oracle {t['id']}: {test_id}")
        if t.get('execution_class') == 'conditional' and not t.get('activation'):
            errors.append(f"No activation condition for {t['id']}")
        if t.get('disposition') in ('complete-plan-addition', 'backend-first-addition'):
            for field in ('owned_paths', 'implementation_slices', 'acceptance', 'failure_oracle'):
                if not t.get(field):
                    errors.append(f"Missing {field}: {t['id']}")
    state = {}
    def visit(task_id, path):
        if state.get(task_id) == 1:
            errors.append('Dependency cycle: ' + ' -> '.join(path+[task_id]))
            return
        if state.get(task_id) == 2 or task_id not in tasks:
            return
        state[task_id] = 1
        t = tasks[task_id]
        for dep in t.get('start_dependencies', []) + t.get('integration_dependencies', []):
            visit(dep, path+[task_id])
        state[task_id] = 2
    for task_id in tasks:
        visit(task_id, [])
    # Preserve known audit completions; future statuses may progress, never regress.
    integrated = set('F1 F2 D1 I1 E1'.split())
    implemented = set('M1 Q1 W1 G1 T1 J1 C1 U1 V1 E2 S1'.split())
    for task_id in integrated:
        if tasks[task_id]['status'] != 'integrated':
            errors.append(f"Audit integrated evidence regressed: {task_id}")
    for task_id in implemented:
        if tasks[task_id]['status'] not in ('implemented', 'integrated'):
            errors.append(f"Audit implemented evidence regressed: {task_id}")
    if not errors:
        app = closure(tasks, ['E4', 'I2A'])
        backend = closure(tasks, ['E4B'])
        forbidden = {t['id'] for t in rows if t.get('product') == 'lab' or t['id'] in ('F3','D7','D8','D9','I5','I6','I7')}
        if app & forbidden:
            errors.append('App depends on later Lab: ' + ', '.join(sorted(app & forbidden)))
        frontend = set('A2 A3 C0 C3A U1 U1R U2 U3 E3A I2A I3 E4 V1M'.split())
        if backend & (forbidden | frontend):
            errors.append('Backend depends on frontend/Lab: ' + ', '.join(sorted(backend & (forbidden | frontend))))
        if not backend <= app:
            errors.append('App does not reuse the complete backend gate closure')
        if {'E4', 'I3', 'I2A', 'E3A'} & closure(tasks, ['I4']):
            errors.append('Conditional backend fleet still depends on frontend release')
        if manifest['current_execution_scope']['id'] != 'BACKEND-FIRST-MARLIN':
            errors.append('Current dispatch scope is not backend first')
        if 'E5L' in closure(tasks, ['E6L']):
            errors.append('Imported evaluation depends on trace observation gate')
        if {'P1','P2','P3','P4','E7L'} & closure(tasks, ['E8L']):
            errors.append('Rollout depends on training implementation')
    for gate, spec in manifest['release_gates'].items():
        for task_id in spec['requires']:
            if task_id not in tasks or tasks[task_id]['status'].startswith('superseded'):
                errors.append(f"Invalid release gate {gate}: {task_id}")
    coverage = (PLAN/'07-requirement-coverage.md').read_text()
    for prefix, maximum in [('BACKEND',10),('APP',13),('LAB',16)]:
        for i in range(1,maximum+1):
            if not re.search(r'^\| '+prefix+f'-{i:02d}'+r'\b',coverage,re.M):
                errors.append(f'Missing requirement mapping: {prefix}-{i:02d}')
    if errors:
        print('\n'.join('ERROR: '+e for e in errors))
        return 1
    rendered = ledger(manifest, tasks)
    target = PLAN/'17-task-ledger.md'
    if args.write_ledger:
        target.write_text(rendered)
    if not target.exists() or target.read_text() != rendered:
        errors.append('Task ledger is stale; run --write-ledger')
    # Verify local Markdown destinations, including historical evidence; headings are left to Markdown renderers.
    files = list(PLAN.rglob('*.md')) + list((ROOT/'research/platforms').glob('*.md'))
    files += [ROOT/p for p in ['README.md','HANDOFF.md','CLAUDE.md','apps/app/README.md','apps/lab/README.md','apps/infrx-api/README.md']]
    links = 0
    for f in files:
        for dest in markdown_destinations(f.read_text()):
            if re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*:',dest) or dest.startswith('#'):
                continue
            dest = unquote(dest.split('#')[0])
            links += 1
            if dest and not (f.parent/dest).exists():
                errors.append(f'Broken link in {f.relative_to(ROOT)}: {dest}')
    if errors:
        print('\n'.join('ERROR: '+e for e in errors))
        return 1
    print(f'PASS: {len(rows)} tasks, acyclic combined dependencies, preserved audit statuses, defined oracles and valid release gates.')
    print(f'PASS: backend closure {len(backend)} tasks excludes frontend/Lab and is reused by App; fleet independent of UI.')
    print(f'PASS: App closure {len(app)} tasks excludes Lab; E6L independent of E5L; E8L independent of training.')
    print(f'PASS: 10 backend + 13 App + 16 Lab requirements mapped; {links} local Markdown links checked across {len(files)} documents; ledger current.')
    return 0

if __name__ == '__main__':
    sys.exit(main())
