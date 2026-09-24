#!/usr/bin/env python3
"""r1 R32 for M6: one single-edit mutant per guard the retention cases claim.

    uv run --frozen pytest -q tests/m/test_retention_mutants.py                  # the subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/m/test_retention_mutants.py  # every one

The shared runner (`tests/contracts/mutants.py`) applies each mutant to a copy of the
package and runs the named cases there; only "the named cases failed, and only they" is a
kill. The copies run on the two in-memory worlds (`INFRX_M6_WORLDS=f2c,fake`), so no
mutant starts a container; the PostgreSQL world runs the same cases in the main suite.
"""
from __future__ import annotations

import os

import pytest

from ..contracts import mutants as shared
from ..contracts.mutants import Mutant, Runner
from . import test_lifecycle_standin, test_retention

R = "media/retention.py"
DELAYED = "test_a_delayed_delete_cannot_remove_the_next_generation_at_the_same_key"
REBORN = "test_a_tombstoned_key_refuses_new_use_and_a_deleted_one_is_the_next_generation"
FOREIGN = "test_a_row_naming_a_key_outside_the_media_prefixes_is_never_deleted"
PAGES = "test_candidates_come_from_the_store_in_bounded_pages"
FAILED_DELETE = "test_a_failed_object_delete_stays_tombstoned_unreadable_and_is_retried"
OUTAGE = "test_an_unavailable_database_deletes_nothing_and_says_so"
ATTACH_RACE = "test_an_attach_before_the_claim_or_the_tombstone_keeps_the_object"
CLAIM_EXPIRED = "test_a_collector_whose_claim_expired_deletes_nothing"
LOST_ACK = "test_a_lost_delete_acknowledgement_is_finished_by_a_later_pass"
RESTARTED = "test_a_runtime_restarted_mid_delete_finishes_it_and_keeps_live_work"
RESTART = "test_a_restarted_collector_keeps_a_live_jobs_source"
KINDS = "test_every_content_kind_goes_and_the_financial_metadata_stays"
SCRUB_FAILED = "test_a_failed_scrub_keeps_the_expired_result_unreadable_and_is_retried"
SCHEDULE = "test_the_schedule_survives_a_failed_pass"


def _m(name, invariant, old, new, *cases, dies_by=()) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=R, old=old, new=new, cases=cases,
                  dies_by=tuple(dies_by))


