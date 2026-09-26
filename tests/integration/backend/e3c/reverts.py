"""E3C revert-type negative controls that remove ONE check from one SQL function.

    python3 tests/integration/backend/e3c/reverts.py <tree> <nc-id>   # control_trees.sh

The scratch tree gets a last migration `9999_e3c_<nc>.sql`: the function's LATEST definition
in that tree's migrations with the one check changed (`REVERTS`). The anchor must occur
exactly as often as written, or nothing is written and the exit is 1 - a moved check is a
hard stop, never a partially reverted tree. Immutable 0001-0025 stay untouched; the
scratch tree is the only place such a migration ever exists.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

MIGRATIONS = Path("apps/app/supabase/migrations")
#: nc-id -> (function, anchor, replacement, occurrences, what the control removes)
REVERTS = {
    "nc-dur-fence": (
        "fence_lease", "if a.generation is distinct from (p_lease->>'generation')::int then",
        "if false then", 1,
        "DUR-FENCE (s14): 0016 fence_lease's generation check; the owner check stays, so only "
        "a stale lease of the SAME worker id passes (s14 same-process)"),
    "nc-dur-cap": (
        "admission_checks", "if v_count >= (p_limits->>'max_active_jobs",
        "if v_count > (p_limits->>'max_active_jobs", 3,
        "DUR-CAP (s15): 0011 admission_checks's three active-job cap comparisons off by one "
        "(>= becomes >): total, per organization and per key each admit one more"),
    "nc-credit-rate": (
        "terminalize", "infrx.debit_credit(j.rate_card_version, v_in, v_out)",
        "infrx.debit_credit((select l.rate_card_version from infrx.catalog_listings l where "
        "l.public_model_id = split_part(j.requested_model, '@', 1) order by l.version desc "
        "limit 1), v_in, v_out)", 1,
        "CREDIT-RATE (s16): 0018 terminalize debits at the alias's CURRENT listing's card, "
        "not the admitted one (E3B db13's defect)"),
}


def latest(root: Path, function: str) -> str | None:
    """The last `create or replace function infrx.<function>(` body in the tree."""
    found = None
    pattern = re.compile(rf"create or replace function infrx\.{function}\(.*?\nend \$\$;", re.S)
    for path in sorted((root / MIGRATIONS).glob("*.sql")):
        for match in pattern.finditer(path.read_text()):
            found = match.group(0)
    return found


def redefined(root: Path, nc: str) -> str:
    """The migration text for `nc` on the tree at `root`; SystemExit when it cannot apply."""
    function, anchor, replacement, occurrences, what = REVERTS[nc]
    source = latest(root, function)
    count = source.count(anchor) if source else 0
    if count != occurrences:
        raise SystemExit(f"{nc}: infrx.{function} has the anchor {count} times, not "
                         f"{occurrences}: the control no longer describes this tree")
    return (f"-- E3C {nc} (a scratch tree only, never a real migration): {what}.\n"
            f"{source.replace(anchor, replacement)}\n")


def main(argv: list[str]) -> int:
    root, nc = Path(argv[1]), argv[2]
    text = redefined(root, nc)
    (root / MIGRATIONS / f"9999_e3c_{nc.replace('-', '_')}.sql").write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
