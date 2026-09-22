#!/usr/bin/env python3
"""The prerequisites an install run checks, and the manifest it checks them against
(`DEPLOY-FAILCLOSED`, brief item 4).

Three of them can only be answered by the interpreter that will actually serve, so
`preflight.py probe` runs there and this file drives it directly: the pinned
interpreter version, the transport logger levels and the composed runtime. The fourth,
the engine flags, is read from the script the vLLM unit runs.

The engine **image** is deliberately not satisfiable yet: `models/marlin2b/serve.sh`
defaults to a floating `vllm/vllm-openai:nightly` tag and W3 owns the digest pin, so
the last case pins that gap as a test rather than leaving it as a note.
"""
from __future__ import annotations

import logging
import sys

import pytest

from . import support
from .support import preflight

REAL_SERVE_SCRIPT = support.REPO / "models" / "marlin2b" / "serve.sh"


# --- the manifest ------------------------------------------------------------------
def test_deploy_failclosed__the_manifest_is_the_only_source_of_env_keys():
    """Names only, one shape each, and the same mode vocabulary the runtime uses. A key
    the installer writes but the manifest does not declare would be unvalidated
    configuration, and a mode the installer accepts but the runtime does not would be a
    file that cannot start."""
    from infrx.contracts.limits import MODES

    assert preflight.MODES == MODES, "the installer and the runtime disagree about modes"
    for key in preflight.MANIFEST:
        assert key.shape in preflight.SHAPES, key.env
        assert key.role, key.env
        assert set(key.required_in) <= set(preflight.MODES), key.env
        assert set(key.forbidden_in) <= set(preflight.MODES), key.env
    declared = [key.env for key in preflight.MANIFEST]
    assert len(set(declared)) == len(declared)
    # Every role the brief requires a declared key for.
    roles = " ".join(key.role for key in preflight.MANIFEST)
    for role in ("runtime mode", "identity source", "metering sink", "price authority",
                 "model pin", "usage sink"):
        assert role in roles, role
    body = preflight.render({"MAX_INFLIGHT": "8", "INFRX_MODE": "dev", "NOT_DECLARED": "x"})
    assert body == "INFRX_MODE=dev\nMAX_INFLIGHT=8\n", "render passed an undeclared key"


def test_deploy_failclosed__an_unset_mode_is_unreachable_from_the_installer():
    """F2.2 item 14 stays open on purpose. `validate_runtime` still maps an unset
    `INFRX_MODE` to legacy behaviour - G2 inverts that, and G's `unset_mode_refuses`
    mutant keeps guarding it until then - but no install run can produce an unset mode,
    because `INFRX_MODE` is a required manifest key and `""` is not a valid mode."""
    from infrx.config import Settings, validate_runtime

    assert validate_runtime(Settings()) == "legacy"
    mode_key = next(key for key in preflight.MANIFEST if key.env == "INFRX_MODE")
    assert mode_key.required_in == preflight.MODES
    assert preflight.shape_problem(mode_key, "") is not None
    assert preflight.shape_problem(mode_key, "legacy") is not None


def test_deploy_failclosed__a_refusal_says_which_rule_the_value_broke():
    """An opaque secret has no pattern, so the universal guards are the only thing
    between an SSM value and the env file - and an operator reading the refusal needs
    to know which rule it broke, not just that "the value is not valid"."""
    key = next(k for k in preflight.MANIFEST if k.env == "SUPABASE_SERVICE_ROLE_KEY")
    good = "a" * 24
    assert "is empty" in (preflight.shape_problem(key, "") or "")
    assert "is empty" in (preflight.shape_problem(key, " " * 24) or "")
    assert "newline" in (preflight.shape_problem(key, good + "\nGATEWAY_API_KEY=x") or "")
    # systemd's EnvironmentFile gives these three a meaning `read_env` does not model.
    for special in ("'", '"', "\\"):
        assert "quote or backslash" in (preflight.shape_problem(key, good + special) or ""), special
    assert "whitespace" in (preflight.shape_problem(key, " " + good) or "")
    assert "not a valid opaque" in (preflight.shape_problem(key, "short") or "")
    assert preflight.shape_problem(key, good) is None


