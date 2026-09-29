#!/usr/bin/env python3
"""R32/R40/R83 for WR-C6-CAPTURE: one single-edit defect per decision `tests/t/capture` claims.

The runner is the shared one (`tests/contracts/mutants.py`) over `test_capture.py`. The
PostgreSQL half (`PG_MUTANTS`) is killed in process, as T2F's is: a copy of the tree has no
migrations to apply, so each `check_*` of `test_capture_pg.py` runs against a mutated copy of
the module on a fresh world of the lane's task-local database (`INFRX_D_TASK`).

    uv run --frozen pytest -q tests/t/capture/test_mutants.py                          # subset
    INFRX_D_TASK=t2f INFRX_MUTANTS=all uv run --frozen pytest -q tests/t/capture/test_mutants.py
"""
from __future__ import annotations

import ast
import pathlib
import sys
import types

API_DIR = pathlib.Path(__file__).resolve().parents[3]
SUITE = "tests/t/capture/test_capture.py"

if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from tests.contracts import mutants as shared  # noqa: E402
from tests.contracts.mutants import Mutant, Outcome, Result, Runner  # noqa: E402,F401

C = "gateway/capture.py"
I = "gateway/routes/ingress.py"

LOWER = "test_a_consented_key_under_a_consenting_org_captures_at_the_lower_of_the_two"
OPT_IN = "test_a_key_that_never_opted_in_or_an_org_without_consent_is_off"
IN_FORCE = "test_a_revoked_or_not_yet_effective_consent_is_off"
HEAD = "test_the_policy_carries_the_consent_head_and_evaluation_only_at_full"
TTL = "test_consent_is_read_once_per_key_within_the_ttl_and_again_after_it"
BOUNDED = "test_the_consent_cache_is_bounded"
FAILS = "test_a_consent_read_that_fails_is_off_and_never_raises"
SQL = "test_the_sql_reads_the_key_of_its_own_org_and_the_consent_head"
RUNTIME = "test_the_runtime_login_without_a_grant_reads_off"
WITHOUT = "test_without_a_composed_capture_the_ingress_policy_is_off"
PG_OPT_IN = "check_the_keys_opt_in_under_its_orgs_consent_head"
PG_REVOKED = "check_a_revoked_head_is_off_not_an_older_consent"
SEAM = "test_the_ingress_admits_with_the_composed_capture_policy"


def _m(name, invariant, file, old, new, *cases, dies_by=()) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=file, old=old, new=new, cases=cases,
                  dies_by=tuple(dies_by))


MODE = "    mode = min(TraceMode(key_mode or TraceMode.off), TraceMode(org_mode), key=ORDER.index)"

MUTANTS: tuple[Mutant, ...] = (
    # --- (a) the policy: the key's opt-in under the org's consent ----------------------
    _m("consent_key_ignored", "the key's own opt-in bounds the mode", C, MODE,
       "    mode = TraceMode(org_mode)", LOWER, OPT_IN),
    _m("consent_org_ignored", "the org's consent bounds the mode", C, MODE,
       "    mode = TraceMode(key_mode or TraceMode.off)", LOWER),
    _m("consent_max_not_min", "the lower of the two, never the higher", C, MODE,
       MODE.replace("min(", "max("), LOWER),
    _m("consent_null_key_opts_out", "a key that never opted in is off (opt-in, D3)", C,
       "TraceMode(key_mode or TraceMode.off)", "TraceMode(key_mode or org_mode)", OPT_IN),
    _m("consent_missing_row_is_consent", "no consent row is off", C,
       "    if row is None or row[1] is None:\n", "    if row is None:\n", OPT_IN,
       dies_by=("ValidationError", "ValueError")),
    _m("consent_not_in_force_kept", "a revoked or future consent is off", C,
       "    if mode is TraceMode.off or not snapshot.is_current(now):",
       "    if mode is TraceMode.off:", IN_FORCE),
    _m("consent_evaluation_below_full", "evaluation consent only at full", C,
       "evaluation_consent=bool(evaluation) and mode is TraceMode.full",
       "evaluation_consent=bool(evaluation)", HEAD),
    _m("consent_version_lost", "the job pins the consent head's version", C,
       "consent_version=version, trace_mode=mode", "consent_version=0, trace_mode=mode", HEAD),
    # --- the cache ----------------------------------------------------------------------
    _m("consent_never_cached", "one read per key per TTL, not per request", C,
       "        if hit is None or hit[0] <= self.clock():", "        if True:", TTL),
    _m("consent_cached_per_org", "the cache is per key: one key's opt-in never answers "
       "for another", C, "        cached = (auth.org_id, auth.key_id)\n",
       "        cached = (auth.org_id, auth.org_id)\n", TTL),
    _m("consent_never_expires", "a revocation lands within the TTL", C,
       "            hit = (self.clock() + self.ttl_s, answer)",
       "            hit = (float(\"inf\"), answer)", TTL),
    _m("consent_cache_unbounded", "the cache is bounded", C,
       "            while len(self.cache) > self.max_entries:", "            while False:",
       BOUNDED),
    _m("consent_failure_raises", "a failed read is off, never an error in the request", C,
       "            except Exception:                    # noqa: BLE001 - fail closed, never raise",
       "            except ZeroDivisionError:            # noqa: BLE001 - fail closed, never raise",
       FAILS, RUNTIME, dies_by=("OSError",)),
    _m("sql_binds_in_the_wrong_order", "the key id, then its org", C,
       "        rows = await self.rows(self.connect, CONSENT_SQL, (key_id, org_id))",
       "        rows = await self.rows(self.connect, CONSENT_SQL, (org_id, key_id))", SQL),
    _m("sql_head_skips_revoked_text", "a revoked head is off, never an older consent", C,
       "                   from infrx.consent_history h where h.org_id = k.org_id\n",
       "                   from infrx.consent_history h where h.org_id = k.org_id\n"
       "                     and h.revoked_at is null\n", SQL),
    # --- the ingress seam ---------------------------------------------------------------
    _m("ingress_ignores_the_capture_policy", "a composed capture's policy is admitted", I,
       "            if self.deps.capture is not None:        # WR-C6-CAPTURE (a): consent, not off",
       "            if False:                                # WR-C6-CAPTURE (a): consent, not off",
       SEAM),
    _m("ingress_applies_an_absent_capture", "no capture composed: the launched ingress", I,
       "            if self.deps.capture is not None:        # WR-C6-CAPTURE (a): consent, not off",
       "            if True:                                 # WR-C6-CAPTURE (a): consent, not off",
       WITHOUT),
)

