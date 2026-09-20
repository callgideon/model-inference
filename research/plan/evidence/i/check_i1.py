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
import hashlib
import pathlib
import re
import subprocess
import sys

EV_PATH = "research/plan/evidence/i/I1-4e052f4.md"
RM_PATH = "infra/README.md"
DEFAULT_RAW = "/tmp/claude-1000/i1-converge"

ID = r"[OM]-[A-Z0-9]+(?:-[A-Z0-9]+)*"
ID_DEF = re.compile(r"^\| \*\*`(" + ID + r")`\*\*", re.M)
ID_USE = re.compile(r"`(" + ID + r")`")
ID_BARE = re.compile(r"\b(" + ID + r")\b")          # also catches un-backticked ids
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
    bare = set(ID_BARE.findall(ev_claims)) | set(ID_BARE.findall(rm_claims))
    undefined = sorted((set(used_ev) | set(used_rm) | bare) - set(defined))
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
            num = re.match(r"\d+(?:\.\d+)?", tail)
            if num:                                     # numeric: exact match only
                if num.group(0) in known:
                    continue
                if re.match(r"6\.[1-7]$", num.group(0)):  # finding item inside §6
                    continue
                if num.group(0) == "1.1":               # cloud-pricing.md §1.1
                    continue
                unresolved.append(f"{doc}:§{num.group(0)}")
                continue
            if any(tail.startswith(k) for k in known):
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
        empty = {"", "—", "-", "n/a", "N/A", "tbd", "TBD", "?"}
        if (len(cells) != 7 or cells[4].strip() in empty
                or cells[5].strip() in empty):
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

    # 7b. git-derived facts: which paths the branch touched, and whether the SHA
    #     §Source states is the newest commit that touched the design document.
    def git(*a: str) -> str | None:
        try:
            r = subprocess.run(("git", *a), cwd=root, capture_output=True,
                               text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return None
        return r.stdout.strip() if r.returncode == 0 else None

    if git("rev-parse", "--git-dir") is None:
        out.append("git: not a git checkout — paths touched and SHA freshness not checked")
    else:
        base = re.search(r"\| Base SHA \| `([0-9a-f]{7,40})`", ev)
        if base:
            names = git("diff", "--name-only", f"{base.group(1)}..HEAD") or ""
            out.append(f"git diff --name-only {base.group(1)}..HEAD: "
                       + (", ".join(names.split("\n")) if names else "(no paths)"))
            owned = all(n.startswith("infra/") or n.startswith("research/plan/evidence/i/")
                        for n in names.split("\n") if n)
            check(owned, "branch touched owned paths only")
        else:
            check(False, "§Source states a base SHA")
        head_rm = git("log", "-1", "--format=%h", "--", RM_PATH)
        if impl and head_rm:
            out.append(f"git log -1 --format=%h -- {RM_PATH}: {head_rm}")
            check(head_rm.startswith(impl.group(1)) or impl.group(1).startswith(head_rm),
                  "§Source implementation SHA is the newest commit touching the design",
                  f"§Source {impl.group(1)} vs git {head_rm}")

    # 8. claims ledger: how many claim-bearing lines each document carries, and
    #    how many of them assert something OBSERVED. The design document should
    #    trend towards zero: observed facts belong to the evidence report.
    classes = ("OBSERVED", "HISTORICAL CLAIM", "PROPOSED", "DERIVED", "CORRECTED")
    # fences stripped: the §Checks block quotes this output, so counting it would
    # make the quoted numbers change every time they are quoted
    for doc, text in (("evidence", ev_claims), ("README", rm_claims)):
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
        manifest_bytes = manifest.read_bytes()
        lines = [l for l in manifest_bytes.decode().split("\n") if l.strip()]
        names = [l.split(None, 1)[1].strip() for l in lines]
        digests = {pathlib.PurePath(l.split(None, 1)[1].strip()).name: l.split()[0]
                   for l in lines}
        basenames = [pathlib.PurePath(n).name for n in names]
        dup = sorted({n for n in basenames if basenames.count(n) > 1})
        files = sorted(p.name for p in rawfiles.glob("*.txt"))
        manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
        out.append(f"raw evidence: {len(files)} files, {len(lines)} manifest lines, "
                   f"{len(set(basenames))} distinct names"
                   + (f", DUPLICATED: {', '.join(dup)}" if dup else ""))
        out.append(f"  manifest sha256: {manifest_sha}")
        mismatched = []
        for name, want in digests.items():
            f = rawfiles / name
            if f.exists() and hashlib.sha256(f.read_bytes()).hexdigest() != want:
                mismatched.append(name)
        check(not mismatched, "every manifest digest matches its raw file",
              str(mismatched))
        quoted = set(re.findall(r"`([0-9a-f]{64})`", ev_claims))
        unknown = sorted(quoted - set(digests.values()) - {manifest_sha})
        out.append(f"  sha256 digests quoted in prose: {len(quoted)}")
        check(not unknown, "every quoted digest is in the manifest", str(unknown))
        check(not dup, "manifest lists each raw file once", str(dup))
        check(len(files) == len(set(basenames)),
              "manifest covers every raw file exactly once",
              f"{len(files)} files vs {len(set(basenames))} names")
        missing_files = sorted(set(basenames) - set(files))
        check(not missing_files, "every manifest entry has a file", str(missing_files))

    # 10. the §Checks block must quote THIS output. Everything above is compared to
    #     the quoted block, minus the timestamp line, this check's own line and the
    #     trailing summary — so a stale quote fails the run instead of being believed.
    quoted_block = None
    if "## Checks" in ev:
        after = ev.split("## Checks", 1)[1]
        if after.count("```") >= 2:
            quoted_block = after.split("```")[1].strip("\n").split("\n")
    if quoted_block is None:
        check(False, "§Checks quotes this script's output", "no fenced block found")
    else:
        actual = []
        for line in quoted_block:
            if line.startswith("$ ") or re.match(r"^\d+$", line):
                continue                                  # command line, exit code
            if re.match(r"^\[(PASS|FAIL)\] quoted §Checks block", line):
                continue                                  # this check's own line
            if re.match(r"^\d+ failed check\(s\)", line) or not line.strip():
                continue                                  # trailing summary
            actual.append(line)
        expected = [l for l in out if l.strip()]
        first = next((f"line {i + 1}: quoted {a!r} vs fresh {b!r}"
                      for i, (a, b) in enumerate(zip(actual, expected)) if a != b),
                     "" if len(actual) == len(expected)
                     else f"{len(actual)} quoted lines vs {len(expected)} fresh")
        check(not first, "quoted §Checks block matches a fresh run", first)

    print("\n".join(out))
    print(f"\n{len(failures)} failed check(s)"
          + (": " + "; ".join(failures) if failures else ""))
    return len(failures)


if __name__ == "__main__":
    sys.exit(main())