def test_deploy_failclosed__a_failed_read_is_classified_before_it_is_tolerated():
    """Only `ParameterNotFound` may ever be tolerated, so an unrecognised failure must
    not fall into that bucket by default."""
    assert preflight.classify("An error occurred (ParameterNotFound) when calling …"
                              ) == preflight.NOT_FOUND
    for code in ("AccessDeniedException", "ThrottlingException", "ExpiredTokenException",
                 "KMSAccessDeniedException", "EndpointConnectionError"):
        assert preflight.classify(f"An error occurred ({code}) …") == preflight.DENIED, code
    assert preflight.classify("could not connect to the endpoint URL") == preflight.UNKNOWN
    assert preflight.classify("") == preflight.UNKNOWN


# --- the runtime interpreter --------------------------------------------------------
def test_deploy_failclosed__the_runtime_interpreter_must_be_new_enough(tmp_path,
                                                                      monkeypatch):
    """Python >= 3.12.4, because CPython 3.12.0-3.12.3 answer `is_private` from older
    special-purpose tables and the media path's address policy depends on them (M1).
    A hard refusal in pilot; a warning in the explicitly permissive dev/test modes."""
    assert preflight.REQUIRED_PYTHON == (3, 12, 4)
    staged = tmp_path / "staged.env"
    staged.write_text("INFRX_MODE=pilot\n")
    monkeypatch.setattr(preflight, "REQUIRED_PYTHON", (99, 0, 0))
    pilot = preflight.probe(staged, "pilot")
    assert pilot["ok"] is False
    assert any("99.0.0 or newer" in problem for problem in pilot["problems"])

    staged.write_text("INFRX_MODE=dev\n")
    dev = preflight.probe(staged, "dev")
    assert dev["ok"] is True
    assert any("99.0.0 or newer" in warning for warning in dev["warnings"])

    monkeypatch.setattr(preflight, "REQUIRED_PYTHON", sys.version_info[:3])
    assert preflight.probe(staged, "dev")["warnings"] == []


def test_deploy_failclosed__the_mode_checked_is_the_mode_written(tmp_path):
    """The requested mode and the staged file's mode are compared once, here: a file
    that claims `pilot` while the pilot checks were skipped is the whole hazard."""
    staged = tmp_path / "staged.env"
    staged.write_text("INFRX_MODE=dev\n")
    verdict = preflight.probe(staged, "test")
    assert any("INFRX_MODE is not the requested 'test'" in problem
               for problem in verdict["problems"])


def test_deploy_failclosed__a_runtime_that_does_not_import_is_not_a_pass(tmp_path,
                                                                        monkeypatch):
    """Every later check needs the runtime package, so an import failure is fatal rather
    than a verdict with nothing in it. The problem names the exception **type**: a
    traceback in an installer's output is where paths and configuration leak."""
    staged = tmp_path / "staged.env"
    staged.write_text("INFRX_MODE=dev\n")
    monkeypatch.setitem(sys.modules, "infrx.config", None)
    verdict = preflight.probe(staged, "dev")
    assert verdict["ok"] is False
    assert verdict["problems"] == ["the runtime package does not import: ModuleNotFoundError"]


# --- the transport loggers ----------------------------------------------------------
@pytest.fixture
def transport_levels():
    """Restore whatever the rest of the suite expects: `tests/m` asserts that
    `httpcore` is at WARNING, and logger levels are process-global."""
    names = ("httpx", "httpcore", "httpcore.http11")
    before = {name: logging.getLogger(name).level for name in names}
    yield
    for name, level in before.items():
        logging.getLogger(name).setLevel(level)


