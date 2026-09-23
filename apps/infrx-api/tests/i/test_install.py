#!/usr/bin/env python3
"""`DEPLOY-FAILCLOSED` (research/plan/04-verification.md): a failed install leaves the
previous env file byte-identical and restarts nothing.

Row `O-FAILOPEN` of the I1 inventory is the defect: the installer swallowed every
`get-parameter` failure, truncated `/etc/marlin2b-gateway.env` and restarted the
gateway anyway, and a gateway with neither `GATEWAY_API_KEY` nor `SUPABASE_URL`
allowed every request. The last case in this file reproduces that behaviour from the
old five lines so the fix is provably a change and not a claim.

Every case asserts **the bytes of the installed file and the recorded `systemctl`
calls**, never only an exit code: an installer that returned non-zero after
truncating the file would pass an exit-code test and still publish an open gateway.

No case touches AWS, systemd, the network or the pilot host. **This suite does not fix
the deployed host**; it fixes the script a later, separately scoped deployment runs.
"""
from __future__ import annotations

import errno
import os
import stat
import subprocess
import sys

import pytest

from . import support
from .support import MARKER, VALID, preflight

SUPABASE_URL = "/model-inference/supabase_url"
SERVICE_ROLE = "/model-inference/supabase_service_role_key"
JOURNAL = "/model-inference/pg_journal_url"
LEGACY_KEY = "/model-inference/marlin2b_api_key"


def valid(**changes):
    """The working parameter set with named entries replaced or removed."""
    spec = {name: {"value": value} for name, value in VALID.items()}
    for name, entry in changes.items():
        target = {"url": SUPABASE_URL, "role": SERVICE_ROLE,
                  "journal": JOURNAL, "legacy": LEGACY_KEY}[name]
        if entry is None:
            spec.pop(target)
        else:
            spec[target] = entry
    return spec


def unchanged(cfg, before: bytes, calls) -> None:
    """The invariant every failure case shares, stated once."""
    assert cfg.env_file.read_bytes() == before, "the installed env file was modified"
    assert calls == [], f"a failed install called systemctl: {calls}"
    assert list(cfg.env_file.parent.glob(".*tmp")) == [], "a staged file was left behind"


# --- a read that failed, in each of the ways it can fail ---------------------------
DENIALS = ("AccessDeniedException", "ThrottlingException", "ExpiredTokenException",
           "KMSAccessDeniedException", "SomethingNobodyHasSeenBefore")


@pytest.mark.parametrize("code", DENIALS)
def test_deploy_failclosed__a_denied_or_unexplained_read_installs_nothing(
        tmp_path, monkeypatch, code):
    """The `O-FAILOPEN` case itself: `AccessDenied`, a throttle, an expired token, a
    KMS refusal and an error nobody has classified all mean "do not deploy". The last
    one matters most - an installer that only recognises the codes it was taught would
    treat a new one as an absent parameter."""
    made = support.stubs(tmp_path, monkeypatch, valid(url={"error": code}))
    cfg = support.config(tmp_path, mode="pilot")
    before = cfg.env_file.read_bytes()
    assert preflight.apply(cfg) == preflight.REFUSED
    unchanged(cfg, before, made.systemctl_calls)


def test_deploy_failclosed__a_required_parameter_that_is_missing_is_a_failure(
        tmp_path, monkeypatch, capsys):
    """`ParameterNotFound` was the only case the old warning was written for, and it is
    still a failure when the mode requires the value."""
    made = support.stubs(tmp_path, monkeypatch, valid(journal=None))
    cfg = support.config(tmp_path, mode="pilot")
    before = cfg.env_file.read_bytes()
    assert preflight.apply(cfg) == preflight.REFUSED
    unchanged(cfg, before, made.systemctl_calls)
    message = capsys.readouterr().err
    assert JOURNAL in message and "not_found" in message
    assert "DATABASE_URL" in message


@pytest.mark.parametrize("code", ["AccessDeniedException", "ThrottlingException",
                                  "SomethingNobodyHasSeenBefore"])