#: Killed in process against real PostgreSQL (`kill_in_process`): cases are `check_*` names.
PG_MUTANTS: tuple[Mutant, ...] = (
    _m("pg_binds_in_the_wrong_order", "the key id, then its org", C,
       "        rows = await self.rows(self.connect, CONSENT_SQL, (key_id, org_id))",
       "        rows = await self.rows(self.connect, CONSENT_SQL, (org_id, key_id))", PG_OPT_IN),
    _m("pg_key_of_any_org", "a key answers only under its own organization", C,
       'where k.id = %s and k.org_id = %s"""', 'where k.id = %s and %s::uuid is not null"""',
       PG_OPT_IN),
    _m("pg_head_skips_revoked", "a revoked head is off, never an older consent", C,
       "                   from infrx.consent_history h where h.org_id = k.org_id\n",
       "                   from infrx.consent_history h where h.org_id = k.org_id\n"
       "                     and h.revoked_at is null\n", PG_REVOKED),
    _m("pg_oldest_head", "the head is the newest version", C,
       "order by h.consent_version desc limit 1", "order by h.consent_version asc limit 1",
       PG_OPT_IN),
    _m("pg_key_opt_in_ignored", "the key's opt-in column is read", C,
       "select k.trace_mode, c.consent_version", "select 'full', c.consent_version", PG_OPT_IN),
)

RUNNER = Runner(name="t-capture", targets=(SUITE,))


def case_names() -> set[str]:
    tree = ast.parse((API_DIR / SUITE).read_text())
    return {node.name for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")}


def pg_checks() -> dict:
    from tests.t.capture import test_capture_pg as pg
    return pg.CHECKS


def kill_in_process(mutant: Mutant) -> Result:
    """The named `check_*` against a mutated copy of `gateway/capture.py` on a fresh world."""
    from tests.g.ops import pgworld
    path = API_DIR / "infrx" / mutant.file
    source = path.read_text()
    if source.count(mutant.old) != 1:
        return Result(Outcome.misdeclared, f"anchor appears {source.count(mutant.old)} times")
    mutated = types.ModuleType(f"infrx_mutant_{mutant.name}")
    mutated.__package__ = "infrx.gateway"
    exec(compile(source.replace(mutant.old, mutant.new), str(path), "exec"), mutated.__dict__)
    checks = pg_checks()
    for case in mutant.cases:
        w = pgworld.world("capture")
        try:
            checks[case](mutated, w)
        except AssertionError as noticed:
            return Result(Outcome.killed, f"{case}: {str(noticed)[:200]}")
        except Exception as crashed:              # noqa: BLE001 - reported, never a kill
            return Result(Outcome.broken_runner, f"{case}: {type(crashed).__name__}: {crashed}")
        finally:
            w.owner.close()
    return Result(Outcome.survived, f"{', '.join(mutant.cases)} passed")


def run_mutant(mutant: Mutant) -> Result:
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    sys.exit(shared.main(MUTANTS, RUNNER, "run WR-C6-CAPTURE's mutation list"))
