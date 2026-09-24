#!/usr/bin/env python3
"""R32/R40/R83 for MPILOT: one single-edit defect per invariant `test_pilot_media.py` claims.

Its own list beside M's (`tests/m/mutants.py` keeps M1-M4's floors and file set): the same
shared runner - one mutant at a time in a throwaway copy, the named cases run there, a
pristine baseline first, and only an assertion or a typed `DomainError` counts as a kill
unless the mutant declares its death in `dies_by`.

Several edits repeat an anchor M's list already mutates (`resolve_owned`'s guards): the
defect is the same, the killer is new - proof that the admission path (`materialize` of an
`infrx-upload:` source) is guarded by those lines too, not only `stage`.

    uv run --frozen pytest -q tests/m/test_pilot_mutants.py
    uv run --frozen python -m tests.m.pilot_mutants --list
"""
from __future__ import annotations

import pathlib
import re
import shutil

from ..contracts import mutants as shared
from ..contracts.mutants import Mutant, Outcome, Result, Runner   # noqa: F401

API_DIR = pathlib.Path(__file__).resolve().parents[2]
SUITE_FILE = "tests/m/test_pilot_media.py"
S = "media/store.py"
R = "media/prepare.py"
U = "media/uploads.py"
A = "media/attachments.py"

E2E = "test_mpilot__an_upload_named_in_a_job_over_the_mounted_gateway"
SECOND = "test_mpilot__a_second_process_resolves_the_attach_and_the_local_file"
SECOND_PG = "test_mpilot_pg__a_second_process_resolves_the_attach_and_the_local_file"
WRITE_ONCE = "test_mpilot__the_exported_write_once_case_runs_on_the_store_alone"
WRITE_ONCE_PG = "test_mpilot_pg__an_attach_is_write_once_and_tenant_bound"


def _m(name, invariant, file, old, new, *cases, dies_by=(), occurrences=1) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=file, old=old, new=new, cases=cases,
                  dies_by=tuple(dies_by), occurrences=occurrences)


