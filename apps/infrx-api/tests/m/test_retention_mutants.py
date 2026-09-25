#!/usr/bin/env python3
"""r1 R32 for M6: one single-edit mutant per guard the retention and cache cases claim.

    uv run --frozen pytest -q tests/m/test_retention_mutants.py                  # the subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/m/test_retention_mutants.py  # every one

The shared runner (`tests/contracts/mutants.py`) applies each mutant to a copy of the
package and runs the named cases there; only "the named cases failed, and only they" is a
kill. The copies run on F2C's reference adapter (`INFRX_M6_WORLDS=f2c`), so no mutant
starts a container; D10's PostgreSQL runs the same cases in the main suite, and D10's own
SQL mutants (`tests/d/d10_mutants.py`) guard the lock and recheck the race cases break.
"""
from __future__ import annotations

import os

import pytest

from ..contracts import mutants as shared
from ..contracts.mutants import Mutant, Runner
from . import test_cache_bounds, test_retention, test_upload_restart

R = "media/retention.py"
S = "media/store.py"
P = "media/prepare.py"
DELAYED = "test_a_delayed_delete_holds_its_key_until_it_is_acknowledged"
REBORN = "test_an_attach_after_the_tombstone_is_refused_and_the_key_comes_back_new"
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
ACROSS_PASSES = "test_one_collector_across_passes_keeps_nothing_between_them"
LEASE = "test_the_delete_is_sent_only_while_the_claim_has_a_request_timeout_left"
WRITERS = "test_the_runtimes_writers_register_every_object_before_writing_it"
RV03 = "test_rv03_probe_the_durable_collector_keeps_a_restarted_gateways_live_upload"
RETIRING = "test_a_write_over_a_key_being_deleted_is_a_retryable_refusal"
EVERY_STEP = "test_upload_restart__create_put_complete_and_use_each_in_another_process"
MAPS = "test_every_process_map_stays_bounded_under_many_requests"
REPLACED = "test_a_replaced_process_finds_and_expires_what_the_old_one_cached"
HIGH_WATER = "test_above_the_high_water_the_oldest_unpinned_go_down_to_the_low_water"
FULL = "test_a_cache_full_of_pinned_media_refuses_the_put_and_writes_nothing"
SWEEP_PIN = "test_the_sweep_never_removes_a_pinned_file"
CROSS_PROCESS = "test_another_process_pin_protects_the_file"
PREPARE_FULL = "test_a_full_cache_still_writes_the_durable_artifact_and_prepare_retries"
IN_FLIGHT = "test_a_put_in_flight_is_never_evicted"


def _m(name, invariant, old, new, *cases, file=R, dies_by=()) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=file, old=old, new=new, cases=cases,
                  dies_by=tuple(dies_by))


