#!/usr/bin/env python3
"""R32/R83 for WR-LDP-2 / LDP-F1 (b) / LDP-F3 / LDP-F7: one single-edit defect per decision
`tests/i/lab_control` claims, in the control factory and the datasets surface's 503.

The fake half runs in every copy; the PostgreSQL case (l4) only on request
(`INFRX_LAB_API_PG=1 INFRX_D_TASK=l4`, the LAB-API runner's rule).

    uv run --frozen pytest -q tests/i/lab_control/test_mutants.py
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/i/lab_control/test_mutants.py
    uv run --frozen python -m tests.i.lab_control.mutants --list
"""
from __future__ import annotations

from ...contracts import mutants as shared
from ...contracts.mutants import Mutant, Result
from ...g.lab_auth import mutants as auth

SUITE_FILES = ("tests/i/lab_control/test_control_routes.py",
               "tests/i/lab_control/test_control_routes_pg.py")
APP, DS, COMPOSE = "lab/control/app.py", "gateway/routes/lab_datasets.py", "lab/compose.py"
FILES = (APP, DS, COMPOSE)
C = "test_control_routes__"
MOUNTED = C + "every_lab_family_is_mounted_behind_the_session_with_no_switch"
LOGIN = C + "a_session_reaches_each_family_on_the_lab_login_never_the_runtimes"
TYPED = C + "a_failing_store_is_each_familys_typed_503_never_a_500"
LOGGED = C + "a_datasets_store_fault_logs_its_type_never_its_message"
KEYS = C + "the_checkpoint_receiver_is_mounted_with_its_key_directory"
OBJECTS = C + "the_lab_objects_are_the_workers_bucket_or_a_typed_503"
AUTH = C + "the_families_verify_sessions_with_the_labs_own_auth_settings"
PG = "test_control_routes_pg__every_family_is_served_on_the_lab_login_typed_never_a_500"
PRESENT = "test_control_routes_pg__present_records_answer_alike_on_both_logins"
VERDICT = "test_control_routes_pg__an_assigned_running_release_reads_its_verdict_on_the_lab_login"
FAMILIES = "(lab_datasets, lab_evaluations, lab_pipelines, lab_releases, lab_checkpoints)"


def _m(name, invariant, file, old, new, *cases) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=file, old=old, new=new, cases=cases)


def _switch(family: str) -> Mutant:
    return _m(f"{family}_waits_on_its_switch", "no LAB_* switch gates a family on the unit (R237)",
              APP, f" {family}=True,", f" {family}=settings.deployment.{family},", MOUNTED)


MUTANTS: tuple[Mutant, ...] = (
    # --- WR-LDP-2: every family, mounted, behind the session, the unit being the switch ------
    _m("families_unmounted", "the unit mounts every Lab family", APP,
       "        family.register(app, rt)\n", "        pass\n", MOUNTED),
    _m("datasets_unmounted", "the datasets family is served by the unit", APP,
       FAMILIES, FAMILIES.replace("lab_datasets, ", ""), MOUNTED),
    _m("releases_unmounted", "the release family is served by the unit", APP,
       FAMILIES, FAMILIES.replace("lab_releases, ", ""), MOUNTED),
    *(_switch(family) for family in ("lab_datasets", "lab_evals", "lab_pipelines",
                                     "lab_releases")),
    _m("checkpoints_wait_on_their_switch", "the receiver is mounted with its key directory",
       APP, "lab_checkpoints=bool(settings.deployment.lab_checkpoint_keys.strip())",
       "lab_checkpoints=settings.deployment.lab_checkpoints", KEYS),
    _m("checkpoints_not_composed", "the receiver is compose's composition on this login", APP,
       "**lab_checkpoints(unit, connect)}", "}", KEYS),
    # --- LDP-F1 (b) / LDP-F7: the Lab's own login, no set role, the Lab's own verifier ------
    _m("families_on_the_runtime_login", "the families never use the runtime's DATABASE_URL",
       APP, "**_families(settings, lab, connect)",
       "**_families(settings, lab, connector(settings.pilot.database_url, set_role=False))",
       LOGIN),
    _m("releases_read_no_experiments", "the release family's verdict reads B4's experiments on the "
       "Lab login (R259, 0059) - never an unwired reads port", COMPOSE,
       "PgLabVariants(connect), PgLabReads(connect)),", "PgLabVariants(connect), None),",
       VERDICT),
    _m("families_set_role", "the Lab login never sets role service_role (LDP-F7)", APP,
       "connector(lab[DATABASE_URL], set_role=False)", "connector(lab[DATABASE_URL])",
       LOGIN, PG, PRESENT),
    _m("readiness_sets_role", "readiness on the Lab login never sets a role (LDP-F7)", APP,
       "connector(os.environ[DATABASE_URL], set_role=False)",
       "connector(os.environ[DATABASE_URL])", LOGIN, PG),
    _m("families_verify_at_the_runtimes_auth", "sessions are verified at the Lab's own URL",
       APP, "                               supabase_url=lab[SUPABASE_URL].rstrip(\"/\"),\n",
       "", AUTH),
    _m("families_hold_the_service_role_key", "the families present the Lab's publishable key",
       APP, "                               supabase_key=lab[SUPABASE_KEY])",
       "                               )", AUTH),
    # --- the Lab objects -------------------------------------------------------------------
    _m("no_objects_is_none", "without the bucket the Lab objects are a typed 503", APP,
       "        else NoObjects()", "        else None", OBJECTS),
    _m("no_objects_answers", "every use of the absent Lab objects is refused", APP,
       '        raise errors.DependencyUnavailable("the Lab objects are not configured '
       '(LAB_S3_BUCKET)")', "        return None", OBJECTS),
    _m("bucket_ignored", "the unit uses the Lab workers' bucket when it is set", APP,
       'if os.environ.get("LAB_S3_BUCKET", "").strip()', "if False", OBJECTS),
    # --- LDP-F3: a store fault is the datasets surface's typed 503 --------------------------
    _m("store_fault_is_a_500", "a non-domain store fault is a 503, not a 500", DS,
       "        except Exception as failed:", "        except ImportError as failed:",
       TYPED, PG),
    _m("store_fault_is_a_400", "a store fault is the service's 503, not the caller's 400", DS,
       'refusal(errors.DependencyUnavailable("the datasets store failed"))',
       'refusal(errors.InvalidRequest("the datasets store failed"))', TYPED, PG),
    _m("store_fault_message_logged", "a store fault is logged by its type, never its message",
       DS, '"lab datasets route failed: %s", type(failed).__name__)',
       '"lab datasets route failed: %s", failed)', LOGGED),
)


def case_names() -> set[str]:
    return auth.case_names_in(SUITE_FILES)


RUNNER = auth.runner("lab-control-routes", SUITE_FILES)


def run_mutant(mutant) -> Result:
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run the lab-control-routes mutation list"))
