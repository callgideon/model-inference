"""J — the LLM-as-judge, starting offline.

J1 is the dry-run half: which traces are eligible, which ~50 of them the calibration
design draws, what the rubric is, what a judge result must look like to be stored, and
what a live run would cost. It performs **no egress and imports no provider SDK**:
`JUDGE_MODE` defaults to `dry_run` and `JUDGE_LIVE_BUDGET_USD` to `0`, and
`cost.require_live_submission` is the guard every future submission path must pass
first (ruling R57).

Durable state (runs, reservations, consent history) belongs to `JudgeCoordinator`,
which D6 implements; nothing here keeps a second copy of it.
"""
from __future__ import annotations

from .cost import (APPROVED_RATES, JUDGE_MODE_LIVE, UNPRICED_NOTE, CostEstimate, ProviderRate,
                   RateTable, StaticRateTable, TokenCeilings, estimate_worst_case,
                   live_submission_allowed, per_sample_cost, require_live_submission, worst_case)
from .dryrun import DEFAULT_CEILINGS, MAX_CANDIDATES, DryRunPlan, plan_dry_run, scan_bound
from .rubric import (MARLIN_VIDEO_V1, MAX_DETAIL_CHARS, Criterion, DuplicateKey, JudgeScores,
                     Rejected, Result, Rubric, Score, ScoreLedger, dedupe_key, is_storable_text,
                     parse_judge_json, validate_json, validate_output)
from .sampling import (DEFAULT_DESIGN, FINISH_REASON_LENGTH, CalibrationDesign, CandidateSource,
                       Exclusion, Excluded, Sample, Selection, Stratum, TraceCandidate,
                       check_consent, deduplicate, rank, select)

__all__ = [
    "APPROVED_RATES", "CalibrationDesign", "CandidateSource", "CostEstimate", "Criterion",
    "DEFAULT_CEILINGS", "DEFAULT_DESIGN", "DryRunPlan", "DuplicateKey", "Exclusion", "Excluded",
    "FINISH_REASON_LENGTH", "JUDGE_MODE_LIVE", "JudgeScores", "MARLIN_VIDEO_V1",
    "MAX_CANDIDATES", "MAX_DETAIL_CHARS", "ProviderRate", "RateTable", "Rejected", "Result",
    "Rubric", "Sample", "Score", "ScoreLedger", "Selection", "StaticRateTable", "Stratum",
    "TokenCeilings", "TraceCandidate", "UNPRICED_NOTE", "check_consent", "dedupe_key",
    "deduplicate", "estimate_worst_case", "is_storable_text",
    "live_submission_allowed", "parse_judge_json",
    "per_sample_cost", "plan_dry_run", "rank", "require_live_submission", "scan_bound", "select",
    "validate_json", "validate_output", "worst_case",
]