MUTANTS: tuple[Mutant, ...] = (
    # === item 1: an `infrx-upload:` source is resolved at admission ======================
    _m("upload_ref_not_resolved",
       "an infrx-upload: source is the finalized upload, not a 400 (gap 1)",
       U, "        if not source.startswith(UPLOAD_REF_SCHEME):\n"
          "            return await super().materialize(org_id, source)",
       "        if True:\n            return await super().materialize(org_id, source)",
       "test_mpilot__a_chat_naming_a_finalized_upload_is_prepared_from_the_store", E2E),
    _m("admission_ignores_the_owner",
       "another org's upload handle is not_found at admission (ownership)",
       S, "        media = self.refs.get((org_id, ref))",
       "        media = next((m for (_, h), m in self.refs.items() if h == ref), None)",
       "test_mpilot__another_orgs_upload_is_not_found_at_admission", E2E),
    _m("admission_accepts_an_unfinalized_upload",
       "a handle whose upload is not finalized is refused at admission, squatted or not",
       U, "                and upload.state is not UploadState.finalized:",
       "                and False:",
       "test_mpilot__an_unfinalized_upload_is_refused_at_admission"),
    _m("admission_accepts_an_expired_upload",
       "an upload past its window is 410 upload_expired at admission and at staging",
       U, "        if upload is not None and upload.org_id == org_id and self.now() >= "
          "upload.expires_at:",
       "        if False:",
       "test_mpilot__an_upload_past_its_window_is_upload_expired_at_admission", E2E),
    _m("admission_skips_the_size_bound",
       "an upload over the media bound is 413 at admission, like any source",
       R, "        budget.spend(ref.bytes)", "        budget.spend(0)",
       "test_mpilot__an_upload_over_the_media_bound_is_refused_at_admission", E2E),
    _m("admission_resolves_a_changed_object",
       "the ref names the object finalize verified: replaced or collected bytes are not_found",
       U, "                and await self.objects.head(media.storage_ref) != media.digest:",
       "                and False:",
       "test_mpilot__an_upload_whose_object_changed_is_refused_at_admission"),
    # === item 2: a second process resolves the attach and the local file ===============
    _m("attach_not_persisted",
       "the attach is written to the durable record another process reads (gap 2)",
       S, "            await self.attachments.put(job_id, owned)", "            pass",
       SECOND),
    _m("attach_not_read_back",
       "a process that did not attach reads the job's refs from the durable record",
       S, "            refs = await self.attachments.get(job_id)", "            refs = None",
       SECOND),
    _m("prepare_reads_only_this_process",
       "preparation reads the attach wherever it is recorded, not only this process's copy",
       R, "        sources = await self.attached(job_id)",
       "        sources = self.by_job.get(job_id)", SECOND),
    _m("local_uri_misses_the_disk",
       "local_uri finds a file another process prepared (the worker's lookup)",
       R, "self.cache.get(ref.org_id, ref.digest, ref.profile_version, ref.mime)",
       "self.cache.get(ref.org_id, ref.digest, ref.profile_version)",
       SECOND, "test_mpilot__a_worker_runs_a_video_job_prepared_in_another_process"),
    _m("index_rebuilt_without_the_hash",
       "a file found on disk is served only if its bytes are the key's content hash",
       R, "        if digest_of(data) != digest or stored_at > self.clock() + "
          "FUTURE_MTIME_SLACK_S:\n            return None\n",
       "        pass\n", "test_mpilot__a_cache_file_that_is_not_the_hash_is_not_served"),
    # review H-N1/H-N4/PAR-4 (nonblocking, folded in)
    _m("disk_hash_compared_by_prefix", "H-N1: the whole content hash, not the path's 16 hex",
       R, "        if digest_of(data) != digest or", "        if digest_of(data)[:23] != digest[:23] or",
       "test_mpilot__a_cache_file_that_is_not_the_hash_is_not_served"),
    _m("disk_symlink_followed", "PAR-4: a symlink at the cache path is not served",
       R, "os.O_RDONLY | os.O_NOFOLLOW", "os.O_RDONLY",
       "test_mpilot__a_cache_file_that_is_not_the_hash_is_not_served"),
    _m("future_mtime_believed", "PAR-4: a file dated in the future does not extend its life",
       R, " or stored_at > self.clock() + FUTURE_MTIME_SLACK_S:", ":",
       "test_mpilot__a_cache_file_that_is_not_the_hash_is_not_served"),
    _m("disk_lookup_without_a_root", "H-N4: no cache root, no disk lookup: not_found",
       R, "        if not self.enabled:\n            return None\n        org_id, digest, profile = key",
       "        org_id, digest, profile = key",
       "test_mpilot__with_no_cache_root_a_prepared_ref_is_not_found"),
    _m("stale_entry_served_after_expiry",
       "a file found on disk lives from when it was written, not from when it was found",
       R, "                stored_at = os.fstat(handle.fileno()).st_mtime",
       "                stored_at = self.clock()",
       "test_mpilot__a_cache_file_past_its_life_is_not_served_by_another_process"),
    _m("put_leaves_the_file_time",
       "the file's mtime is the entry's stored_at, so another process reads the same life",
       R, "        os.utime(temporary, (stored_at, stored_at))", "        pass",
       "test_mpilot__a_cache_file_past_its_life_is_not_served_by_another_process"),
    _m("disk_entry_trusted_for_its_facts",
       "an entry found on disk carries no measurement: preparation re-measures it",
       R, "            if entry is None or entry.probed is None \\\n",
       "            if entry is None \\\n",
       SECOND, dies_by=("AttributeError",)),
    # --- review PAR-1/PAR-2: write-once in process (the store alone, no durable record) ---
    _m("in_process_rebind_accepted",
       "a job bound in this process is never re-bound: other refs, superset, subset, reorder",
       S, "        if bound is not None and bound != owned:", "        if False:", WRITE_ONCE),
    _m("in_process_duplicate_accepted", "one attach naming a ref twice is invalid_request",
       S, "        if len({ref.handle for ref in owned}) != len(owned):", "        if False:",
       WRITE_ONCE),
    # --- review PAR-3/H-B2: the durable attach on the relay's paths (O4/O5/O6) -------------
    _m("bound_here_before_it_is_durable",
       "O4: a failed durable write leaves no in-process binding, so the retry attaches",
       S, "        if self.attachments is not None:            # durable first (MPILOT gap 2)\n"
          "            await self.attachments.put(job_id, owned)\n"
          "        self.by_job[job_id] = owned\n",
       "        self.by_job[job_id] = owned\n"
       "        if self.attachments is not None:            # durable first (MPILOT gap 2)\n"
       "            await self.attachments.put(job_id, owned)\n",
       "test_mpilot__a_failed_durable_attach_binds_nothing_here"),
    _m("attached_with_no_media_reads_unbound",
       "O5: () bound in this process is bound, whatever the record (no row for a text job)",
       S, "        if refs is None and self.attachments is not None:",
       "        if not refs and self.attachments is not None:",
       "test_mpilot__a_text_job_attached_here_is_bound_whatever_the_record_holds"),
    _m("replay_read_unguarded",
       "O6: the durable read in _resume is a dependency - a database failure is a 503",
       "gateway/routes/relay.py",
       "        if await _dependency(self.media.attached(job.request_id)) is not None:",
       "        if await self.media.attached(job.request_id) is not None:",
       "test_mpilot__a_replay_whose_attach_record_is_unreachable_is_retryable"),
    _m("attach_record_not_composed",
       "create_app from settings gives M's store the durable attach record on D's pool",
       "gateway/pilot.py", "        job_org=relay.job_org, attachments=attachments)",
       "        job_org=relay.job_org)",
       "test_mpilot__the_pilot_composition_records_the_attach_on_its_pool"),
)

