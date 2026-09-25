#!/usr/bin/env python3
"""r1 R32: M1's guards must be killable.

    uv run --frozen pytest -q tests/m/test_mutants.py               # the fast subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/m/test_mutants.py    # every mutant

One pytest process per mutant, so the whole list is on demand and a subset covering one
mutant per file runs by default. A surviving mutant fails the suite either way.
"""
from __future__ import annotations

import os

import pytest

from . import mutants as mutation_list

ALL = mutation_list.MUTANTS
FULL_RUN = os.environ.get("INFRX_MUTANTS", "").lower() in ("all", "1", "true")
# One per mutated file, plus the two pins the whole path rests on.
SUBSET = ("one_answer_is_enough", "connects_to_the_name_not_the_address",
          "denied_networks_not_checked", "stage_takes_a_ref_it_never_made", "key_without_the_tenant",
          "transport_logs_not_silenced", "attach_accepts_an_unstaged_ref",
          # M2: one per new file, plus the two pins the whole preparation path rests on -
          # the measured duration the engine budgets frames from and the tenant in the
          # processing-cache path.
          "box_length_not_compared_with_the_file", "no_probe_at_materialization",
          "duration_cap_removed", "cache_path_without_the_tenant", "facts_hook_not_consulted",
          # M3: one per new file, plus the two acceptance pins - a live job's input is
          # never collected, and a refused upload stays refused.
          "upload_owner_unchecked", "liveness_ignored", "consent_org_unchecked",
          "lookup_failure_fails_open", "refusal_not_recorded",
          # M4: no new file; the two pins - a download's header reaches the profile, and a
          # prepared clip is the same file on every run.
          "early_look_not_wired", "cache_file_name_varies",
          # M5: the two pins of the durable upload - a refused PUT writes nothing (the
          # receipt first), and an admitted job's upload ref binds only its tenant's object.
          "bytes_before_the_receipt", "attach_accepts_a_forged_upload_ref")
SELECTED = ALL if FULL_RUN else tuple(m for m in ALL if m.name in SUBSET)


def test_the_list_is_well_formed():
    """A typo in a test name would make a mutant unkillable by construction and pass."""
    from . import (test_consent, test_fetch, test_gc, test_parity, test_prepare, test_probe,
                   test_store, test_upload_restart, test_uploads)

    names = {name for module in (test_fetch, test_prepare, test_probe, test_store,
                                 test_uploads, test_gc, test_consent, test_parity,
                                 test_upload_restart)
             for name in vars(module)
             if name.startswith("test_")}
    assert len({m.name for m in ALL}) == len(ALL), "duplicate mutant names"
    assert set(SUBSET) <= {m.name for m in ALL}
    for mutant in ALL:
        assert mutant.cases, f"{mutant.name} names no test"
        assert mutant.invariant, f"{mutant.name} states no invariant"
        for case in mutant.cases:
            assert case in names, f"{mutant.name} names unknown test {case}"


def test_every_anchor_is_in_the_source_as_often_as_declared():
    """M4: an edit that moves an anchor turns its mutant into `misdeclared` only when the
    full list runs (`INFRX_MUTANTS=all`); this finds it in the default suite."""
    source = {m.file: (mutation_list.API_DIR / mutation_list.PACKAGE / m.file).read_text()
              for m in ALL}
    moved = [m.name for m in ALL if source[m.file].count(m.old) != m.occurrences]
    assert moved == [], f"anchors no longer in the source: {moved}"


def test_the_mutation_list_covers_the_owned_modules():
    """R32 from the other side: a guard nothing can break is a guard nothing proves. The
    count is a floor on the two modules M1 wrote plus the address policy it reuses."""
    files = {mutant.file for mutant in ALL}
    assert files == {"media/fetch.py", "media/store.py", "media/video.py",
                     "media/probe.py", "media/prepare.py",
                     "media/uploads.py", "media/gc.py", "media/consent.py"}
    assert len(ALL) >= 77 + 30, f"only {len(ALL)} mutants declared"
    # M2's own floor, stated separately so widening M1's list cannot cover for a thin one
    m2 = [mutant for mutant in ALL if mutant.file in ("media/probe.py", "media/prepare.py")]
    assert len(m2) >= 30, f"only {len(m2)} mutants for M2's modules"
    # and M3's, for the same reason
    m3 = [mutant for mutant in ALL
          if mutant.file in ("media/uploads.py", "media/gc.py", "media/consent.py")]
    assert len(m3) >= 50, f"only {len(m3)} mutants for M3's modules"
    # and M5's durable upload adapter, on its own
    m5 = [mutant for mutant in ALL if mutant.file == "media/uploads.py"]
    assert len(m5) >= 40, f"only {len(m5)} mutants for M5's adapter"


@pytest.mark.parametrize("mutant", SELECTED, ids=[m.name for m in SELECTED])
def test_mutant_is_killed(mutant):
    result = mutation_list.run_mutant(mutant)
    assert result.killed, (f"{mutant.name} is {result.outcome} ({mutant.invariant}): "
                           f"{result.detail}. The tests {list(mutant.cases)} do not prove "
                           f"what they claim.")


def test_the_runner_cannot_report_a_false_kill():
    """A runner that counted a syntax error, an unmatched selection or a no-op edit as a
    kill would let every mutant above pass while proving nothing."""
    from ..contracts.mutants import Outcome

    checks = (
        (Outcome.broken_runner, mutation_list.Mutant(
            name="self_syntax_error", invariant="a broken copy is not a kill",
            file="media/store.py", old="    async def stage(self", new="    async def stage(self)) :::",
            cases=("test_a_foreign_or_oversize_reference_is_not_staged",))),
        (Outcome.survived, mutation_list.Mutant(
            name="self_no_op", invariant="an edit that changes nothing survives",
            file="media/store.py", old="HANDLE_PREFIX = \"med_\"",
            new="HANDLE_PREFIX = \"med_\"  # a comment changes no behaviour",
            cases=("test_a_foreign_or_oversize_reference_is_not_staged",))),
        (Outcome.survived, mutation_list.Mutant(
            name="self_wrong_test", invariant="the kill must come from the named test",
            file="media/store.py",
            old="            if existing is None or existing.digest != ref.digest:",
            new="            if False:", cases=("test_an_unstaged_payload_is_not_found",))),
        (Outcome.misdeclared, mutation_list.Mutant(
            name="self_missing_anchor", invariant="the list matches the code",
            file="media/store.py", old="this text is not in the module", new="nor is this",
            cases=("test_a_foreign_or_oversize_reference_is_not_staged",))),
        (Outcome.misdeclared, mutation_list.Mutant(
            name="self_no_case", invariant="every mutant names a test",
            file="media/store.py", old="HANDLE_PREFIX = \"med_\"", new="HANDLE_PREFIX = \"x_\"",
            cases=())),
        # pytest exits 5 when a selection collects nothing, which lands in the same
        # "this run proved nothing" bucket as a syntax error. Not a kill, which is the point.
        (Outcome.broken_runner, mutation_list.Mutant(
            name="self_unmatched_selection", invariant="a selection that runs nothing is not a kill",
            file="media/store.py", old="HANDLE_PREFIX = \"med_\"", new="HANDLE_PREFIX = \"x_\"",
            cases=("test_no_such_test_exists_anywhere",))),
    )
    for expected, mutant in checks:
        result = mutation_list.run_mutant(mutant)
        assert result.outcome is expected, f"{mutant.name}: got {result.outcome} - {result.detail}"
        assert result.ok is (expected is Outcome.killed)
