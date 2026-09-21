"""J — the LLM-as-judge, starting offline.

J1 is the dry-run half: which traces are eligible, which ~50 of them the calibration
design draws, what the rubric is, what a judge result must look like to be stored, and
what a live run would cost. It performs **no egress and imports no provider SDK**:
`JUDGE_MODE` defaults to `dry_run` and `JUDGE_LIVE_BUDGET_USD` to `0`, and
`cost.require_live_submission` is the guard every future submission path must pass
first. J2 owns submission and collection, J3 the calibration report.

Durable state (runs, reservations, consent history) belongs to `JudgeCoordinator`,
which D6 implements; nothing here keeps a second copy of it.
"""
from __future__ import annotations

from .cost import (APPROVED_RATES, UNPRICED_NOTE, CostEstimate, ProviderRate, RateTable,
                   StaticRateTable, TokenCeilings, estimate_worst_case,
                   live_submission_allowed, require_live_submission)
from .dryrun import DEFAULT_CEILINGS, MAX_CANDIDATES, DryRunPlan, plan_dry_run
from .rubric import (MARLIN_VIDEO_V1, Criterion, JudgeScores, Rejected, Result, Rubric, Score,
                     ScoreLedger, dedupe_key, validate_output)
from .sampling import (DEFAULT_DESIGN, CalibrationDesign, CandidateSource, Exclusion, Excluded,
                       Sample, Selection, Stratum, TraceCandidate, rank, select)

__all__ = [
    "APPROVED_RATES", "CalibrationDesign", "CandidateSource", "CostEstimate", "Criterion",
    "DEFAULT_CEILINGS", "DEFAULT_DESIGN", "DryRunPlan", "Exclusion", "Excluded", "JudgeScores",
    "MARLIN_VIDEO_V1", "MAX_CANDIDATES", "ProviderRate", "RateTable", "Rejected", "Result",
    "Rubric", "Sample", "Score", "ScoreLedger", "Selection", "StaticRateTable", "Stratum",
    "TokenCeilings", "TraceCandidate", "UNPRICED_NOTE", "dedupe_key", "estimate_worst_case",
    "live_submission_allowed", "plan_dry_run", "rank", "require_live_submission", "select",
    "validate_output",
]