def test_deploy_failclosed__a_transport_logger_below_warning_refuses_the_install(
        tmp_path, transport_levels):
    """At INFO httpx writes `HTTP Request: GET <pinned url>` for every hop - the
    validated IP and the caller's signed query - and httpcore writes the same target at
    DEBUG. The installed configuration is refused rather than leaking one URL, and the
    refusal has to come out of the probe's verdict, not only out of the check."""
    import infrx.media.fetch                      # noqa: F401 - sets the levels at import

    staged = tmp_path / "staged.env"
    staged.write_text("INFRX_MODE=dev\n")
    assert preflight.transport_logger_problems() == []
    assert preflight.probe(staged, "dev")["ok"] is True

    logging.getLogger("httpx").setLevel(logging.INFO)
    assert any("httpx" in problem for problem in preflight.transport_logger_problems())
    verdict = preflight.probe(staged, "dev")
    assert verdict["ok"] is False
    assert any("httpx" in problem for problem in verdict["problems"]), verdict


def test_deploy_failclosed__an_unset_transport_level_is_not_good_enough(transport_levels):
    """Stricter than M1's snippet, on purpose: `NOTSET` inherits the root logger, so a
    process that sets root to DEBUG leaks every URL. The two root transport names must
    carry an explicit level of their own."""
    import infrx.media.fetch                      # noqa: F401

    logging.getLogger("httpcore").setLevel(logging.NOTSET)
    problems = preflight.transport_logger_problems()
    assert any("httpcore" in problem for problem in problems), problems


def test_deploy_failclosed__a_child_transport_logger_cannot_reopen_the_leak(
        transport_levels):
    """A `dictConfig` naming `httpcore.http11` overrides what the module set at import,
    which is exactly M1's integration request 5."""
    import infrx.media.fetch                      # noqa: F401

    logging.getLogger("httpcore.http11").setLevel(logging.DEBUG)
    problems = preflight.transport_logger_problems()
    assert any("httpcore.http11" in problem for problem in problems), problems


# --- the engine ---------------------------------------------------------------------
@pytest.mark.parametrize("flag", ["--reasoning-parser deepseek_r1", "continuous_usage_stats"])
def test_deploy_failclosed__an_unsupported_engine_flag_refuses_the_install(tmp_path, flag):
    """The gateway's adapter cannot read a reasoning-parser stream and does not accept
    per-chunk usage, so an engine started with either is not a deployable engine."""
    script = support.serve_script(tmp_path, extra=flag)
    assert preflight.engine_problems(script, "dev")
    assert preflight.engine_problems(support.serve_script(tmp_path), "dev") == []


def test_deploy_failclosed__the_engine_image_must_be_pinned_by_digest_in_pilot(tmp_path):
    """A floating tag means the deployed engine is whatever the registry served that
    morning, which no measured result can be attributed to. Required in pilot only."""
    script = support.serve_script(tmp_path, image="vllm/vllm-openai:nightly")
    assert preflight.engine_problems(script, "dev") == []
    assert any("digest" in problem for problem in preflight.engine_problems(script, "pilot"))
    assert preflight.engine_problems(support.serve_script(tmp_path, image=support.PINNED),
                                     "pilot") == []


def test_deploy_failclosed__a_missing_engine_script_is_not_a_pass(tmp_path):
    """An absent file is an unchecked flag set, not an empty one."""
    assert preflight.engine_problems(tmp_path / "nowhere.sh", "dev")
    assert preflight.engine_problems(None, "dev")


def test_deploy_failclosed__the_repository_engine_script_is_checked_as_it_stands():
    """The real `models/marlin2b/serve.sh`, not a generated stand-in: it passes neither
    forbidden flag and binds the engine to loopback, and it is **not** digest-pinned and
    has no `serving-version.json` beside it, so a pilot install is refused today. Those
    two refusals are the recorded pending items, owner W3; this case changes in W3's
    commit (I2B integration request)."""
    assert REAL_SERVE_SCRIPT.exists(), REAL_SERVE_SCRIPT
    assert preflight.engine_problems(REAL_SERVE_SCRIPT, "dev") == []
    pilot = preflight.engine_problems(REAL_SERVE_SCRIPT, "pilot")
    assert len(pilot) == 2, pilot
    assert "pending on W3" in pilot[0] and pilot[1].startswith("PENDING(W3)"), pilot


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
