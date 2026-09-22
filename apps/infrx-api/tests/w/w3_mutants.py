#!/usr/bin/env python3
"""r1 R32 / R83 for W3: every invariant the W3 cases claim must be killable.

Two kinds of target, one kill rule (`tests/contracts/mutants.py`):

* **Python** (`worker/*.py`) runs through the shared pytest runner, one process per
  mutant, with W3's case files as the target.
* **The engine pin** (`models/marlin2b/serve.sh`, `serving-version.json`) cannot: the
  runner compiles every mutated file as Python. Those edits are applied to a throwaway
  copy of `models/` and the named case's own check runs in process under
  `assertion_kill`, the rule track D's migration list uses. The check must pass on the
  unmutated copy first (R83 (b)), or the mutant is `broken_runner`.

    uv run --frozen pytest -q tests/w/test_w3_mutants.py              # fast subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/w/test_w3_mutants.py
    uv run --frozen python -m tests.w.w3_mutants --list
"""
from __future__ import annotations

import pathlib
import shutil
import sys
import tempfile

from ..contracts import mutants as shared
from ..contracts.mutants import Mutant, Outcome, Result, Runner, _m
from . import test_serving as serving

S = "marlin2b/serve.sh"
J = "marlin2b/serving-version.json"
PIN_FILES = ("common/env.sh", "marlin2b/model.env", S, J)

LAUNCH = "test_perf_pilot__the_engine_starts_pinned_on_loopback_with_the_recorded_flags"
ONE_SOURCE = "test_perf_pilot__a_pinned_setting_has_one_source"
RECORD = "test_perf_pilot__the_serving_record_matches_the_code_it_pins"
PIN_CHECKS = {LAUNCH: serving.check_pinned_launch,
              ONE_SOURCE: serving.check_one_source_per_setting,
              RECORD: serving.check_record_matches_the_code}
DIGEST = "sha256:4cbfd34aac145fd1870381c030131c7f868fcad45448f401ecdb5fd4ed020b42"

PIN_MUTANTS: tuple[Mutant, ...] = (
    _m("image_is_a_moving_tag", "the engine is pulled by registry digest (R76, I0 gate)",
       S, f"IMAGE=${{IMAGE:-vllm/vllm-openai@{DIGEST}}}",
       "IMAGE=${IMAGE:-vllm/vllm-openai:nightly}", LAUNCH),
    _m("published_beyond_loopback", "the engine is reachable on loopback only",
       S, '-p "127.0.0.1:$PORT:8000"', '-p "$PORT:8000"', LAUNCH),
    _m("media_mount_writable", "the engine reads prepared media, never writes it",
       S, 'media=(-v "$PROCESSING_CACHE_DIR:$PROCESSING_CACHE_DIR:ro")',
       'media=(-v "$PROCESSING_CACHE_DIR:$PROCESSING_CACHE_DIR")', LAUNCH),
    _m("media_mounted_elsewhere", "the file:// path the adapter sends exists in the engine",
       S, 'media=(-v "$PROCESSING_CACHE_DIR:$PROCESSING_CACHE_DIR:ro")',
       'media=(-v "$PROCESSING_CACHE_DIR:/media:ro")', LAUNCH),
    _m("media_root_not_allowed", "R61 (2): the engine may open files under the root",
       S, 'flags=(--allowed-local-media-path "$PROCESSING_CACHE_DIR")', "flags=()", LAUNCH),
    _m("max_num_seqs_default_32", "the default is the recorded (pilot) value",
       S, "ENGINE_MAX_NUM_SEQS=${ENGINE_MAX_NUM_SEQS:-8}",
       "ENGINE_MAX_NUM_SEQS=${ENGINE_MAX_NUM_SEQS:-32}", LAUNCH),
    _m("max_num_seqs_ignores_its_setting", "ENGINE_MAX_NUM_SEQS is the one source",
       S, '--max-num-seqs "$ENGINE_MAX_NUM_SEQS"', "--max-num-seqs 8", ONE_SOURCE),
    _m("caller_overrides_a_pinned_flag", "a second value on the command line is refused",
       S, "--max-num-seqs*|--allowed-local-media-path*|--api-key*)", "--never-pinned*)",
       ONE_SOURCE),
    _m("api_key_passes_through", "the engine takes no API key",
       S, "|--api-key*)", ")", ONE_SOURCE),
    _m("relative_root_accepted", "the media root is absolute",
       S, "    /*) ;;", "    /*|*) ;;", ONE_SOURCE),
    _m("missing_root_accepted", "a root that does not exist is refused, not created by docker",
       S, '  test -d "$PROCESSING_CACHE_DIR" || {', "  true || {", ONE_SOURCE),
    _m("record_flag_drift", "the record's flags are the flags served",
       J, '    "bfloat16",', '    "float16",', LAUNCH),
    _m("record_digest_stale", "engine_options_digest is recomputed from the flags",
       J, '"engine_options_digest": "sha256:3c4b', '"engine_options_digest": "sha256:3c4c',
       LAUNCH),
    _m("record_names_one_eos_id", "both EOS ids are the pinned artifact's (R61 (3))",
       J, "    248044,\n", "", RECORD),
    _m("record_setting_is_not_the_contract", "the recorded max-num-seqs is the pilot setting",
       J, '"ENGINE_MAX_NUM_SEQS": "8"', '"ENGINE_MAX_NUM_SEQS": "32"', RECORD),
    _m("record_digest_source_invented", "R76: provenance is one of the recorded sources",
       J, '"digest_source": "registry_oid"', '"digest_source": "registry"', RECORD),
)