MUTANTS: tuple[Mutant, ...] = (
    # --- which keys a delete may name ---------------------------------------------------------
    _m("m6_deletes_a_name_nobody_wrote", "the delete names the key the writer wrote",
       "                await self.objects.delete(tombstone.object_key)",
       "                await self.objects.delete(tombstone.object_key + (\n"
       '                    f".g{tombstone.generation}" if tombstone.generation > 1 else ""))',
       DELAYED),
    _m("m6_any_key_deletable", "only keys M writes (media/, uploads/, payloads/) are deleted",
       '    return object_key.startswith(DELETABLE_PREFIXES) and ".." not in object_key.split("/")',
       "    return True", FOREIGN),
    _m("m6_dot_segments_deletable", "a key with a .. segment is never deleted",
       '    return object_key.startswith(DELETABLE_PREFIXES) and ".." not in object_key.split("/")',
       "    return object_key.startswith(DELETABLE_PREFIXES)", FOREIGN),
    _m("m6_foreign_rows_not_checked", "a candidate's key is checked before it is claimed",
       '        if identity.location == "object_store" and not deletable(identity.object_key):',
       "        if False:", FOREIGN),
    # --- bounded pages and bounded work -------------------------------------------------------
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
    # --- retain and report --------------------------------------------------------------------
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
    # --- what a delete is ---------------------------------------------------------------------
    _m("m6_database_content_to_the_bucket", "database content never reaches the object store",
       "        if not in_database:", "        if True:", KINDS),
    _m("m6_objects_not_deleted", "object content is deleted from the object store",
       "        if not in_database:", "        if False:", KINDS, RESTART),
    _m("m6_ack_skipped", "a finished delete is acknowledged",
       "            await self.lifecycle.acknowledge_delete(tombstone)", "            pass",
       RESTART, LOST_ACK),
    _m("m6_delete_without_lease_margin",
       "a delete is sent only with a request timeout of lease left",
       "            if lease_s - (self.clock() - asked) < self.delete_timeout_s:",
       "            if False:", LEASE),
    _m("m6_lease_margin_at_equality_kept", "exactly one request timeout left is enough",
       "            if lease_s - (self.clock() - asked) < self.delete_timeout_s:",
       "            if lease_s - (self.clock() - asked) <= self.delete_timeout_s:", LEASE),
    # --- no process state between passes ------------------------------------------------------
    _m("m6_instance_remembers_items", "a reused collector keeps nothing between passes",
       "        identity = item.identity\n",
       '        _done = self.__dict__.setdefault("_done", set())\n'
       "        if (item.content_id, item.generation) in _done:\n"
       "            return\n"
       "        _done.add((item.content_id, item.generation))\n"
       "        identity = item.identity\n", ACROSS_PASSES),
    _m("m6_failed_pass_stops_the_schedule", "a failed pass is logged and the next one runs",
       "            except Exception:\n                log.exception",
       "            except ZeroDivisionError:\n                log.exception", SCHEDULE,
       dies_by=("RuntimeError",)),
    # --- phase 2: the runtime's writers register before they write ---------------------------
    _m("m6_fetched_source_unregistered", "a fetched source has its content row first",
       "        await self._register(ContentKind.source, org_id, ref.storage_ref, digest, ref.bytes)\n",
       "", WRITERS, RETIRING, file=S),
    _m("m6_registered_after_the_write", "the row comes before the bytes, never after",
       "        await self._register(ContentKind.source, org_id, ref.storage_ref, digest, ref.bytes)\n"
       "        await self._write_once(ref.storage_ref, fetched.data, ref.mime)",
       "        await self._write_once(ref.storage_ref, fetched.data, ref.mime)\n"
       "        await self._register(ContentKind.source, org_id, ref.storage_ref, digest, ref.bytes)",
       WRITERS, RETIRING, file=S),
    _m("m6_envelope_unregistered", "the staged envelope has its content row, for its job",
       "        await self._register(ContentKind.payload, org_id, key, digest, len(payload),\n"
       "                             job_id=request.request_id)\n", "", WRITERS, RV03,
       EVERY_STEP, file=S),
    _m("m6_prepared_unregistered", "a prepared artifact has its content row, for its job",
       "                await self._register(ContentKind.prepared, ref.org_id, prepared_key,\n"
       "                                     ref.digest if body is data\n"
       "                                     else await asyncio.to_thread(digest_of, body),\n"
       "                                     len(body), job_id=job_id)\n", "",
       EVERY_STEP, file=P),
    # --- phase 2: bounded process maps --------------------------------------------------------
    _m("m6_maps_unbounded", "no process map keeps more than MAX_PROCESS_ENTRIES",
       "        while len(self) > self.limit:", "        while False:", MAPS, file=S),
    _m("m6_maps_evict_the_newest", "a map evicts its oldest entry, never the one just written",
       "            self.popitem(last=False)", "            self.popitem(last=True)", MAPS,
       file=S),
    # --- phase 2: the local cache -------------------------------------------------------------
    _m("m6_cache_sweeps_only_its_index", "the sweep reads the disk, not this process's index",
       "        for mtime, _, path in self._files():",
       "        for mtime, _, path in [(e.stored_at, e.bytes, e.local_path)\n"
       "                               for e in self.entries.values()]:", REPLACED, file=P),
    _m("m6_parts_never_swept", "a crashed put's .part goes after PART_GRACE_S",
       '            life = PART_GRACE_S if path.endswith(".part") else self.ttl_s',
       "            life = self.ttl_s", REPLACED, file=P),
    _m("m6_future_mtime_trusted", "a file dated in the future is removed, not kept for ever",
       "            if (now - mtime >= life or mtime > now + FUTURE_MTIME_SLACK_S) \\",
       "            if (now - mtime >= life) \\", REPLACED, file=P),
    _m("m6_no_high_water", "a put above max_bytes makes room or is refused",
       "        if self.max_bytes is None:\n            return\n        # A `.part`",
       "        if True:\n            return\n        # A `.part`", HIGH_WATER, FULL, file=P),
    _m("m6_evicts_newest_first", "eviction takes the oldest files first",
       "        for _, size, path in sorted(files):",
       "        for _, size, path in sorted(files, reverse=True):", HIGH_WATER, file=P),
    _m("m6_evicts_only_to_the_high_water", "eviction runs down to the low water",
       "            if total + incoming <= LOW_WATER * self.max_bytes:",
       "            if total + incoming <= self.max_bytes:", HIGH_WATER, file=P),
    _m("m6_pins_ignored", "a pinned file is never evicted or swept, by any process",
       "                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)",
       "                    pass", HIGH_WATER, SWEEP_PIN, CROSS_PROCESS, file=P),
    _m("m6_full_cache_overfills", "a cache full of pinned media refuses the put",
       "        if total + incoming > self.max_bytes:\n            raise",
       "        if False:\n            raise", FULL, PREPARE_FULL, file=P),
    _m("m6_parts_evicted_in_flight", "a put in flight is never the high water's to take",
       ' and not f[2].endswith(".part")]', "]", IN_FLIGHT, file=P),
)

