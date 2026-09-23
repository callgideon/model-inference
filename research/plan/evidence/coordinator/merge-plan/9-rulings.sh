#!/usr/bin/env bash
# Coordinator: number the proposed rulings in 08 §10 (next free R95), the lanes' exact text.
#   R95  M1-L2 - the media object store       (evidence/m/M1L2-eae07a9.md "Proposed ruling")
#   R96  E4B   - the published serving revision is the measured one  (E4B-9aa7ffe.md)
#   R97  E4B   - a certification counts at one clean SHA             (E4B-9aa7ffe.md)
#   R98  cutover - adapters from settings or refuse. Its text is the lane's DRAFT (uncommitted
#        research/plan/evidence/g/CUTOVER-351d084.md at analysis time): if the merged evidence
#        differs, replace CUTOVER below with the committed text before running.
#   M pilot-media proposed none at 078eefe; a later candidate takes R99.
# 08 §5/§5.1 rows: the cutover (INFRX_MODE, DATABASE_URL, VALKEY_URL, S3_MEDIA_BUCKET,
# DATABASE_POOL_*) and M1-L2 (S3_MEDIA_PREFIX, S3_ENDPOINT_URL) wrote their own; nothing to add.
set -euo pipefail
DAY=$(date -u +%F) python3 - <<'PY'
import os, pathlib
root = pathlib.Path("research/plan")
def flat(text):
    return " ".join(text.split())
m1 = (root / "evidence/m/M1L2-eae07a9.md").read_text()
m1 = m1.split("> **The media object store (M1-L2).** ", 1)[1].split("\n\n", 1)[0]
m1 = flat(m1.replace("\n> ", " "))
e4 = (root / "evidence/e/E4B-9aa7ffe.md").read_text()
e4 = e4.split("## Ruling candidates (proposed, not numbered; next free is R95)\n\n", 1)[1].split("\n\n", 1)[0]
a, b = [flat(item) for item in e4.split("\n- ")]
a_title, a_text = a.lstrip("- ").split("** ", 1)
b_title, b_text = b.split("** ", 1)
CUTOVER = ("`create_app` builds each adapter it is not given: D5's catalog, D4's journal and the "
           "job store on one pool from `DATABASE_URL`/`DATABASE_POOL_*`, and the object store from "
           "`S3_MEDIA_BUCKET`, which must answer HeadBucket (M1-L2). It never stages media in "
           "process memory outside a test that injects the store. An unset `S3_MEDIA_BUCKET`, or a "
           "store to build with no `DATABASE_URL`, refuses in every mode, naming the setting. An "
           "unset `INFRX_MODE` refuses to start (R44, completed).")
rows = [("R95", "The media object store (M1-L2 proposal)", m1),
        ("R96", a_title.strip("*").rstrip(".") + " (E4B proposal)", a_text),
        ("R97", b_title.strip("*").rstrip(".") + " (E4B proposal)", b_text),
        ("R98", "The gateway composes its durable adapters from settings or refuses (cutover proposal)", CUTOVER)]
p = root / "08-contracts-v1-encoding.md"
s = p.read_text()
anchor = next(l for l in s.splitlines(keepends=True) if l.startswith("| R94 |"))
assert s.count(anchor) == 1 and "| R95 |" not in s
assert all("|" not in text for _, _, text in rows)
s = s.replace(anchor, anchor + "".join(f"| {n} | {t} | {x} |\n" for n, t, x in rows))
assert s.rstrip().rsplit("\n## ", 1)[1].startswith("Verification log")
s = s.rstrip("\n") + "\n" + (f"- {os.environ['DAY']}: Rulings R95–R98 numbered at the merges (the lanes' proposed text): "
    "R95 M1-L2's media object store, R96–R97 E4B's two candidates, R98 the cutover's adapters-from-settings "
    "rule (R95 and R98 overlap by design: the cutover's refusal names M1-L2's store). Next free ruling R99.\n")
p.write_text(s)
PY
git add research/plan/08-contracts-v1-encoding.md
git commit -q -m "plan: 08 §10 rulings R95-R98 numbered at the merges (M1-L2 object store; E4B x2; the cutover's adapters from settings)"
