#!/usr/bin/env python3
"""Derive every count and consistency check for the I1 deliverables.

The two I1 documents state no number, SHA or "check passed" claim in prose: they
quote this script's output. Run it from the repository root:

    python3 research/plan/evidence/i/check_i1.py [--raw <scratch dir>]

Standard library only, no network, no writes: it reads
`infra/README.md`, `research/plan/evidence/i/I1-4e052f4.md` and, when the
session-local scratch directory still exists, its raw-output manifest.

Exit status 0 = every check passed. Non-zero = the number of failed checks.
A missing scratch directory is reported, not failed: scratch is session-local
and is expected to be gone once the session ends, which is why provisioning a
durable evidence store is a matrix row of its own.
"""

from __future__ import annotations

import argparse
import collections
import pathlib
import re
import sys

EV_PATH = "research/plan/evidence/i/I1-4e052f4.md"
RM_PATH = "infra/README.md"
DEFAULT_RAW = "/tmp/claude-1000/i1-converge"

ID_DEF = re.compile(r"^\| \*\*`([OM]-[A-Z0-9]+)`\*\*", re.M)
ID_USE = re.compile(r"`([OM]-[A-Z0-9]+)`")
CMD_ROW = re.compile(r"^\| ([1-9]) \| (\d\d:\d\d:\d\d) \|", re.M)
# mutating verbs, as the acceptance criterion words them
VERB = re.compile(
    r"\b(creates?|enables?|attach(?:es)?|restricts?|rotates?|migrat\w+|provisions?"
    r"|replaces?|revokes?|swaps?|flips?|lowers?|sets?|moves?|deletes?|drops?|writes?)\b",
    re.I,
)
EXEMPT = re.compile(
    r"`M-[A-Z0-9]+`|separately authorized|coordinator action|contingenc"
    r"|not proposed|PROPOSED|read-only|I1 (?:created|performs) nothing",
    re.I,
)


def headings(text: str) -> set[str]:
    """Section names a `§x` reference may resolve to."""
    out: set[str] = set()
    for line in text.split("\n"):
        m = re.match(r"^#{2,4} (\d+(?:\.\d+)?)[\.\s]", line)
        if m:
            out.add(m.group(1))
        m = re.match(r"^## ([A-Za-z].*)$", line)
        if m:
            out.add(m.group(1).split(" —")[0].strip())
    return out


def numbered_items(text: str, start: str, end: str) -> set[str]:
    body = text.split(start)[1].split(end)[0]
    return set(re.findall(r"^(\d+)\. ", body, re.M))