RUNNER = Runner(name="m6", targets=("tests/m/test_retention.py", "tests/m/test_cache_bounds.py",
                                    "tests/m/test_upload_restart.py"),
                env=("INFRX_M6_WORLDS",))
FULL_RUN = os.environ.get("INFRX_MUTANTS", "").lower() in ("all", "1", "true")
# The acceptance pins: a foreign key is never deleted, a refusal keeps the object, database
# content never reaches the bucket, every written object has its row, a pin holds.
SUBSET = ("m6_any_key_deletable", "m6_refusal_escapes", "m6_database_content_to_the_bucket",
          "m6_fetched_source_unregistered", "m6_pins_ignored")
SELECTED = MUTANTS if FULL_RUN else tuple(m for m in MUTANTS if m.name in SUBSET)


def test_the_list_is_well_formed():
    names = {name for module in (test_retention, test_cache_bounds, test_upload_restart)
             for name in vars(module) if name.startswith("test_")}
    assert len({m.name for m in MUTANTS}) == len(MUTANTS), "duplicate mutant names"
    assert set(SUBSET) <= {m.name for m in MUTANTS}
    for mutant in MUTANTS:
        source = (shared.API_DIR / "infrx" / mutant.file).read_text()
        assert mutant.cases and mutant.invariant and mutant.new != mutant.old, mutant.name
        assert set(mutant.cases) <= names, f"{mutant.name} names an unknown case"
        assert source.count(mutant.old) == 1, f"{mutant.name}: anchor count {source.count(mutant.old)}"


@pytest.mark.parametrize("mutant", SELECTED, ids=[m.name for m in SELECTED])
def test_mutant_is_killed(mutant, monkeypatch):
    monkeypatch.setenv("INFRX_M6_WORLDS", "f2c")
    result = shared.run_mutant(mutant, RUNNER)
    assert result.killed, (f"{mutant.name} is {result.outcome} ({mutant.invariant}): "
                           f"{result.detail}. The tests {list(mutant.cases)} do not prove "
                           f"what they claim.")