def test_deploy_failclosed__an_optional_parameter_is_omitted_only_when_absent(
        tmp_path, monkeypatch, code):
    """The distinction `infra/README.md` §5 demands, asserted on the **same** key.
    `GATEWAY_API_KEY` is optional in dev, so `ParameterNotFound` leaves it out and the
    install proceeds; a denial, a throttle or an error nobody has classified on that
    same parameter aborts, because none of them is evidence of absence."""
    made = support.stubs(tmp_path, monkeypatch, valid(legacy=None))
    cfg = support.config(tmp_path)
    assert preflight.apply(cfg) == 0
    assert "GATEWAY_API_KEY" not in preflight.read_env(cfg.env_file)
    assert made.systemctl_calls == ["daemon-reload", "restart marlin2b-vllm marlin2b-gateway"]

    denied = support.stubs(tmp_path, monkeypatch, valid(legacy={"error": code}))
    after_first = cfg.env_file.read_bytes()
    denied.fail_systemctl()
    (denied.dir / "systemctl.log").unlink(missing_ok=True)
    assert preflight.apply(cfg) == preflight.REFUSED
    unchanged(cfg, after_first, denied.systemctl_calls)


# --- a value that arrived but may not be written -----------------------------------
BAD_VALUES = (
    ("empty", SUPABASE_URL, ""),
    ("whitespace only", SUPABASE_URL, "   "),
    ("padded", SUPABASE_URL, " https://x.supabase.co "),
    ("not https", SUPABASE_URL, "http://fcbnscgsymzdykendbrc.supabase.co"),
    ("not a url", SUPABASE_URL, "fcbnscgsymzdykendbrc"),
    ("wrong dsn scheme", JOURNAL, "mysql://infrx:pw@db.invalid/infrx"),
    ("truncated secret", SERVICE_ROLE, "short"),
    # An opaque secret has no pattern to fail, so these three are the cases the
    # length-and-pattern check cannot catch and the universal guards must.
    ("whitespace-only secret", SERVICE_ROLE, " " * 24),
    ("newline in a secret", SERVICE_ROLE, "role-key-long-enough\nGATEWAY_API_KEY=x"),
    ("padded secret", SERVICE_ROLE, " role-key-long-enough-here "),
)


@pytest.mark.parametrize("name,parameter,value", BAD_VALUES, ids=[b[0] for b in BAD_VALUES])
def test_deploy_failclosed__a_value_of_the_wrong_shape_installs_nothing(
        tmp_path, monkeypatch, name, parameter, value):
    """Validation happens before the installed file is touched, so a bad value is not
    a half-written env file. An empty value is the one the old script could not tell
    from a failed read at all."""
    spec = {name: {"value": v} for name, v in VALID.items()}
    spec[parameter] = {"value": value}
    made = support.stubs(tmp_path, monkeypatch, spec)
    # dev, not pilot: every value that is read is validated whether the mode requires
    # it or not, and in pilot the pending engine-digest gap would refuse first and
    # leave this case unable to tell validation from that refusal.
    cfg = support.config(tmp_path)
    before = cfg.env_file.read_bytes()
    assert preflight.apply(cfg) == preflight.REFUSED
    unchanged(cfg, before, made.systemctl_calls)


def test_deploy_failclosed__a_value_cannot_write_a_second_variable(tmp_path, monkeypatch,
                                                                  capsys):
    """The trust boundary: an SSM value containing a newline would append its own
    `KEY=VALUE` line, i.e. whoever can write a parameter chooses `GATEWAY_API_KEY` and
    with it the gateway's authentication. Refused for every key."""
    injection = f"https://x.supabase.co\nGATEWAY_API_KEY={MARKER}-injected"
    made = support.stubs(tmp_path, monkeypatch, valid(url={"value": injection}))
    cfg = support.config(tmp_path)
    before = cfg.env_file.read_bytes()
    assert preflight.apply(cfg) == preflight.REFUSED
    unchanged(cfg, before, made.systemctl_calls)
    assert MARKER not in capsys.readouterr().err


def test_deploy_failclosed__a_withdrawn_price_key_is_refused(tmp_path, monkeypatch):
    """`/model-inference/price_table_version` was withdrawn, not reassigned: contracts
    v1 resolves the price from D1's `price_versions`, so a deploy-time pin would be a
    second price authority. An env file carrying one is refused."""
    made = support.stubs(tmp_path, monkeypatch)
    cfg = support.config(tmp_path)
    before = cfg.env_file.read_bytes()
    values, problems = preflight.collect(cfg)
    assert problems == []
    values["PRICE_TABLE_VERSION"] = "7"
    assert any("PRICE_TABLE_VERSION" in problem
               for problem in preflight.collect(cfg)[1] + preflight.withdrawn(values))
    unchanged(cfg, before, made.systemctl_calls)


