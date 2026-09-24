#!/usr/bin/env bash
# Coordinator (2026-09-24), after 9-rulings.sh: the remaining candidates get numbers, in the
# lanes' exact text. R99 = M pilot-media's "Upload references at use and the durable attach"
# (evidence/m/MPILOT-078eefe.md, the "Coordinator — proposed ruling" bullet); R100–R103 = D5's
# (i)–(iv) (evidence/d/D5-4bcac3b.md "Ruling candidates"). D5's platform_cancelled cancel cause
# (addendum 2) is NOT numbered: it is an unimplemented contract addition (pending input).
set -euo pipefail
DAY=$(date -u +%F) python3 - <<'PY'
import os, pathlib, re
root = pathlib.Path("research/plan")
def flat(text):
    return " ".join(text.split())
m = (root / "evidence/m/MPILOT-078eefe.md").read_text()
start = m.index("* **Coordinator — proposed ruling")
block = m[start:].split("\n* ", 1)[0].split("\n\n", 1)[0]
head, body = block.split(":** *", 1)
title, text = body.split(".* ", 1)
m_title, m_text = flat(title), flat(text)
assert m_text.startswith("(a) An `infrx-upload:") and "(c) `attach` is" in m_text, m_text[:80]
d = (root / "evidence/d/D5-4bcac3b.md").read_text()
cand = d.split("- **Ruling candidates.**\n", 1)[1].split("\n- **", 1)[0]
items = [flat(l.strip()[2:]) for l in cand.splitlines() if l.strip().startswith("- (")]
assert len(items) == 4 and [i[:5] for i in items] == ["(i) A", "(ii) ", "(iii)", "(iv) "], items
d_rows = [("R100", "A negative operator adjustment is a store-made compensating entry (D5 candidate i)", items[0]),
          ("R101", "Private catalog naming is <provider slug>/<endpoint name>-<env> (D5 candidate ii)", items[1]),
          ("R102", "The data-access policy defaults are consent version 1 and trace off (D5 candidate iii)", items[2]),
          ("R103", "USD admin grants stay on the legacy writer, never grant_credit (D5 candidate iv)", items[3])]
rows = [("R99", m_title + " (M pilot-media proposal)", m_text), *d_rows]
p = root / "08-contracts-v1-encoding.md"
s = p.read_text()
anchor = next(l for l in s.splitlines(keepends=True) if l.startswith("| R98 |"))
assert s.count(anchor) == 1 and "| R99 |" not in s
assert all("|" not in text for _, _, text in rows)
s = s.replace(anchor, anchor + "".join(f"| {n} | {t} | {x} |\n" for n, t, x in rows))
old = "Next free ruling R99.\n"
assert s.count(old) == 1
s = s.replace(old, "Next free ruling R99.\n" + f"- {os.environ['DAY']}: Rulings R99–R103 numbered (the lanes' proposed text): "
    "R99 M pilot-media's upload references at use and the durable attach; R100–R103 D5's candidates (i)–(iv). "
    "D5's platform_cancelled cancel cause stays a pending input (unimplemented). Next free ruling R104.\n")
p.write_text(s)
PY
git add research/plan/08-contracts-v1-encoding.md
git commit -q -m "plan: 08 §10 rulings R99-R103 numbered (M pilot-media's upload references and durable attach; D5's four candidates)"
