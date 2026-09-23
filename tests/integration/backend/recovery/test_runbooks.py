"""I3B.c: the runbooks are the procedures the drills ran, and they stay that way. Layer 1.

* every alert rule names a runbook section that exists;
* restore.md drives `pgrestore.py` (the tool bk01/bk02 drill) through subcommands it has,
  with the pinned client image E2's stack runs;
* rollback.md's maintenance statement is bk04's, character for character, and its step 4
  runs I2B's rollback.sh as the script documents it (rc10 drills the procedure);
* every `bash` step block parses (a step that cannot run over SSM is not a step);
* every relative link and anchor between runbooks resolves;
* a failed restore client never re-raises row data (RS-4), and a timed-out one never
  re-raises its argv.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import recoverykit as kit                               # noqa: E402

import harness                                          # noqa: E402
import test_restore                                     # noqa: E402

RUNBOOKS = kit.ROOT / "infra" / "runbooks"
BOOKS = sorted(RUNBOOKS.glob("*.md"))


def anchors(path: Path) -> set[str]:
    """GitHub's heading slugs: lower case, punctuation dropped, spaces to hyphens."""
    return {re.sub(r"[^a-z0-9 -]", "", line.lstrip("#").strip().lower()).replace(" ", "-")
            for line in path.read_text().splitlines() if re.match(r"#{1,6} ", line)}


def test_i3b_rb01_every_alert_names_an_existing_runbook_section():
    rules = json.loads((kit.ROOT / "infra" / "alerts" / "alerts.json").read_text())["rules"]
    for rule in rules:
        document, _, anchor = rule["runbook"].partition("#")
        assert (kit.ROOT / document).is_file(), f"{rule['name']}: no runbook {document}"
        assert anchor in anchors(kit.ROOT / document), f"{rule['name']}: no section #{anchor}"


def test_i3b_rb02_restore_runs_the_drilled_tool_with_the_pinned_client():
    text = (RUNBOOKS / "restore.md").read_text()
    used = set(re.findall(r"pgrestore\.py (\w+)", text))
    assert used == {"dump", "restore", "check"}, used
    for command in ("dump --conninfo", "restore --conninfo", "check --source"):
        assert f"pgrestore.py {command}" in text, command
    assert test_restore.pg.IMAGE == harness.compose_images()["postgres"]


def test_i3b_rb03_the_maintenance_statement_is_the_one_bk04_runs():
    text = (RUNBOOKS / "rollback.md").read_text()
    assert test_restore.MAINTENANCE % "false" in text
    # as bk04 runs it: under service_role (0006's policy), not as the pooler's postgres
    assert "set role service_role;" in text
    assert text.index("set role service_role;") < text.index(test_restore.MAINTENANCE % "false")
    assert "reset role;" in text


def test_i3b_rb08_rollback_step_4_runs_the_script_rc10_drives():
    """rollback.md step 4 is I2B's `rollback.sh` with the argument its own usage line gives
    (the one rc10 drives); the step is no longer a pending placeholder."""
    script, argument = re.search(r"^#   sudo (\./apps/infrx-api/deploy/rollback\.sh) (\S+)$",
                                 (harness.REPO_ROOT / "apps" / "infrx-api" / "deploy" /
                                  "rollback.sh").read_text(), re.M).groups()
    text = (RUNBOOKS / "rollback.md").read_text()
    step = re.search(r"^4\. .*?^5\. ", text, re.S | re.M).group(0)
    assert f'sudo {script} "$BACKUP"\n' in step and f"`{argument}`" in step, step
    assert "PENDING" not in step, step


def test_i3b_rb04_every_bash_step_parses():
    blocks = [(book.name, block) for book in BOOKS
              for block in re.findall(r"```bash\n(.*?)```", book.read_text(), re.S)]
    assert len(blocks) >= 10, blocks
    for name, block in blocks:
        result = subprocess.run(["bash", "-n"], input=block, capture_output=True, text=True)
        assert result.returncode == 0, (name, result.stderr, block[:200])


def test_i3b_rb05_every_link_between_runbooks_resolves_and_each_keeps_a_log():
    for book in BOOKS:
        text = book.read_text()
        assert "## Verification log" in text, book.name
        for target, anchor in re.findall(r"\]\(([\w./-]+\.md)(?:#([\w-]+))?\)", text):
            path = (book.parent / target).resolve()
            assert path.is_file(), (book.name, target)
            if anchor:
                assert anchor in anchors(path), (book.name, target, anchor)
        for anchor in re.findall(r"\]\(#([\w-]+)\)", text):
            assert anchor in anchors(book), (book.name, anchor)


def test_i3b_rb06_a_failed_client_never_re_raises_row_data(monkeypatch, tmp_path):
    """RS-4: pg_restore's DETAIL/CONTEXT lines quote key values or whole rows (measured:
    `DETAIL: Key (id)=(<uuid>) already exists.`); the error an operator sees keeps the
    failure and drops them."""
    pg = test_restore.pg
    stderr = ('pg_restore: error: COPY failed for table "users": ERROR:  duplicate key value '
              'violates unique constraint "users_pkey"\n'
              "DETAIL:  Key (id)=(0a000000-0000-4000-8000-000000000001) already exists.\n"
              "CONTEXT:  COPY users, line 1: \"one@example.com\"\n")
    monkeypatch.setattr(pg.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a[0], 1, stdout="", stderr=stderr))
    try:
        pg.run(tmp_path, "pg_restore", "-d", "x")
    except RuntimeError as failure:
        said = str(failure)
    else:
        raise AssertionError("a failed client did not raise")
    assert "users_pkey" in said
    assert "0a000000" not in said and "one@example.com" not in said, said


def test_i3b_rb07_a_client_timeout_never_re_raises_its_argv(monkeypatch, tmp_path):
    """RS-4 residual: `subprocess.TimeoutExpired`'s text is the whole argv - the `-d`
    conninfo, LOCAL's password included. The tool re-raises the client's name only, and
    does not chain the original (a traceback would print it)."""
    pg = test_restore.pg

    def hang(argv, **_):
        raise subprocess.TimeoutExpired(argv, 1800)
    monkeypatch.setattr(pg.subprocess, "run", hang)
    try:
        pg.run(tmp_path, "pg_restore", "-d", "host=x password=sekritXYZ dbname=y")
    except Exception as failure:                        # noqa: BLE001 - the type is asserted
        raised = failure
    else:
        raise AssertionError("a timed-out client did not raise")
    assert isinstance(raised, RuntimeError), type(raised)
    assert "timed out" in str(raised) and "sekritXYZ" not in str(raised), str(raised)
    assert raised.__cause__ is None and raised.__suppress_context__, "the argv is chained"
