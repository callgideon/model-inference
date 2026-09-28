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
       "                        actor=user, accept_rejects=", "                        actor="
       '"lab", accept_rejects=', IDENTITY),
    _m("published_not_listed", "a published import is listed",
       "                    note(provider, report.dataset_ref)\n", "", IDENTITY),
    _m("body_before_identity", "an import body is read after the acting provider",
       "        async def work(provider, user):\n" + READ + "            spec = ",
       "        body = await read(request)\n\n        async def work(provider, user):\n"
       "            spec = ", ORDER),
    _m("provider_not_derived", "every call acts for a provider the session may act for",
       "            return await work(await acting_provider(access, user, provider), user)",
       "            return await work(provider, user)", ACCESS, ORDER),
    _m("malformed_body_is_a_500", "a body without the operation's fields is a 400",
       "        except (KeyError, TypeError, AttributeError):\n", "        except ():\n", BODY),
    _m("unbounded_body", "an import is one bounded request",
       "max_bytes=MAX_BODY_BYTES,", "max_bytes=MAX_BODY_BYTES * 2,", BODY),
    _m("too_large_is_a_400", "a body past the bound is a 413",
       "(errors.RequestTooLarge, 413), ", "", BODY),
)


def case_names() -> set[str]:
    return auth.case_names_in(SUITE_FILES)


RUNNER = auth.runner("lab-datasets", SUITE_FILES)


def run_mutant(mutant) -> Result:
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run WR-N4-1's datasets mutation list"))
