#!/usr/bin/env python3
"""R32/R40/R83 for WR-N4-1: one single-edit defect per invariant the gateway's datasets
routes (`lab_datasets`) claim.

    uv run --frozen pytest -q tests/g/lab_datasets/test_mutants.py
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/g/lab_datasets/test_mutants.py
    uv run --frozen python -m tests.g.lab_datasets.mutants --list
"""
from __future__ import annotations

from ...contracts import mutants as shared
from ...contracts.mutants import Mutant, Result
from ..lab_auth import mutants as auth

SUITE_FILES = ("tests/g/lab_datasets/test_lab_datasets.py",)
F = "gateway/routes/lab_datasets.py"
FILES = (F,)
C = "test_lab_datasets__"
MOUNT = C + "nothing_is_mounted_without_the_switch"
IDENTITY = C + "the_upload_identity_is_the_verified_lab_session_only"
ORDER = C + "the_body_is_read_only_after_the_acting_provider"
ACCESS = C + "a_viewer_and_another_providers_member_cannot_import"
BODY = C + "a_body_is_a_bounded_json_object_of_the_operation"
JOBS = C + "an_import_job_is_read_only_by_its_own_provider"
DURABLE = C + "an_import_is_one_durable_job_the_pool_works"
REQUEUE = C + "a_failed_import_is_requeued_as_a_new_job_the_pool_works"
READ = "            body = await read(request)\n"


def _m(name, invariant, old, new, *cases) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=F, old=old, new=new, cases=cases)


MUTANTS: tuple[Mutant, ...] = (
    _m("mounted_without_the_switch", "LAB_DATASETS off (no rt.lab_datasets): no route",
       "    if x is None:\n        return None\n    limits",
       "    if False:\n        return None\n    limits", MOUNT),
    _m("runtime_datasets_ignored", "register(app, rt) mounts over rt.lab_datasets",
       'getattr(rt, "lab_datasets", None)', "None", IDENTITY),
    _m("identity_from_the_token_itself", "the user is the verified session, never the token",
       "user_of=lambda request: lab_auth.authenticate(request, x.sessions))",
       'user_of=lambda request: asyncio.sleep(0, request.headers.get("authorization", "")'
       '[7:]))', IDENTITY),
    _m("unauthenticated_is_unavailable", "a missing or unverified session is a 401",
       "STATUS = ((errors.InvalidApiKey, 401), ", "STATUS = (", IDENTITY),
    _m("actor_not_the_session", "N1 records the session's user as the actor",
       "                                               actor=user))",
       '                                               actor="lab"))', IDENTITY, DURABLE),
    _m("published_not_listed", "a published import is listed",
       '                note(provider, found["report"]["dataset_ref"])\n',
       "                pass\n", IDENTITY),
    _m("body_before_identity", "an import body is read after the acting provider",
       "        async def work(provider, user):\n" + READ + "            return shown(",
       "        body = await read(request)\n\n        async def work(provider, user):\n"
       "            return shown(", ORDER),
    _m("provider_not_derived", "every call acts for a provider the session may act for",
       "            return await work(await acting_provider(access, user, provider), user)",
       "            return await work(provider, user)", ACCESS, ORDER),
    _m("malformed_body_is_a_500", "a body without the operation's fields is a 400",
       "        except (KeyError, TypeError, AttributeError):\n", "        except ():\n", BODY),
    _m("unbounded_body", "an import is one bounded request",
       "max_bytes=MAX_BODY_BYTES,", "max_bytes=MAX_BODY_BYTES * 2,", BODY),
    _m("too_large_is_a_400", "a body past the bound is a 413",
       "(errors.RequestTooLarge, 413), ", "", BODY),
    _m("import_job_other_provider", "an import job is read for the path's provider",
       "found = shown(await queue().job(import_id, provider_org_id=provider))",
       "found = shown(await queue().job(import_id, provider_org_id=user))", JOBS),
    # WR-C5-N4-ROUTE (composition-6): one durable job on 0051's queue, as the Lab reads it
    _m("import_running_as_published", "a queued or running job reads `running`",
       '.get(job["state"], "running")', '.get(job["state"], "published")', DURABLE),
    _m("import_rejected_as_failed", "refused rows read `rejected` with their report",
       '    if state == "failed" and job.get("error") == "rejected":\n', "    if False:\n",
       DURABLE),
    _m("import_error_beyond_failure", "only a failure carries its reason",
       '"error": job.get("error") if state == "failed" else None}', '"error": job.get("error")}',
       DURABLE),
    _m("import_queue_invented", "without a queue the import routes are a 503",
       "        if jobs is None:\n            raise", "        if False:\n            raise",
       DURABLE),
    # WR-C6-REQUEUE (lab-sql LW7): a failed job again as a new job the pool works unchanged
    _m("requeue_rows_not_copied", "the upload's rows are copied to the new job's id first",
       "            if rows is not None:\n", "            if False:\n", REQUEUE),
    _m("requeue_rows_other_provider", "the rows copied are the path provider's upload",
       "rows = await objects.get(imports.rows_key(provider, import_id))",
       "rows = await objects.get(imports.rows_key(user, import_id))", REQUEUE),
    _m("requeue_id_random", "the new id is derived from the failed one (a retry writes nothing)",
       'str(uuid.uuid5(uuid.NAMESPACE_URL, f"requeue:{import_id}"))', "str(uuid.uuid4())",
       REQUEUE),
    _m("requeue_other_provider", "the job requeued is the path provider's",
       "new_job_id=again,\n                                            provider_org_id=provider,",
       "new_job_id=again,\n                                            provider_org_id=user,",
       REQUEUE),
    _m("requeue_actor_not_session", "the requeuer is the session's user",
       "provider_org_id=provider, actor=user))\n        return await guarded(request, provider, "
       "work)\n\n    @api.get(\"/versions\")",
       "provider_org_id=provider, actor=\"lab\"))\n        return await guarded(request, "
       "provider, work)\n\n    @api.get(\"/versions\")", REQUEUE),
)


def case_names() -> set[str]:
    return auth.case_names_in(SUITE_FILES)


RUNNER = auth.runner("lab-datasets", SUITE_FILES)


def run_mutant(mutant) -> Result:
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run WR-N4-1's datasets mutation list"))