def _worded(check):
    """Outside pytest an `assert` carries no message, and the shared `_first_line` indexes
    the first line of one; give it the check's name instead."""
    def run(*args):
        try:
            check(*args)
        except AssertionError as failure:
            raise AssertionError(str(failure).strip() or f"{check.__name__} failed") from None
    return run


def run_pin_mutant(mutant: Mutant) -> Result:
    """Copy the four files, check the pristine copy, apply the one edit, check again."""
    check = _worded(PIN_CHECKS[mutant.cases[0]])
    with tempfile.TemporaryDirectory(prefix=f"w3-pin-{mutant.name}-") as tmp:
        root = pathlib.Path(tmp)
        models = root / "models"
        for name in PIN_FILES:
            (models / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(serving.MODELS / name, models / name)
        (root / "pristine").mkdir()
        baseline = shared.assertion_kill(check, models, root / "pristine")
        if baseline.outcome is not Outcome.survived:
            return Result(Outcome.broken_runner, f"pristine copy fails its own check: "
                                                 f"{baseline.detail}")
        target = models / mutant.file
        source = target.read_text()
        found = source.count(mutant.old)
        if found != mutant.occurrences:
            return Result(Outcome.misdeclared, f"anchor appears {found} times in "
                                               f"{mutant.file}: {mutant.old[:60]!r}")
        target.write_text(source.replace(mutant.old, mutant.new, 1))
        (root / "mutated").mkdir()
        return shared.assertion_kill(check, models, root / "mutated")


MUTANTS: tuple[Mutant, ...] = PIN_MUTANTS


def run(mutant: Mutant) -> Result:
    return run_pin_mutant(mutant)


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="run W3's mutation list")
    parser.add_argument("names", nargs="*")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    if args.list:
        for mutant in MUTANTS:
            print(f"{mutant.name:44s} {mutant.invariant}")
        print(f"\n{len(MUTANTS)} mutants over {len({c for m in MUTANTS for c in m.cases})} "
              f"named cases")
        return 0
    chosen = [m for m in MUTANTS if not args.names or m.name in args.names]
    bad = []
    for mutant in chosen:
        result = run(mutant)
        print(f"[{result.outcome:13s}] {mutant.name}: {result.detail}", flush=True)
        if not result.killed:
            bad.append(mutant.name)
    print(f"\n{len(chosen) - len(bad)}/{len(chosen)} killed" + (f"; not killed: {bad}" if bad else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
