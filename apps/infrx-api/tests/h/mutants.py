#!/usr/bin/env python3
"""R32/R83 for H1: one single-edit defect per decision `tests/h` claims.

The shared runner (`tests/contracts/mutants.py`) with `require_every_case`, in the copy
layout of the F3 list (the F3 fixtures live in `packages/shared`).

    uv run --frozen pytest -q tests/h/test_mutants.py                     # subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/h/test_mutants.py   # all
    uv run --frozen python -m tests.h.mutants --list
"""
from __future__ import annotations

import ast
import dataclasses
import pathlib
import sys

API_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from tests.contracts import mutants as shared  # noqa: E402
from tests.contracts.lab.mutants import _layout  # noqa: E402
from tests.contracts.mutants import Mutant, Outcome, Result, Runner  # noqa: E402,F401

SUITE = "tests/h/test_harness.py"
P = "harnesses/replay.py"

ID = "test_h1_a_prompt_or_processor_change_is_a_new_identity"
CODE = "test_h1_arbitrary_code_is_never_a_harness"
BIND = "test_h1_binding_pins_the_revision_and_checks_the_purpose"
REAL = "test_h1_binding_asks_the_real_l2_port"
MEMBER = "test_h1_binding_checks_the_callers_current_membership"
XDEP = "test_h1_replay_targets_only_its_own_providers_deployment"
MUT = "test_h1_a_mutable_harness_ref_is_rejected"
XPROV = "test_h1_a_cross_provider_harness_fails_the_purpose_check"
REC = "test_h1_replay_answers_tools_only_from_recordings"
MISS = "test_h1_a_missing_recording_is_unsupported_not_executed"
BLOCK = "test_h1_a_mutating_tool_or_actuator_is_blocked"
UNDECL = "test_h1_an_undeclared_tool_is_unsupported"
BOUND = "test_h1_a_bound_exceeded_stops_the_run"
DEV = "test_h1_replay_only_targets_dev_endpoints"
MID = "test_h1_a_harness_changed_mid_run_is_refused"
ADAPT = "test_h1_adapters_are_built_in_and_bounded"
COV = "test_h1_coverage_reports_every_unsupported_case"
CLIP = "test_h1_an_imported_clip_reaches_the_finite_video_adapter"
B1 = "evaluation/runner/__init__.py"
SINGLE = "test_h1_a_prompt_only_variant_is_a_single_factor_comparison"
MULTI = "test_h1_multifactor_needs_an_explicit_tag_and_the_same_universe"


def m(name, invariant, old, new, *cases, dies_by=()):
    return Mutant(name=name, invariant=invariant, file=P, old=old, new=new, cases=cases,
                  dies_by=dies_by)