# --- the mode itself ---------------------------------------------------------------
@pytest.mark.parametrize("mode", ["", "prod", "Pilot", "legacy"])
def test_deploy_failclosed__an_unset_or_unknown_mode_installs_nothing(
        tmp_path, monkeypatch, mode, capsys):
    """There is no default mode (infra/README.md §5): a typo in a unit file is not a
    mode, and an empty `INFRX_MODE` is not "legacy, that's fine". Nothing is even read
    for an unusable mode."""
    made = support.stubs(tmp_path, monkeypatch)
    cfg = support.config(tmp_path, mode=mode)
    before = cfg.env_file.read_bytes()
    assert preflight.apply(cfg) == preflight.REFUSED
    unchanged(cfg, before, made.systemctl_calls)
    assert "no default mode" in capsys.readouterr().err
    assert made.argv == "", "a parameter was read for an unusable mode"


def test_deploy_failclosed__pilot_is_refused_while_the_runtime_is_not_composed(
        tmp_path, monkeypatch, capsys):
    """Item 3 of the brief: pilot mode is only written when the pilot routers are
    composed. `infrx/gateway/app.py` still mounts `(health, models, chat)`, so a
    correct pilot configuration with every parameter present is *still* refused - and
    that is the current, intended state until the G2 cutover. The engine image is
    pinned here so the *other* pilot prerequisite is not what refuses the install."""
    made = support.stubs(tmp_path, monkeypatch)
    cfg = support.config(tmp_path, mode="pilot",
                         serve_script=support.serve_script(tmp_path, image=support.PINNED))
    before = cfg.env_file.read_bytes()
    assert preflight.apply(cfg) == preflight.REFUSED
    unchanged(cfg, before, made.systemctl_calls)
    message = capsys.readouterr().err
    assert "pilot routers to be composed" in message  # preflight's own phrase (G1R review C2)
    assert MARKER not in message


def test_deploy_failclosed__the_staged_bytes_are_what_the_runtime_validates(tmp_path,
                                                                           monkeypatch):
    """`config.validate_runtime` is the probe, run against the file that would be
    installed. It is a second opinion on purpose: a pilot file missing the metering
    DSN is refused by the runtime's own rule even though the manifest here would have
    to have been wrong for it to get that far."""
    support.stubs(tmp_path, monkeypatch)
    staged = tmp_path / "staged.env"
    staged.write_text("INFRX_MODE=pilot\nSUPABASE_URL=https://x.supabase.co\n"
                      "SUPABASE_SERVICE_ROLE_KEY=" + MARKER + "-role\n")
    verdict = preflight.probe(staged, "pilot")
    assert verdict["ok"] is False
    assert any("DATABASE_URL" in problem for problem in verdict["problems"])
    assert not any(MARKER in problem for problem in verdict["problems"])


def test_deploy_failclosed__pilot_never_writes_the_shared_legacy_key(tmp_path,
                                                                    monkeypatch, capsys):
    """r1 R51: `Auth.authenticate` answers "allowed, no row" for the shared key, so a
    request bearing it has no tenant to meter. The installer does not read the
    parameter in pilot mode and says so, and the runtime refuses the mode if the key
    reaches the file by any other route."""
    made = support.stubs(tmp_path, monkeypatch)
    cfg = support.config(tmp_path, mode="pilot")
    values, _ = preflight.collect(cfg)
    assert "GATEWAY_API_KEY" not in values
    assert LEGACY_KEY not in made.argv
    assert "GATEWAY_API_KEY: forbidden in pilot mode" in capsys.readouterr().out

    staged = tmp_path / "with-key.env"
    staged.write_text("INFRX_MODE=pilot\nDATABASE_URL=postgresql://h/d\n"
                      "SUPABASE_URL=https://x.supabase.co\n"
                      "SUPABASE_SERVICE_ROLE_KEY=role-key-long-enough\n"
                      f"GATEWAY_API_KEY={MARKER}-shared\n")
    verdict = preflight.probe(staged, "pilot")
    assert any("GATEWAY_API_KEY" in problem for problem in verdict["problems"])
    assert not any(MARKER in problem for problem in verdict["problems"])


