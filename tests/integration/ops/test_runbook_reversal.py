"""RUNBOOK-3: the W7f reversal and the reinstall rule, as the runbooks state them. Layer 1.

* every way back onto a `legacy_usd` release in rollback.md (the rollout rollback's restore,
  the known-good drill's install) names `credit-transition --to legacy_usd` before it, and
  rollout.md §3 names the verb's key rules and the PostgreSQL drill that proves them - a test
  that exists under that name;
* the reversal drains while the release it reverses still has its worker: before any step that
  stops it (review 0-RV3-1), with the way out named for a run after one did;
* every 50-install of a release with R127's dedicated logins is followed by
  `55-runtime-login.sh` (preflight rewrites the env file from SSM: `DATABASE_URL` is the owner
  login again and `MONITOR_DATABASE_URL` is gone), and a pre-R127 target never gets it.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
RUNBOOKS = REPO / "infra" / "runbooks"
ROLLOUT = (RUNBOOKS / "rollout.md").read_text()
ROLLBACK = (RUNBOOKS / "rollback.md").read_text()
VERB = "credit-transition --to legacy_usd"
LOGIN = "55-runtime-login.sh"


def section(text: str, title: str) -> str:
    return re.search(rf"^{re.escape(title)}\n(.*?)(?=^## )", text, re.S | re.M).group(1)


def flat(text: str) -> str:
    """Prose as read: one space for any line break, no bold markers."""
    return re.sub(r"\s+", " ", text).replace("**", "")


def steps(text: str) -> dict[str, str]:
    """A numbered procedure's steps by number, each with its continuation lines."""
    return {m.group(1): m.group(0) for m in
            re.finditer(r"^(\d+[a-z]?)\. .*?(?=^\d+[a-z]?\. |\Z)", text, re.S | re.M)}


def test_runbook3_rv01_every_restore_onto_legacy_usd_reverses_w7f_first():
    """Oracle: rollback.md restores or installs a `legacy_usd` release after W7f with no
    reversal before it, so hosted (CREDIT only) refuses every admission while the restored
    runtime reads ready; or §3's proof names a test that does not exist."""
    restore = steps(section(ROLLBACK, "## Rollout rollback"))["4"]
    assert VERB in restore and restore.index(VERB) < restore.index("rollback.sh \"$BACKUP\""), \
        restore
    drill = section(ROLLBACK, "## Known-good rollback drill")
    assert VERB in drill and drill.index(VERB) < drill.index("50-install.sh"), drill
    forward = next(s for s in steps(drill).values() if "**Roll forward" in s)
    assert "credit-transition --card" in forward and "new key" in flat(forward), forward
    reversal = flat(section(ROLLOUT, "## 3. Rollback triggers").split("| Trigger |")[0])
    for rule in (VERB, "same key", "new key", "--dry-run", "operator step"):
        assert rule in reversal, rule
    proof = re.search(r"test_reversal_pg\.py::(test_\w+)", reversal).group(1)
    tests = (REPO / "apps/infrx-api/tests/g/ops/test_reversal_pg.py").read_text()
    assert f"def {proof}(" in tests, proof
    assert proof in ROLLBACK, "rollback.md does not cite the drill that proves the reversal"


def test_runbook3_rv02_every_reinstall_is_followed_by_the_runtime_login_step():
    """Oracle: an install row or step with no 55 after it leaves the runtime on the owner
    login and the worker without its monitor login until someone remembers; or the rule
    sends 55 after a pre-R127 target, whose pool `set role service_role` the dedicated login
    may not (0021: a member of no role)."""
    window = [line for line in ROLLOUT.splitlines() if re.match(r"\| W\d+[a-z]? \|", line)]
    installs = [i for i, row in enumerate(window) if "50-install.sh" in row.split(" | ")[2]]
    assert installs, "no install row"
    for i in installs:
        assert LOGIN in window[i + 1].split(" | ")[2], window[i + 1]
    rule = section(ROLLOUT, "## 3. Rollback triggers")
    assert "reinstall rule" in rule and LOGIN in rule
    assert "4226315" in rule and "bda1586" in rule and "never" in rule
    drill = steps(section(ROLLBACK, "## Known-good rollback drill"))
    for text in drill.values():
        if "50-install.sh" in text or "**Roll forward" in text:
            assert LOGIN in text, text
            if "50-install.sh" in text:
                assert text.index("50-install.sh") < text.index(LOGIN), text


def test_runbook3_rv03_the_reversal_drains_before_anything_stops_the_worker():
    """Oracle (review 0-RV3-1): the reversal scheduled after a step that stops the worker
    (30-pause.sh, the rollout rollback's drain): a CREDIT job that the drain released stays
    in flight (the reaper is the worker's), so every same-key rerun exits 1 `in_flight` and
    neither regime admits; or the roll-forward drains the target's USD jobs after its own
    pause stopped them; or no way out is named for a run after a pause already did."""
    rollout_rollback = section(ROLLBACK, "## Rollout rollback")
    assert rollout_rollback.index(VERB) < rollout_rollback.index("**Drain and fence**"), \
        rollout_rollback
    drill = section(ROLLBACK, "## Known-good rollback drill")
    assert drill.index(VERB) < drill.index("30-pause.sh"), drill
    forward = next(s for s in steps(drill).values() if "**Roll forward" in s)
    assert "step 3b" in forward, forward
    assert forward.index("credit-transition --card") < forward.index("step 3b"), forward
    reversal = flat(section(ROLLOUT, "## 3. Rollback triggers").split("| Trigger |")[0])
    for text in (reversal, flat(drill)):
        for way_out in ("systemctl start infrx-worker", "systemctl stop infrx-worker"):
            assert way_out in text, (way_out, text)