MUTANTS: tuple[Mutant, ...] = (
    # H1.a identity
    m("h1_identity_ignores_the_prompt", "a prompt change is a new revision",
      "return lab.ref_of(payload)", 'return lab.ref_of({**payload, "prompt_template": "x"})',
      ID, MID),
    m("h1_any_record_is_a_harness", "only a harness revision is a harness",
      'if payload.get("schema") != "lab.harness_revision.1":', "if False:", CODE),
    # H1.c binding
    m("h1_bind_skips_the_rights_port", "binding asks L2 before scheduling",
      "    await rights.authorize(lab.Gate.schedule,",
      "    None and await rights.authorize(lab.Gate.schedule,",
      BIND, XPROV, MEMBER),
    m("h1_bind_forwards_no_principal", "L2 is asked about the calling user's membership",
      "lab.Gate.schedule, user_id=user_id,", "lab.Gate.schedule, user_id=None,", BIND, MEMBER),
    m("h1_bind_asks_the_wrong_gate", "binding is the scheduling gate",
      "rights.authorize(lab.Gate.schedule,", "rights.authorize(lab.Gate.access,", BIND),
    m("h1_bind_validates_the_old_run", "the pinned ref itself is validated first",
      "record = lab.parse(bound)", "record = lab.parse(run)", MUT, XPROV),
    # H1.b replay
    m("h1_prod_endpoint_allowed", "replay targets dev deployments only",
      "if deployment.environment is not v2.Environment.dev:", "if False:", DEV),
    m("h1_replay_on_another_providers_deployment", "replay targets the harness provider's deployment",
      "if deployment.provider_org_id != self._revision.provider_org_id:", "if False:", XDEP),
    m("h1_mutating_tool_replayed", "network mutation is blocked like an actuator",
      'if tool.effect != "read_only":', 'if tool.effect == "actuator":', BLOCK),
    m("h1_undeclared_tool_mapped", "an undeclared tool is unsupported",
      'tool = tools.get(call["name"])', 'tool = tools.get(call["name"]) or rev.tools[0]', UNDECL),
    m("h1_missing_recording_filled", "a missing recording is unsupported, never invented",
      'call.get("arguments", {})), _MISSING)', 'call.get("arguments", {})), {})', MISS),
    m("h1_recording_ignores_arguments", "a recording answers only its own arguments",
      "hashlib.sha256(lab.canonical(arguments))", 'hashlib.sha256(b"")', REC),
    m("h1_prompt_not_rendered", "the prompt is the template over the mapped input",
      "prompt = _PLACEHOLDER.sub(lambda m: str(values.get(m.group(1), m.group(0))), rev.prompt_template)",
      "prompt = rev.prompt_template", REC),
    m("h1_request_bound_off_by_one", "max_requests is the number of calls",
      "if self._requests >= self._bounds.max_requests:", "if self._requests > self._bounds.max_requests:",
      BOUND),
    m("h1_time_unbounded", "the run is bounded in wall time",
      "if self._clock() - self._start > self._bounds.max_seconds:",
      "if False and self._clock() - self._start > self._bounds.max_seconds:", BOUND),
    m("h1_bytes_unbounded", "the run is bounded in bytes",
      "if self._bytes > self._bounds.max_bytes:", "if False:", BOUND),
    m("h1_stop_forgotten", "a stopped run stays stopped", "if self._stopped:", "if False:", BOUND),
    m("h1_mid_run_edit_accepted", "a harness edited mid-run is refused",
      "if revision_ref(self._payload) != self._ref:", "if False:", MID),
    m("h1_missing_input_rendered", "a missing input is unsupported", "if missing:", "if False:", ADAPT),
    m("h1_video_cap_moved", "finite video stops at 82 s",
      '1 <= values["duration_ms"] <= lab.MAX_VIDEO_MS', '1 <= values["duration_ms"] <= lab.MAX_VIDEO_MS + 1',
      ADAPT),
    m("h1_video_media_dropped", "the clip is passed by reference",
      'media = [values["media_ref"]]', "media = []", ADAPT),
    # E6L-O1 (B1's side of the seam, proved through H1's adapter)
    Mutant(name="b1_clip_media_ref_dropped", invariant="a clip is passed as its N1 media key",
           file=B1, old='"media_ref": media_key(provider_org_id, content["media_digest"]),',
           new='"media_ref": content["media_digest"],', cases=(CLIP,)),
    Mutant(name="b1_clip_duration_is_the_end", invariant="duration_ms is the span's length",
           file=B1, old='"duration_ms": end - start}', new='"duration_ms": end}', cases=(CLIP,)),
    Mutant(name="b1_clip_inputs_never_added", invariant="a clip carries H1's video inputs",
           file=B1, old='    if "media_digest" in content:\n        start, end',
           new='    if False:\n        start, end', cases=(CLIP,)),
    m("h1_text_adapter_parses_json", "only the structured adapter parses output",
      'if rev.adapter != "structured":', 'if rev.adapter == "text":', ADAPT),
    m("h1_bad_json_scored_complete", "unparseable structured output is a failure",
      'return Outcome("failed", None, ("output_not_json",))', 'return Outcome("complete", text, ())',
      ADAPT),
    m("h1_coverage_drops_failures", "a failed case is comparable",
      'return self.status in ("complete", "failed")', 'return self.status in ("complete",)', COV),
    # H1.c comparisons
    m("h1_harness_not_a_factor", "a prompt-only variant is a factor",
      'FACTORS = ("serving_ref", "harness_ref", "seed")', 'FACTORS = ("serving_ref", "seed")', SINGLE),
    m("h1_untagged_multifactor", "a multifactor comparison is tagged",
      "if len(differ) > 1 and not multifactor_tag:", "if False:", MULTI),
    m("h1_universe_unchecked", "runs over different universes are not comparable",
      "if any(getattr(ra, f) != getattr(rb, f) for f in UNIVERSE):", "if False:", MULTI),
)


def case_names() -> set[str]:
    tree = ast.parse((API_DIR / SUITE).read_text())
    return {node.name for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")}


#: WR-H1-1: the binding decisions again, killed through the real L2 port on PostgreSQL
#: (`-m pg`, the D harness of `INFRX_D_TASK`; the copy reaches the migrations via its
#: `apps/app` link).
PG_MUTANTS: tuple[Mutant, ...] = tuple(
    dataclasses.replace(m, name=f"pg_{m.name}", cases=(REAL,))
    for m in MUTANTS if m.name in ("h1_bind_skips_the_rights_port", "h1_bind_forwards_no_principal"))

RUNNER = Runner(name="h1", targets=(SUITE,), layout=_layout, require_every_case=True,
                extra_args=("-m", "not pg"))
PG_RUNNER = Runner(name="h1-pg", targets=(SUITE,), layout=_layout, require_every_case=True,
                   extra_args=("-m", "pg"), env=("INFRX_D_TASK",))


def run_mutant(mutant: Mutant) -> Result:
    """The PostgreSQL list has no module baseline, so its case runs unmutated first, once
    per process (R83 (b))."""
    if mutant not in PG_MUTANTS:
        return shared.run_mutant(mutant, RUNNER)
    return shared.pristine((REAL,), PG_RUNNER) or shared.run_mutant(mutant, PG_RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run the H1 harness mutation list"))