# --- the filesystem ----------------------------------------------------------------
def test_deploy_failclosed__a_directory_it_cannot_write_installs_nothing(tmp_path,
                                                                        monkeypatch):
    """A read-only `/etc` (or a wrong owner) must abort at the staging step, which is
    before anything the running service can see."""
    made = support.stubs(tmp_path, monkeypatch)
    cfg = support.config(tmp_path)
    before = cfg.env_file.read_bytes()
    directory = cfg.env_file.parent
    assert preflight.staged_name(cfg.env_file).parent == directory, (
        "the staged file shares the target's directory, so the rename is atomic and a "
        "directory the installer cannot write aborts before anything is staged")
    directory.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        with pytest.raises(OSError):
            preflight.apply(cfg)
        assert cfg.env_file.read_bytes() == before
        assert made.systemctl_calls == []
    finally:
        directory.chmod(0o755)


def test_deploy_failclosed__a_write_that_fails_leaves_no_staged_file(tmp_path,
                                                                    monkeypatch):
    """A full disk: the staged file is removed, the installed file is byte-identical
    and nothing restarts. Injected at `os.write`, because a 0600 file in `/etc` holding
    half a secret is the failure mode, not the exit code."""
    made = support.stubs(tmp_path, monkeypatch)
    cfg = support.config(tmp_path)
    before = cfg.env_file.read_bytes()

    def no_space(*args, **kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(preflight.os, "write", no_space)
    with pytest.raises(OSError):
        preflight.apply(cfg)
    unchanged(cfg, before, made.systemctl_calls)


def test_deploy_failclosed__a_crash_before_the_rename_changes_nothing(tmp_path,
                                                                     monkeypatch):
    """The installer is killed between staging and renaming - the runtime probe is the
    step in between, so a `--runtime-python` that kills its parent lands exactly there
    without a crash hook in the module. The installed file is byte-identical, nothing
    restarted, and the staged file the dead process left is removed by the next run."""
    made = support.stubs(tmp_path, monkeypatch)
    cfg = support.config(tmp_path)
    before = cfg.env_file.read_bytes()
    killer = support.runtime_stub(tmp_path, "killer-python", support.KILLER_STUB)
    done = support.run_installer(["apply", "--mode", "dev",
                                  "--env-file", str(cfg.env_file),
                                  "--owner", support.owner_name(),
                                  "--runtime-python", killer,
                                  "--serve-script", str(cfg.serve_script),
                                  "--restart", "marlin2b-gateway"])
    assert done.returncode == -9, done.stderr
    assert cfg.env_file.read_bytes() == before
    assert made.systemctl_calls == []
    stale = list(cfg.env_file.parent.glob(".marlin2b-gateway.env.*.tmp"))
    assert len(stale) == 1, "the crash should have left exactly the staged file"
    assert stat.S_IMODE(stale[0].stat().st_mode) == 0o600

    assert preflight.apply(cfg) == 0
    assert list(cfg.env_file.parent.glob(".marlin2b-gateway.env.*.tmp")) == []
    assert preflight.read_env(cfg.env_file)["INFRX_MODE"] == "dev"


def test_deploy_failclosed__the_probe_that_says_nothing_is_a_refusal(tmp_path,
                                                                    monkeypatch):
    """A runtime interpreter that answers something other than a verdict is not a
    passing check."""
    made = support.stubs(tmp_path, monkeypatch)
    babbler = support.runtime_stub(tmp_path, "babbler", support.BABBLER_STUB)
    cfg = support.config(tmp_path, runtime_python=babbler)
    before = cfg.env_file.read_bytes()
    assert preflight.apply(cfg) == preflight.REFUSED
    unchanged(cfg, before, made.systemctl_calls)


def test_deploy_failclosed__a_pilot_install_stops_on_the_unpinned_engine_image(
        tmp_path, monkeypatch, capsys):
    """Review r1 B2: the digest requirement was asserted only at `engine_problems`
    level, so skipping the engine checks **in pilot** survived every case — the one
    apply-level pilot case used a pinned script, the flag case ran in dev, and the
    real-script case called `engine_problems` directly. This is the apply-level
    assertion: a pilot install with the default floating-tag script, every parameter
    present and valid, refuses before the env file is touched.

    It is also the repository's state today — `models/marlin2b/serve.sh` defaults to
    `:nightly` and W3 owns the pin — so this refusal is the recorded pending gap."""
    made = support.stubs(tmp_path, monkeypatch)
    cfg = support.config(tmp_path, mode="pilot")          # default script: floating tag
    before = cfg.env_file.read_bytes()
    assert preflight.apply(cfg) == preflight.REFUSED
    unchanged(cfg, before, made.systemctl_calls)
    message = capsys.readouterr().err
    assert "digest" in message, message
    assert MARKER not in message


# --- the install that works, and the restart that does not -------------------------
def test_deploy_failclosed__an_engine_the_adapter_cannot_read_installs_nothing(
        tmp_path, monkeypatch, capsys):
    """Brief item 4 reaching the install decision: the gateway's adapter cannot read a
    reasoning-parser stream, so an install run that finds the flag in the script the
    vLLM unit starts refuses before the env file is touched."""
    made = support.stubs(tmp_path, monkeypatch)
    cfg = support.config(tmp_path,
                         serve_script=support.serve_script(tmp_path,
                                                           extra="--reasoning-parser qwen3"))
    before = cfg.env_file.read_bytes()
    assert preflight.apply(cfg) == preflight.REFUSED
    unchanged(cfg, before, made.systemctl_calls)
    assert "--reasoning-parser" in capsys.readouterr().err


@pytest.mark.parametrize("previous", ["PREVIOUS=1\n", None],
                         ids=["replacing a file", "first install"])
def test_deploy_failclosed__a_valid_install_replaces_the_file_and_restarts(tmp_path,
                                                                          monkeypatch,
                                                                          previous):
    """The positive case, asserted on the same two things: the file's bytes, mode and
    owner, and the exact `systemctl` calls in order. `O_EXCL` staging and `os.replace`
    also have to work when there is nothing to replace."""
    made = support.stubs(tmp_path, monkeypatch)
    cfg = support.config(tmp_path, previous=previous)
    assert preflight.apply(cfg) == 0
    installed = preflight.read_env(cfg.env_file)
    assert installed["INFRX_MODE"] == "dev"
    assert installed["MODEL_ID"] == "nemostation/marlin-2b"
    assert installed["SUPABASE_URL"] == VALID[SUPABASE_URL]
    assert installed["DATABASE_URL"] == VALID[JOURNAL]
    assert set(installed) <= {key.env for key in preflight.MANIFEST}
    assert stat.S_IMODE(cfg.env_file.stat().st_mode) == 0o600
    assert cfg.env_file.owner() == support.owner_name()
    assert made.systemctl_calls == ["daemon-reload",
                                    "restart marlin2b-vllm marlin2b-gateway"]


def test_deploy_failclosed__the_final_owner_is_set_before_the_rename(tmp_path,
                                                                     monkeypatch):
    """The staged file gets its final owner *before* `os.replace`, so the installed file
    is never briefly owned by root while the service user needs it - and never briefly
    readable by anyone else. Asserted through the call, because a test that is not root
    can only chown to the user it already is."""
    support.stubs(tmp_path, monkeypatch)
    cfg = support.config(tmp_path)
    chowns = []
    real = preflight.shutil.chown
    monkeypatch.setattr(preflight.shutil, "chown",
                        lambda path, **kw: chowns.append((str(path), kw)) or real(path, **kw))
    assert preflight.apply(cfg) == 0
    assert len(chowns) == 1, chowns
    path, keywords = chowns[0]
    assert keywords == {"user": support.owner_name()}
    assert path == str(preflight.staged_name(cfg.env_file)), "chowned after the rename"


def test_deploy_failclosed__a_failed_restart_is_reported_and_not_rolled_back(
        tmp_path, monkeypatch, capsys):
    """A validated file is installed; a unit that will not start is an alert, not a
    reason to put an unvalidated file back. The rollback path is infra/README.md §8 -
    a compatible runtime or a maintenance 503, never unmetered serving."""
    made = support.stubs(tmp_path, monkeypatch, restart=1)
    cfg = support.config(tmp_path)
    assert preflight.apply(cfg) == preflight.RESTART_FAILED
    assert preflight.read_env(cfg.env_file)["INFRX_MODE"] == "dev"
    assert made.systemctl_calls == ["daemon-reload",
                                    "restart marlin2b-vllm marlin2b-gateway"]
    assert "marlin2b-gateway" in capsys.readouterr().err


def test_deploy_failclosed__a_failed_daemon_reload_never_reaches_the_restart(
        tmp_path, monkeypatch):
    """Restarting units against a unit file systemd has not reloaded is how a deploy
    ends up running the old command line with the new configuration."""
    made = support.stubs(tmp_path, monkeypatch, **{"daemon-reload": 1})
    cfg = support.config(tmp_path)
    assert preflight.apply(cfg) == preflight.RESTART_FAILED
    assert made.systemctl_calls == ["daemon-reload"]


# --- secrets -----------------------------------------------------------------------
def test_deploy_failclosed__no_secret_value_reaches_stdout_stderr_or_an_argument(
        tmp_path, monkeypatch, capsys):
    """The marker audit. Every stub secret contains `MARKER`, and the stubs record
    their own `argv`, so one assertion covers the installer's own output, the probe
    subprocess's output, and every argument of `aws`, `systemctl` and the runtime
    interpreter. Only the 0600 env file may contain it."""
    made = support.stubs(tmp_path, monkeypatch)
    cfg = support.config(tmp_path, runtime_python=made.logging_python())
    assert preflight.apply(cfg) == 0
    captured = capsys.readouterr()
    assert MARKER not in captured.out and MARKER not in captured.err
    assert MARKER not in made.argv
    assert MARKER in cfg.env_file.read_text(), "the audit would pass vacuously"


def test_deploy_failclosed__a_stale_staged_file_is_never_left_in_place(tmp_path,
                                                                      monkeypatch):
    """Staged files hold secrets at 0600 and `/etc` is forever, so a crashed run's
    leftovers are removed rather than accumulating. The deployment lock
    (infra/README.md §1) is what makes "any match is abandoned" true."""
    support.stubs(tmp_path, monkeypatch)
    cfg = support.config(tmp_path)
    stale = cfg.env_file.parent / ".marlin2b-gateway.env.4242.tmp"
    stale.write_text(f"SUPABASE_SERVICE_ROLE_KEY={MARKER}-abandoned\n")
    assert preflight.apply(cfg) == 0
    assert not stale.exists()
    assert list(cfg.env_file.parent.glob(".marlin2b-gateway.env.*.tmp")) == []


# --- the behaviour this task replaced ---------------------------------------------
OLD_INSTALLER = r"""
set -euo pipefail
REGION=us-east-1
ssm() {
  aws ssm get-parameter --name "$1" --with-decryption --region "$REGION" \
      --query Parameter.Value --output text 2>/dev/null || {
    echo "warning: SSM $1 missing; leaving it out of ENV_FILE" >&2; }
}
key=$(ssm /model-inference/marlin2b_api_key)
supabase_url=$(ssm /model-inference/supabase_url)
supabase_key=$(ssm /model-inference/supabase_service_role_key)

install -m 600 /dev/null "$ENV_FILE"
{ printf 'MODEL_ID=nemostation/marlin-2b\nMAX_INFLIGHT=16\n'
  if [ -n "$key" ]; then printf 'GATEWAY_API_KEY=%s\n' "$key"; fi
  if [ -n "$supabase_url" ]; then printf 'SUPABASE_URL=%s\n' "$supabase_url"; fi
  if [ -n "$supabase_key" ]; then printf 'SUPABASE_SERVICE_ROLE_KEY=%s\n' "$supabase_key"; fi
} > "$ENV_FILE"
systemctl restart marlin2b-gateway
"""


def test_deploy_failclosed__the_old_installer_published_an_open_gateway(tmp_path,
                                                                       monkeypatch):
    """Row `O-FAILOPEN`, reproduced from `install.sh` lines 12-26 and 35 at base
    `ec6c548`, so the change is demonstrated rather than asserted.

    With every parameter read denied, the old five lines truncate the env file, write
    an env with **neither** `GATEWAY_API_KEY` nor `SUPABASE_URL` - the configuration
    for which `authenticate()` returns no error at all, i.e. every request allowed,
    unauthenticated and unmetered - and restart the gateway. The new path, same stubs,
    leaves the file byte-identical and restarts nothing.
    """
    denied = {name: {"error": "AccessDeniedException"} for name in VALID}
    made = support.stubs(tmp_path, monkeypatch, denied)
    cfg = support.config(tmp_path, previous="SUPABASE_URL=https://old.supabase.co\n")
    before = cfg.env_file.read_bytes()

    old = subprocess.run(["bash", "-c", OLD_INSTALLER], capture_output=True, text=True,
                         env={**os.environ, "ENV_FILE": str(cfg.env_file)})
    assert old.returncode == 0, old.stderr
    truncated = preflight.read_env(cfg.env_file)
    assert truncated == {"MODEL_ID": "nemostation/marlin-2b", "MAX_INFLIGHT": "16"}
    assert "SUPABASE_URL" not in truncated and "GATEWAY_API_KEY" not in truncated
    assert made.systemctl_calls == ["restart marlin2b-gateway"]

    cfg.env_file.write_bytes(before)
    (made.dir / "systemctl.log").unlink()
    assert preflight.apply(cfg) == preflight.REFUSED
    unchanged(cfg, before, made.systemctl_calls)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