# === item 2 on PostgreSQL: the attach record's SQL (Docker; `INFRX_D_TASK` picks the port) ==
PG_MUTANTS: tuple[Mutant, ...] = (
    _m("attach_not_persisted_pg",
       "the attach is written to D2's staged tables another process reads",
       S, "            await self.attachments.put(job_id, owned)", "            pass",
       SECOND_PG, "test_mpilot_pg__the_exported_mpilot_cases_run_on_postgresql"),
    _m("another_jobs_refs_returned",
       "a job reads back its own refs, never another job's",
       A, "where m.job_id = %s and", "where %s::text is not null and",
       "test_mpilot_pg__each_job_reads_back_its_own_refs_in_order"),
    _m("attach_order_lost", "a job's refs come back in the order they were attached",
       A, "order by m.position\")", "order by m.position desc\")",
       "test_mpilot_pg__each_job_reads_back_its_own_refs_in_order"),
    _m("rebind_to_other_refs_accepted", "a job bound to its refs is never re-bound to others",
       A, "                if bound != given:", "                if False:", WRITE_ONCE_PG),
    # review PAR-1: the three rebinds the round-1 check let through or never tried
    _m("rebind_to_a_superset_accepted", "a bound job is not extended by a longer attach",
       A, "                if bound != given:", "                if bound != given[:len(bound)]:",
       WRITE_ONCE_PG),
    _m("rebind_reordered_accepted", "a bound job's order is its order: a reorder is a conflict",
       A, "                if bound != given:", "                if sorted(bound) != sorted(given):",
       WRITE_ONCE_PG),
    _m("rebind_to_other_content_accepted",
       "the binding is compared by handle AND digest, not by handle alone",
       A, "                if bound != given:",
       "                if [h for h, _ in bound] != [h for h, _ in given]:", WRITE_ONCE_PG),
    _m("existing_binding_ignored", "an attach inserts only when the job has no binding yet",
       A, "            if bound:\n", "            if False:\n", WRITE_ONCE_PG,
       dies_by=("UniqueViolation",)),      # the exact replay re-inserts its own row
    _m("attach_not_serialized", "attaches of one job wait for its row (for update)",
       A, " and org_id = %s for update\"", " and org_id = %s\"",
       "test_mpilot_pg__an_attach_waits_for_the_job_row"),
    _m("tenant_unchecked_at_the_lock", "R55: a ref of another org than the job is not_found",
       A, "where request_id = %s and org_id = %s for update",
       "where request_id = %s and %s::uuid is not null for update", WRITE_ONCE_PG),
    _m("recorded_content_unchecked",
       "a handle recorded with other content is a conflict, never bound",
       A, "                    if recorded != ref.digest:", "                    if False:",
       WRITE_ONCE_PG),
)


def case_names() -> set[str]:
    """Every case `test_pilot_media.py` defines."""
    return set(re.findall(r"^def (test_\w+)\(", (API_DIR / SUITE_FILE).read_text(), re.M))


#: The shared runner's default copy (package, tests, pyproject) is all these cases need.
RUNNER = Runner(name="mpilot", targets=(SUITE_FILE,))


def _pg_layout(root: pathlib.Path) -> pathlib.Path:
    """The default copy, one level down as `apps/infrx-api`, beside a copy of the migrations
    `infrx.state.migrations` reads from `<repo>/apps/app/supabase/migrations` - the D harness
    builds its template database from them."""
    api = root / "apps" / "infrx-api"
    api.mkdir(parents=True)
    shared._copy(api, RUNNER)
    migrations = pathlib.Path("apps", "app", "supabase", "migrations")
    shutil.copytree(API_DIR.parents[1] / migrations, root / migrations)
    return api


#: The PostgreSQL cases: the copy inherits the D harness's task (`INFRX_D_TASK`), so it
#: provisions that task's own container and port, never the default one.
PG_RUNNER = Runner(name="mpilot-pg", targets=(SUITE_FILE,), env=("INFRX_D_TASK",),
                   layout=_pg_layout)


def run_mutant(mutant) -> Result:
    """Apply one mutant to a throwaway copy and run the cases it names. The PostgreSQL list
    is not a module's `MUTANTS`, so the shared runner would take no baseline for it: its
    cases run unmutated first, once per process (R83 (b))."""
    if mutant not in PG_MUTANTS:
        return shared.run_mutant(mutant, RUNNER)
    cases = tuple(sorted({case for m in PG_MUTANTS for case in m.cases}))
    return shared.pristine(cases, PG_RUNNER) or shared.run_mutant(mutant, PG_RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run MPILOT's mutation list"))
