#!/usr/bin/env python3
"""R32/R40/R83 for G3: one single-edit defect per invariant `tests/g/jobs` claims.

The list is G3's; the runner and its rules are the shared one (`tests/contracts/mutants.py`):
a mutant that does not compile is `broken_runner`, every failing test must be a named case,
every death must be an assertion or a typed `DomainError` unless the mutant declares it in
`dies_by`, the list's cases must pass unmutated first, and (G6B's stricter rule, kept) every
named case must notice.

One mutated file sits outside `infrx/` (`client_example.py`, which imports
`models/marlin2b/bench.py`), so a mutant's `file` is relative to `apps/infrx-api`
(`package=""`) and the copy keeps the repository shape (the G list's `_layout`).

    uv run --frozen pytest -q tests/g/jobs/test_jobs_mutants.py                     # subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/g/jobs/test_jobs_mutants.py   # all
    uv run --frozen python -m tests.g.jobs.jobs_mutants --list
"""
from __future__ import annotations

import ast
import pathlib
import sys

API_DIR = pathlib.Path(__file__).resolve().parents[3]
SUITE = "tests/g/jobs"

if str(API_DIR) not in sys.path:        # `python tests/g/jobs/jobs_mutants.py`
    sys.path.insert(0, str(API_DIR))

from tests.contracts import mutants as shared  # noqa: E402
from tests.contracts.mutants import Mutant, Outcome, Result, Runner  # noqa: E402,F401
from tests.g.mutants import _layout  # noqa: E402

J = "infrx/gateway/routes/jobs.py"
R = "infrx/gateway/routes/relay.py"      # G2's; G3's additive seams (admit, on_async, pump)
N = "infrx/gateway/routes/ingress.py"    # the route table
ST = "infrx/contracts/fakes/state.py"    # the contract store: idempotency is its rule
X = "client_example.py"

ACCEPT = "test_api_modes__respond_async_on_chat_answers_202_only_after_the_commit"
POST_JOBS = "test_api_modes__post_jobs_is_always_async_and_applies_no_preference"
LOST_202 = "test_dur_admit__a_lost_202_retried_with_its_key_answers_the_same_job"
CHANGED = "test_dur_admit__a_changed_payload_under_the_key_is_409_and_admits_nothing"
EXPIRED_KEY = "test_dur_admit__an_expired_mapping_is_410_and_never_a_new_billable_job"
TWICE = "test_dur_admit__two_concurrent_submissions_with_one_key_admit_once"
DETACHED = "test_api_modes__a_detached_202_never_cancels_its_job"
CREDIT_202 = "test_api_modes__a_credit_async_job_is_admitted_on_its_wallet"


def _m(name, invariant, file, old, new, *cases, dies_by=()) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=file, old=old, new=new, cases=cases,
                  dies_by=tuple(dies_by))


MUTANTS: tuple[Mutant, ...] = (
    # === item 1: acceptance and the 202 (API-MODES, DUR-ADMIT) ===========================
    _m("preference_not_echoed", "Preference-Applied: respond-async when the preference was used",
       J, "            **headers, wire.HEADER_PREFERENCE_APPLIED: wire.PREFER_RESPOND_ASYNC,",
       "            **headers,", ACCEPT),
    _m("location_dropped", "the 202 names its status resource (Location)",
       J, '            HEADER_LOCATION: f"{JOBS_PATH}/{job.handle}",\n', "", ACCEPT, POST_JOBS),
    _m("retry_after_dropped", "the 202 says when to poll (Retry-After)",
       J, "            wire.HEADER_RETRY_AFTER: str(POLL_AFTER_S)})", "            })", ACCEPT),
    _m("hook_not_installed", "mounting the jobs router installs the 202 hook",
       J, "    relay.on_async = jobs.accepted\n", "", ACCEPT),
    _m("staged_refs_never_attached", "the 202 follows the attach of the staged refs",
       R, "                await _dependency(self.media.attach(job.request_id, refs))",
       "                pass", ACCEPT),
    _m("async_hook_ignores_regime", "the async path admits by regime (CREDIT on its wallet)",
       R, "        admit = self.jobs.admit_credit(prepared, idem) if self.regime == CREDIT \\",
       "        admit = self.jobs.admit_credit(prepared, idem) if False \\", CREDIT_202),
    _m("accepted_state_invented", "the 202's state is the committed one, never a constant",
       J, "                                state=self.state_of(admission, outcome),",
       "                                state=JobState.preparing,", LOST_202),
    _m("replay_state_stale", "a replay answers the original acceptance as it stands now",
       J, "        if replayed:\n            # The original acceptance",
       "        if False:\n            # The original acceptance", CREDIT_202),
    _m("replay_flag_dropped", "a replay says so in the body (idempotency_replayed)",
       J, "                                idempotency_replayed=replayed)",
       "                                idempotency_replayed=False)", LOST_202, TWICE),
    _m("deadline_from_the_gateway", "the 202's deadline is the stored one (legacy)",
       J, "            return admission.deadline_at", "            return job.bound", ACCEPT),
    _m("credit_deadline_ignores_the_caller", "a CREDIT deadline is the store's rule, both terms",
       J, "        return min(job.bound - timedelta(seconds=self.relay.grace_s),",
       "        return max(job.bound - timedelta(seconds=self.relay.grace_s),", CREDIT_202),
    _m("jobs_route_follows_prefer", "POST /v1/jobs is async whatever Prefer says",
       J, "await jobs.ingress.validated(_as_async(request), request_id)",
       "await jobs.ingress.validated(request, request_id)", POST_JOBS),
    _m("preference_applied_always", "POST /v1/jobs reports no preference applied",
       J, "        del answer.headers[wire.HEADER_PREFERENCE_APPLIED]\n", "", POST_JOBS),
    _m("detach_cancels", "the 202 path never cancels the job it accepted (the sync path does)",
       J, "        replayed, outcome = admission.replayed, None",
       "        await self.relay.cancel(job.org_id, job.handle, quiet=True)\n"
       "        replayed, outcome = admission.replayed, None", DETACHED),
    _m("replay_readmits", "a retry with the same key is the same job, one hold",
       ST, "            replay = self._replay(idem, now, credit=credit)",
       "            replay = None", LOST_202, TWICE),
    _m("changed_payload_replayed", "a changed payload under the key is 409, never a replay",
       ST, "        if record.payload_hash != idem.payload_hash:", "        if False:", CHANGED),
    _m("expired_mapping_readmits", "an expired mapping is 410, never a new billable job",
       ST, '            raise errors.IdempotencyExpired(f"idempotency key expired at '
           '{record.expires_at}")',
       "            return None", EXPIRED_KEY),
)


#: The shared runner (R83), in the repository's shape, every named case required to notice.
RUNNER = Runner(name="g3", package="", targets=(SUITE,), layout=_layout,
                extra_args=(f"--ignore={SUITE}/test_jobs_mutants.py",), require_every_case=True)


def run_mutant(mutant: Mutant) -> Result:
    return shared.run_mutant(mutant, RUNNER)


def case_names() -> set[str]:
    """Every `test_*` function in this suite except the list's own claims."""
    names = set()
    for path in sorted((API_DIR / SUITE).glob("test_*.py")):
        if path.name == "test_jobs_mutants.py":
            continue
        tree = ast.parse(path.read_text())
        names |= {n.name for n in tree.body
                  if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")}
    return names


def main() -> int:
    return shared.main(MUTANTS, RUNNER, "run G3's mutation list")


if __name__ == "__main__":
    raise SystemExit(main())