def table_column_mismatches(text: str) -> list[tuple[int, int, int]]:
    """Rows whose column count differs from the first row of their own table."""
    bad, expected = [], None
    for lineno, line in enumerate(text.split("\n"), 1):
        if not line.startswith("|"):
            expected = None
            continue
        cells = line.replace(r"\|", "").count("|")
        stripped = line.replace("|", "").replace(" ", "")
        if expected is None:
            expected = cells
        elif set(stripped) <= set("-:"):
            continue
        elif cells != expected:
            bad.append((lineno, cells, expected))
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--raw", default=DEFAULT_RAW)
    args = ap.parse_args()
    root = pathlib.Path(args.root).resolve()
    ev = (root / EV_PATH).read_text()
    rm = (root / RM_PATH).read_text()
    # §Checks quotes this script's own output. That block is machine output, not a
    # claim either document makes, so it is excluded from the scans that look for
    # stated times, SHAs and section references — otherwise the report could never
    # quote a run without failing the run it quotes.
    fence = re.compile(r"^```.*?^```", re.M | re.S)
    ev_claims = fence.sub("", ev)
    rm_claims = fence.sub("", rm)
    out: list[str] = []
    failures: list[str] = []

    def check(ok: bool, label: str, detail: str = "") -> None:
        out.append(f"[{'PASS' if ok else 'FAIL'}] {label}{(': ' + detail) if detail else ''}")
        if not ok:
            failures.append(label)

    # 1. §Commands — one table, rows per pass
    rows = CMD_ROW.findall(ev)
    per_pass = collections.Counter(p for p, _ in rows)
    tables = ev.split("## Commands")[1].split("## Results")[0].count("| Pass | UTC |")
    out.append(f"command rows: {len(rows)} total, by pass "
               + ", ".join(f"{k}={per_pass[k]}" for k in sorted(per_pass)))
    check(tables == 1, "commands table is single", f"{tables} header row(s)")
    ordered = rows == sorted(rows, key=lambda r: (r[0], r[1]))
    check(ordered, "command rows ordered by pass then UTC")
    cited_times = set(re.findall(r"\b(\d\d:\d\d:\d\d)Z", ev_claims)) | set(
        re.findall(r"\b(\d\d:\d\d:\d\d)Z", rm_claims))
    declared = {t for _, t in rows}
    # Times a document states on purpose that no command row can back, each because
    # the report says so where it uses them. Printed, not hidden, so a reviewer sees
    # the whole exemption set rather than trusting the check.
    documented = {
        "18:27:00": "pass 2, unitemised call (re-observed in pass 3)",
        "18:28:03": "pass 2, unitemised call (re-observed in pass 3)",
        "18:29:06": "pass 2, unitemised call (re-observed in pass 3)",
        "19:42:21": "local pytest attempt, not an AWS call",
        "18:02:53": "CloudTrail DescribeAlarmHistory, read-only, unattributed",
        "18:02:59": "CloudTrail DescribeRouteTables, read-only, unattributed",
    }
    out.append("cited times with no command row, documented: "
               + "; ".join(f"{k} ({v})" for k, v in sorted(documented.items())))
    orphans = sorted(cited_times - declared - set(documented))
    check(not orphans, "every cited UTC time has a command row or a documented "
                       "exemption", str(orphans))

    # 2. row ids
    defined = collections.Counter(ID_DEF.findall(ev))
    dups = sorted(k for k, v in defined.items() if v > 1)
    used_ev = collections.Counter(ID_USE.findall(ev))
    used_rm = collections.Counter(ID_USE.findall(rm))
    undefined = sorted((set(used_ev) | set(used_rm)) - set(defined))
    # "cited" = referenced somewhere other than its own defining cell
    def_lines = {m.group(1): ev[:m.start()].count("\n") for m in ID_DEF.finditer(ev)}
    cited = set()
    for rid in defined:
        elsewhere = used_ev[rid] - 1 + used_rm[rid]  # minus the defining cell itself
        if elsewhere > 0:
            cited.add(rid)
    uncited = sorted(set(defined) - cited)
    out.append(f"row ids: {len(defined)} defined, {len(cited)} cited elsewhere, "
               f"{len(uncited)} defined, uncited (inventory-only)")
    out.append("  defined, uncited (inventory-only): " + (", ".join(uncited) or "none"))
    check(not dups, "row ids unique", str(dups))
    check(not undefined, "every cited row id is defined", str(undefined))
    rm_ids = sorted(set(used_rm))
    out.append(f"  ids the design document relies on: {len(rm_ids)} "
               f"({', '.join(rm_ids)})")

    # 3. section / item / link references
    hev, hrm = headings(ev), headings(rm)
    known = sorted(hev | hrm, key=len, reverse=True)
    unresolved = []
    for doc, text in (("evidence", ev_claims), ("README", rm_claims)):
        for m in re.finditer("§", text):
            tail = text[m.end():m.end() + 70]
            if any(tail.startswith(k) for k in known):
                continue
            if re.match(r"6\.[1-7]\b", tail):           # finding item inside §6
                continue
            if re.match(r"1\.1\b", tail):               # cloud-pricing.md §1.1
                continue
            if tail.startswith('"AWS access'):          # quoted CLAUDE.md section name
                continue
            if tail.startswith("Deploy design"):        # quoted, withdrawn reference
                continue
            if re.match(r"…|Verification\s*\n|Commands\s*\n|Source\s*\n", tail):
                continue                                # line-wrapped heading name
            unresolved.append(f"{doc}:{tail[:40]!r}")
    check(not unresolved, "every section reference resolves", str(unresolved))
    lim = numbered_items(ev, "## Limits", "## Handback")
    hb = numbered_items(ev, "## Handback", "## Verification log")
    lim_missing = sorted(set(re.findall(r"Limits item (\d+)", ev + rm)) - lim)
    hb_missing = sorted(set(re.findall(r"handback item (\d+)", ev + rm)) - hb)
    out.append(f"Limits items: {len(lim)}; handback items: {len(hb)}")
    check(not lim_missing, "every Limits item reference resolves", str(lim_missing))
    check(not hb_missing, "every handback item reference resolves", str(hb_missing))
    links = re.findall(r"\]\((?!http)([^)#]+?)\)", ev) + re.findall(
        r"\]\((?!http)([^)#]+?)\)", rm)
    missing = []
    for link in links:
        for base in (root, root / "infra", root / "research/plan/evidence/i"):
            if (base / link).exists():
                break
        else:
            missing.append(link)
    out.append(f"relative links: {len(links)}")
    check(not missing, "every relative link exists", str(missing))

    # 4. tables
    for doc, text in (("evidence", ev), ("README", rm)):
        bad = table_column_mismatches(text)
        check(not bad, f"{doc} table columns consistent", str(bad[:5]))

    # 5. matrix: one owner, one environment, an id per row
    matrix = ev.split("### 5. Required vs existing")[1].split("### 6.")[0]
    mrows = [l for l in matrix.split("\n")
             if l.startswith("| ") and "---" not in l and not l.startswith("| Resource")]
    noid, noown = [], []
    for row in mrows:
        cells = row.split("|")
        if not re.match(r"\s*\*\*`M-[A-Z0-9]+`\*\*", cells[1]):
            noid.append(cells[1].strip()[:40])
        if len(cells) != 7 or not cells[4].strip() or not cells[5].strip():
            noown.append(cells[1].strip()[:40])
    out.append(f"matrix rows: {len(mrows)}")
    check(not noid, "every matrix row carries an id", str(noid))
    check(not noown, "every matrix row has one owner and one environment", str(noown))

    # 6. mutable operations in the design reconcile to the matrix
    body = rm[:rm.index("## Verification log")]
    offenders = []
    for para in re.split(r"\n\s*\n", body):
        if not VERB.search(para) or EXEMPT.search(para):
            continue
        if para.lstrip().startswith("#"):                # heading
            continue
        if para.lstrip().startswith("- ") or re.match(r"\s*\d+\. ", para):
            continue                                     # continuation of a cited step
        if re.search(r"two-deploy change|never one", para):
            continue                                     # a rule, not an operation
        offenders.append(" ".join(para.split())[:80])
    check(not offenders, "every mutable operation in the design cites a matrix row",
          str(offenders))

    # 7. implementation SHA stated once, in §Source
    source = ev.split("## Source")[1].split("## Requirement coverage")[0]
    shas = re.findall(r"`([0-9a-f]{7})`", source)
    impl = re.search(r"\| Implementation SHA \| \*\*`([0-9a-f]{7})`\*\*", source)
    if impl:
        elsewhere = (ev_claims.count(impl.group(1)) - source.count(impl.group(1))
                     + rm_claims.count(impl.group(1)))
        out.append(f"implementation SHA in §Source: {impl.group(1)} "
                   f"(occurrences outside §Source: {elsewhere})")
        check(elsewhere == 0, "implementation SHA stated only in §Source")
    else:
        check(False, "§Source states an implementation SHA")

    # 8. claims ledger: how many claim-bearing lines each document carries, and
    #    how many of them assert something OBSERVED. The design document should
    #    trend towards zero: observed facts belong to the evidence report.
    classes = ("OBSERVED", "HISTORICAL CLAIM", "PROPOSED", "DERIVED", "CORRECTED")
    for doc, text in (("evidence", ev), ("README", rm)):
        body = text[:text.index("## Verification log")]
        claim_lines = [l for l in body.split("\n")
                       if l.strip() and set(l.strip()) - set("|- ")]
        classed = [l for l in claim_lines if any(c in l for c in classes)]
        observed = [l for l in classed if "OBSERVED" in l]
        out.append(f"claims ledger, {doc} (excluding its log): {len(claim_lines)} "
                   f"claim-bearing lines, {len(classed)} class-labelled, "
                   f"{len(observed)} OBSERVED-bearing")

    # 9. raw-output manifest (session-local; reported, not failed, when absent)
    raw_dir = pathlib.Path(args.raw)
    manifest, rawfiles = raw_dir / "digests.txt", raw_dir / "raw"
    if not manifest.exists() or not rawfiles.is_dir():
        out.append(f"raw evidence: absent at {raw_dir} "
                   "(session-local scratch; expected once the session ends)")
    else:
        lines = [l for l in manifest.read_text().split("\n") if l.strip()]
        names = [l.split(None, 1)[1].strip() for l in lines]
        basenames = [pathlib.PurePath(n).name for n in names]
        dup = sorted({n for n in basenames if basenames.count(n) > 1})
        files = sorted(p.name for p in rawfiles.glob("*.txt"))
        out.append(f"raw evidence: {len(files)} files, {len(lines)} manifest lines, "
                   f"{len(set(basenames))} distinct names"
                   + (f", DUPLICATED: {', '.join(dup)}" if dup else ""))
        check(not dup, "manifest lists each raw file once", str(dup))
        check(len(files) == len(set(basenames)),
              "manifest covers every raw file exactly once",
              f"{len(files)} files vs {len(set(basenames))} names")
        missing_files = sorted(set(basenames) - set(files))
        check(not missing_files, "every manifest entry has a file", str(missing_files))

    print("\n".join(out))
    print(f"\n{len(failures)} failed check(s)"
          + (": " + "; ".join(failures) if failures else ""))
    return len(failures)


if __name__ == "__main__":
    sys.exit(main())
