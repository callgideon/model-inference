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


# --- E4C-RUNBOOK-2: the window's hosted order (E4C-readiness-2026-09-26 §2d-f) -----------

RB = kit.ROOT / "models" / "marlin2b" / "results" / "E4C-runbook.md"
MIGRATIONS = kit.ROOT / "apps" / "app" / "supabase" / "migrations"


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    following = re.search(r"^#{2,3} ", text[start + len(heading):], re.M)
    return text[start:start + len(heading) + following.start()] if following else text[start:]


def test_e4c_rb09_the_copy_is_seeded_with_hosted_s_own_applied_history():
    """rollout.md W6: the restored copy has no supabase_migrations schema, so its history is
    seeded before the copy's `plan`. Oracle: the seed was the literal 0001/0002 of the
    prep-time hosted; hosted is at 0018 (20-platform-handoff-2026-09-24.md:73), so the copy
    planned 0003-0025 while hosted plans 0019-0025, their digests differ and W7 aborts - or,
    worse, an operator re-types the seed. The seed is now derived at the window from
    hosted's own read-only `migrate.py plan` `applied:` line; fed the line migrate.py prints
    for a 0001-0018 history, it yields exactly those 18 rows."""
    sys.path.insert(0, str(kit.ROOT / "apps" / "infrx-api" / "deploy"))
    import migrate
    block = _section((RUNBOOKS / "rollout.md").read_text(), "### W6")
    assert "values ('0001', 'init'), ('0002', 'seed_models')" not in block, "the stale seed"
    read = re.search(r'^export MIGRATE_DATABASE_URL="\$HOSTED".*\n'
                     r"^HOSTED_APPLIED=\$\(\$PY apps/infrx-api/deploy/migrate\.py plan \| "
                     r"sed -n 's/\^applied: //p'\)", block, re.M)
    assert read, "the seed is not read from hosted's own plan"
    derive = re.search(r"^SEED=.*$", block, re.M)
    assert derive and block.index(read.group(0)) < derive.start(), "no SEED derived after it"
    assert re.search(r'-c "insert into supabase_migrations\.schema_migrations '
                     r'\(version, name\) values \$SEED"', block), "the insert is not the seed"
    local = migrate.local_migrations(MIGRATIONS)
    applied = {v: n for v, n, _ in local if v <= "0018"}
    line = migrate.describe([], applied).splitlines()[0].removeprefix("applied: ")
    done = subprocess.run(["bash", "-c", f'set -euo pipefail; HOSTED_APPLIED="$1"; '
                                         f'{derive.group(0)}; printf %s "$SEED"', "_", line],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    rows = re.findall(r"\('(\d{4})', '([a-z0-9_]*)'\)", done.stdout)
    assert rows == sorted(applied.items()) and len(rows) == 18, done.stdout
    assert done.stdout == ",".join(f"('{v}', '{n}')" for v, n in rows), done.stdout
    assert "0001-0018" in block and "0001-0025" in _section((RUNBOOKS / "rollout.md").read_text(), "### W7")


def test_e4c_rb10_the_candidate_installs_the_credit_regime_with_the_p01_card():
    """rollout.md §1: the E4C candidate runs in CREDIT with the P-01 card (P-17 check 3).
    Oracle: INSTALL_ARGS left ACCOUNTING_REGIME unset (legacy_usd), so the certify ledger ran
    in USD and P-17 failed by construction; the card in INFRX_SET must be the one the E4C
    runbook publishes, and the known-good record passes every INFRX_SET name (else its
    `config` check skips the two)."""
    ro = (RUNBOOKS / "rollout.md").read_text()
    block = re.search(r"^INSTALL_ARGS=\((.*?)\)$", ro, re.S | re.M).group(1)
    pairs = dict(p.split("=", 1) for p in re.search(r'INFRX_SET="([^"]*)"', block).group(1).split())
    card = re.search(r"publish-card .*?--card-version (\S+)", RB.read_text()).group(1)
    assert pairs.get("ACCOUNTING_REGIME") == "credit", pairs
    assert pairs.get("ACTIVE_RATE_CARD_VERSION") == card == "rc_marlin2b_20260925_launch", pairs
    record = _section(ro, "### Known-good record")
    assert set(pairs) <= set(re.findall(r"--set (\w+)", record)), record
    row = next(line for line in ro.splitlines() if line.startswith("| `ACCOUNTING_REGIME`"))
    for claim in ("config.py:249", "price_source", "55000", "exit 2", "exit 4"):
        assert claim in row, claim


def test_e4c_rb11_the_card_and_the_activation_follow_the_hosted_apply():
    """E4C-runbook §0 ran publish-card and the credit-transition before the window, but the
    activation calls 0022's `infrx.set_feature_flag` (transition.py) and hosted is at 0018:
    both must follow W7, and both must precede the install (W8-W13) that sets the regime.
    Oracle: either command in §0, or after the install line; a stale `0.3`-`0.8` step
    reference left behind by the renumbering."""
    text = RB.read_text()
    before = _section(text, "## 0. Before the window")
    assert "publish-card" not in before and "credit-transition" not in before, before
    hosted = _section(text, "### 1a. After the hosted apply")
    window = _section(text, "## 1. The window")
    assert window.index("W1–W7") < window.index("§1a below (H1–H6") < window.index("W8–W13")
    assert text.index("## 1. The window") < text.index("### 1a.") < text.index("## 2. Freeze")
    steps = re.findall(r"^\| (H\d) \|", hosted, re.M)
    assert steps == ["H1", "H2", "H3", "H4", "H5", "H6"], steps
    order = [hosted.index(s) for s in ("publish-card", "credit-transition --dry-run",
                                       "credit-transition --card", "revoke-key", "grant --user",
                                       "adjust --user", "keys-certify.json")]
    assert order == sorted(order), order
    body = text.split("## Verification log")[0]
    stale = re.findall(r"\b(?:step|as in|see|retakes?|after|flags as|yet) 0\.[3-8]\b|\(0\.[3-8]\)"
                       r"|\b0\.[3-8]'s\b|\b0\.[3-8]/0\.[3-8]\b|\b0\.[3-8] `keys`|^\| 0\.[3-8] \|",
                       body, re.M)
    assert not stale, stale
    assert "set_feature_flag" in hosted


ROLLOUT_README = kit.ROOT / "infra" / "rollout" / "README.md"
#: The steps that restart a release other than RELEASE and reopen the edge on it.
BACK = r"91-abort|90-revert|create-replace-root-volume-task|root-volume swap|\bR[124]\b"


def _first_back(cell: str) -> int:
    found = re.search(BACK, cell)
    assert found, cell
    return found.start()


def test_e4c_rb12_every_way_back_after_the_activation_reverses_it_first():
    """rollout.md W7f moves hosted to CREDIT (`credit_admission` t, `legacy_usd_admission` f)
    before the install. Every rollback after it restarts a release that runs legacy_usd (the
    previous pilot release, the known-good targets) and reopens the edge, where 0006's
    admission guard then refuses every request. Oracle: a rollback cell after W7f - in the
    window table, in §3, or in README R1/R2/R4 - that reaches 91-abort, 90-revert or the
    root-volume swap without `credit-transition --to legacy_usd` (the W7f reversal) first."""
    ro = (RUNBOOKS / "rollout.md").read_text()
    window = {m.group(1): m.group(0) for m in re.finditer(r"^\| (W\d+[a-z]?) \|.*$", ro, re.M)}
    order = list(window)
    checked = []
    for step in order[order.index("W7f") + 1:]:
        back = window[step].rstrip().rstrip("|").rsplit("|", 1)[-1]
        if re.search(BACK, back):
            assert "W7f reversal" in back and back.index("W7f reversal") < _first_back(back), \
                (step, back)
            checked.append(step)
    assert checked == ["W8", "W9", "W10", "W11", "W12"], checked
    triggers = _section(ro, "## 3. Rollback triggers")
    assert "credit-transition --to legacy_usd" in triggers.split("| Trigger |")[0], \
        "§3 does not define the W7f reversal"
    late = [line.split(" | ", 1) for line in triggers.splitlines()
            if line.startswith("| ") and not line.startswith(("| Trigger", "|---"))]
    late = [(t, a) for t, a in late if re.search(r"\bW(9|1[0-2])\b", t) or a.startswith("R4")]
    assert len(late) == 5, late
    for trigger, action in late:
        action = re.sub(r"^R\d: ", "", action)                      # the row's own label
        assert "W7f reversal" in action and action.index("W7f reversal") < _first_back(action), \
            (trigger, action)
    readme = ROLLOUT_README.read_text()
    for revert in ("R1", "R2", "R4"):
        row = next(line for line in readme.splitlines() if line.startswith(f"| {revert} - "))
        action = row.split(" | ", 1)[1]
        assert "--to legacy_usd" in action and action.index("--to legacy_usd") < _first_back(
            action.replace(revert, "", 1)), (revert, action)


def test_e4c_rb13_the_readme_window_activates_credit_before_the_credit_install():
    """infra/rollout/README.md §1 is a valid window sequence (E4C-runbook §1), and its step 8
    installs rollout.md §1's INSTALL_ARGS, which carry ACCOUNTING_REGIME=credit. Oracle: no
    step between the hosted apply (6) and the install (8) publishes the card and activates
    CREDIT, so the install exits 4 (price_source) or serves 503 on every admission."""
    readme = ROLLOUT_README.read_text()
    steps = re.findall(r"^\| (\d+[a-z]?) \| ", _section(readme, "## 1. The window"), re.M)
    assert steps.index("6") < steps.index("6b") < steps.index("7") < steps.index("8"), steps
    row = next(line for line in readme.splitlines() if line.startswith("| 6b | "))
    for needle in ("rollout.md#2-the-window", "W7f",
                   "E4C-runbook.md#1a-after-the-hosted-apply-before-w8", "publish-card",
                   "credit-transition", "ACCOUNTING_REGIME=credit", "--to legacy_usd"):
        assert needle in row, needle
    install = next(line for line in readme.splitlines() if line.startswith("| 8 | "))
    assert re.search(r"ACCOUNTING_REGIME=credit[^|]*only after (step )?6b", install), install
