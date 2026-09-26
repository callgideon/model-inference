#!/usr/bin/env python3
"""P25-ENACT fix round: the two runbook figures P-25 (decided 2026-09-25) sets, read from the
runbooks the coordinator follows and, for the prune, run.

    uv run --frozen pytest -q tests/w/test_p25_runbooks.py

Not in `test_worker_main.py`: that suite's mutation list must cover every case, and the shared
runner compiles each mutated file as Python, so a Markdown anchor is `broken_runner`. The
fails-before runs (both cases against the pre-fix runbooks) are in the P25-ENACT evidence.
"""
from __future__ import annotations

import os
import pathlib
import re
import subprocess

REPO = pathlib.Path(__file__).resolve().parents[4]


def runbook_section(name: str, heading: str) -> str:
    """One `infra/runbooks/<name>` section, from its heading to the next one."""
    text = (REPO / "infra" / "runbooks" / name).read_text()
    return re.split(r"\n#{2,3} ", text.split(f"\n{heading}\n", 1)[1], maxsplit=1)[0]


def test_worker_main__the_known_good_record_passes_every_install_name():
    """P-25's known-good record runs known-good.py's `config` check with every tunable name
    the install passes (section 1's INFRX_SET, plus ENGINE_MAX_NUM_SEQS, which 50-install
    adds), and `--bundles`; RETENTION_GRACE_S stays out. Oracle: a record command with no
    `--set`, whose config check passes vacuously (known-good.py compares only the --set
    names), or one that drifts from the install."""
    text = (REPO / "infra" / "runbooks" / "rollout.md").read_text()
    installed = re.search(r'INSTALL_ARGS=\(.*?INFRX_SET="([^"]*)"', text, re.S).group(1)
    names = {pair.partition("=")[0] for pair in installed.split()} | {"ENGINE_MAX_NUM_SEQS"}
    command, = re.findall(r"```bash\n(.*?)```",
                          runbook_section("rollout.md", "### Known-good record"), re.S)
    assert "known-good.py" in command and "--bundles" in command
    assert set(re.findall(r"--set (\w+)", command)) == names
    assert "RETENTION_GRACE_S" not in names


def test_worker_main__the_dump_cadence_keeps_the_7_newest(tmp_path):
    """P-25 (dump cadence while PITR is off) is A9's "longer period": the section's prune
    replaces A9's post-A8 removal and keeps the 7 newest dumps. Oracle: A9's removal left
    in force (the 7 are never held), no prune (dumps pile up), or a prune that keeps the
    oldest or removes a dump while 7 or fewer remain."""
    cadence = runbook_section("restore.md", "### Dump cadence while PITR is off")
    assert "A9" in cadence and "longer period" in cadence
    prune, = re.findall(r"```bash\n(.*?)```", cadence, re.S)
    backups = tmp_path / "infrx-backups"
    stamps = [f"hosted-202609{day:02d}T030000Z" for day in range(1, 11)]
    for stamp in stamps:
        (backups / stamp).mkdir(parents=True)
    for _ in range(2):                                   # the second run removes nothing
        subprocess.run(["bash", "-euo", "pipefail", "-c", prune], check=True,
                       capture_output=True, env={**os.environ, "HOME": str(tmp_path)})
        assert sorted(p.name for p in backups.iterdir()) == stamps[-7:]