MUTANTS: tuple[Mutant, ...] = (
    # --- where a generation lives, and which keys a row may name ---------------------------
    _m("m6_generation_ignored", "a delete names its generation's object, never a later one",
       '    return object_key if generation == 1 else f"{object_key}.g{generation}"',
       "    return object_key", DELAYED, REBORN),
    _m("m6_any_key_deletable", "only keys M writes (media/, uploads/, payloads/) are deleted",
       '    return object_key.startswith(DELETABLE_PREFIXES) and ".." not in object_key.split("/")',
       "    return True", FOREIGN),
    _m("m6_dot_segments_deletable", "a key with a .. segment is never deleted",
       '    return object_key.startswith(DELETABLE_PREFIXES) and ".." not in object_key.split("/")',
       "    return object_key.startswith(DELETABLE_PREFIXES)", FOREIGN),
    _m("m6_foreign_rows_not_checked", "a candidate's key is checked before it is claimed",
       '        if identity.location == "object_store" and not deletable(identity.object_key):',
       "        if False:", FOREIGN),
    # --- bounded pages and bounded work -----------------------------------------------------
    _m("m6_page_unbounded", "each read asks the store for at most page_size candidates",
       "limit=self.page_size)", "limit=1000)", PAGES),
    _m("m6_first_page_only", "a pass follows the cursor until the store has no more",
       "            if cursor is None:\n                break\n        return report",
       "            break\n        return report", PAGES),
    _m("m6_concurrency_unbounded", "at most `concurrency` candidates are in flight",
       "asyncio.Semaphore(self.concurrency)", "asyncio.Semaphore(1_000)", PAGES),
    _m("m6_work_after_abort", "nothing new starts once the pass has stopped",
       "                if report.aborted is None:\n                    await self._collect(item, report)",
       "                await self._collect(item, report)", FAILED_DELETE),
    # --- retain and report ----------------------------------------------------------------
    _m("m6_candidates_outage_escapes", "an unavailable store ends the pass, reported",
       '            except errors.DependencyUnavailable:\n                report.aborted = "dependency_unavailable"\n                break',
       "            except ZeroDivisionError:\n                break", OUTAGE),
    _m("m6_claim_outage_escapes", "an outage at claim or tombstone ends the pass, reported",
       '        except errors.DependencyUnavailable:\n            report.aborted = "dependency_unavailable"\n            return\n        except (errors.NotClaimable',
       "        except ZeroDivisionError:\n            return\n        except (errors.NotClaimable",
       OUTAGE),
    _m("m6_refusal_escapes", "a refused claim or tombstone keeps the object and the pass goes on",
       "        except (errors.NotClaimable, errors.StaleLease, errors.NotFound) as refused:",
       "        except errors.NotFound as refused:", ATTACH_RACE, CLAIM_EXPIRED),
    _m("m6_delete_failure_does_not_stop", "a store that refuses a delete ends the pass",
       '                report.aborted = "object_store_unavailable"\n                return',
       "                pass", FAILED_DELETE),
    _m("m6_delete_failure_unreported", "a failed delete is counted",
       "                report.delete_failed += 1\n", "", FAILED_DELETE),
    _m("m6_lost_ack_unreported", "a lost acknowledgement is counted",
       "            report.ack_lost += 1\n", "", LOST_ACK, SCRUB_FAILED),
    _m("m6_lost_ack_does_not_stop", "a store that loses an acknowledgement ends the pass",
       '            report.ack_lost += 1\n            report.aborted = "dependency_unavailable"\n',
       "            report.ack_lost += 1\n", SCRUB_FAILED),
    _m("m6_pending_age_unmeasured", "the age of an unfinished delete is measured",
       "            if item.tombstoned_at is not None:", "            if False:",
       LOST_ACK, RESTARTED),
    # --- what a delete is -------------------------------------------------------------------
    _m("m6_database_content_to_the_bucket", "database content never reaches the object store",
       "        if not in_database:", "        if True:", KINDS),
    _m("m6_objects_not_deleted", "object content is deleted from the object store",
       "        if not in_database:", "        if False:", KINDS, RESTART),
    _m("m6_ack_skipped", "a finished delete is acknowledged",
       "            await self.lifecycle.acknowledge_delete(tombstone)", "            pass",
       RESTART, LOST_ACK),
    _m("m6_failed_pass_stops_the_schedule", "a failed pass is logged and the next one runs",
       "            except Exception:\n                log.exception",
       "            except ZeroDivisionError:\n                log.exception", SCHEDULE,
       dies_by=("RuntimeError",)),
)

RUNNER = Runner(name="m6", targets=("tests/m/test_retention.py",
                                    "tests/m/test_lifecycle_standin.py"),
                env=("INFRX_M6_WORLDS",))
FULL_RUN = os.environ.get("INFRX_MUTANTS", "").lower() in ("all", "1", "true")
# The acceptance pins: a later generation survives, a foreign key is never deleted, a
# refusal keeps the object, database content never reaches the bucket.
SUBSET = ("m6_generation_ignored", "m6_any_key_deletable", "m6_refusal_escapes",
          "m6_database_content_to_the_bucket")
SELECTED = MUTANTS if FULL_RUN else tuple(m for m in MUTANTS if m.name in SUBSET)


def test_the_list_is_well_formed():
    names = {name for module in (test_retention, test_lifecycle_standin)
             for name in vars(module) if name.startswith("test_")}
    source = (shared.API_DIR / "infrx" / R).read_text()
    assert len({m.name for m in MUTANTS}) == len(MUTANTS), "duplicate mutant names"
    assert set(SUBSET) <= {m.name for m in MUTANTS}
    for mutant in MUTANTS:
        assert mutant.cases and mutant.invariant and mutant.new != mutant.old, mutant.name
        assert set(mutant.cases) <= names, f"{mutant.name} names an unknown case"
        assert source.count(mutant.old) == 1, f"{mutant.name}: anchor count {source.count(mutant.old)}"


@pytest.mark.parametrize("mutant", SELECTED, ids=[m.name for m in SELECTED])
def test_mutant_is_killed(mutant, monkeypatch):
    monkeypatch.setenv("INFRX_M6_WORLDS", "f2c,fake")
    result = shared.run_mutant(mutant, RUNNER)
    assert result.killed, (f"{mutant.name} is {result.outcome} ({mutant.invariant}): "
                           f"{result.detail}. The tests {list(mutant.cases)} do not prove "
                           f"what they claim.")
