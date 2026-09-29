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
SYNC = "test_a_consented_sync_request_writes_one_record_of_the_request_and_its_answer"
UNCONSENTED = "test_an_unconsented_request_writes_nothing"
MINIMAL = "test_a_minimal_request_writes_metadata_only"
CREDENTIAL = "test_a_credential_never_reaches_the_spool"
MEDIA = "test_inline_media_is_spooled_by_digest_not_bytes"
STREAM = "test_a_streamed_answer_is_captured_as_relayed"
ASYNC = "test_an_async_request_is_left_to_the_worker"
KEEP = "test_a_capture_that_cannot_keep_the_record_never_fails_the_request"
OPEN = "test_a_capture_that_cannot_open_never_fails_the_request"
BROKEN = "test_an_answer_that_breaks_off_is_recorded_as_incomplete"


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
    # --- (b) the request-path hook ------------------------------------------------------
    _m("hook_never_wraps", "a consented answer is captured", I,
       "        if deps.capture is not None:            # WR-C6-CAPTURE (b): the answer's record",
       "        if False:                               # WR-C6-CAPTURE (b): the answer's record",
       SYNC, STREAM, CREDENTIAL),
    _m("hook_spools_async", "an async request's record is the worker's", C,
       "        if request.trace_policy.trace_mode is TraceMode.off \\\n"
       "                or request.execution_mode is ExecutionMode.async_:",
       "        if request.trace_policy.trace_mode is TraceMode.off:", ASYNC),
    _m("hook_mode_not_the_policy", "the capture's mode is the admitted policy's", C,
       "                                     request.trace_policy.trace_mode, request.deadline_at)",
       "                                     TraceMode.full, request.deadline_at)", MINIMAL),
    _m("hook_ignores_off", "an off policy wraps nothing", C,
       "        if request.trace_policy.trace_mode is TraceMode.off \\\n"
       "                or request.execution_mode is ExecutionMode.async_:",
       "        if request.execution_mode is ExecutionMode.async_:", UNCONSENTED),
    _m("hook_no_request_half", "the record carries what was asked", C,
       "            capture.add(request_line(request, self.token))\n",
       "            pass\n", SYNC, CREDENTIAL),
    _m("hook_no_answer_half", "the record carries the answer as sent", C,
       "                capture.add(scrub(message[\"body\"], self.token))",
       "                pass", SYNC, STREAM),
    _m("hook_first_frame_only", "every frame of a stream, not the first", C,
       "            if message[\"type\"] == \"http.response.body\" and message.get(\"body\"):",
       "            if message[\"type\"] == \"http.response.body\" and message.get(\"body\") "
       "and not capture.content_bytes > len(request_line(request, self.token)):", STREAM),
    _m("hook_never_finished", "the record is finished however the answer ends", C,
       "                await capture.finish(envelope(request, capture, self.limits, finished))",
       "                pass", SYNC, MINIMAL),
    _m("hook_finish_raises", "a failed finish is never the request's error", C,
       "            except Exception:                    # noqa: BLE001 - never the request's error\n"
       "                log.warning(\"trace capture of %s failed\", request.request_id, exc_info=True)",
       "            except ZeroDivisionError:            # noqa: BLE001 - never the request's error\n"
       "                log.warning(\"trace capture of %s failed\", request.request_id, exc_info=True)",
       KEEP, dies_by=("RuntimeError",)),
    _m("hook_open_raises", "a failed open is never the request's error", C,
       "        except Exception:                        # noqa: BLE001 - never the request's error\n"
       "            log.warning(\"trace capture of %s failed\", request.request_id, exc_info=True)\n"
       "            return await self.inner(scope, receive, send)",
       "        except ZeroDivisionError:                # noqa: BLE001 - never the request's error\n"
       "            log.warning(\"trace capture of %s failed\", request.request_id, exc_info=True)\n"
       "            return await self.inner(scope, receive, send)", OPEN, dies_by=("OSError",)),
    _m("hook_credential_in_the_request", "the bearer token never reaches the spool", C,
       "    return scrub(json.dumps(document, sort_keys=True, separators=(\",\", \":\"),\n"
       "                            default=str).encode(), token) + b\"\\n\"",
       "    return json.dumps(document, sort_keys=True, separators=(\",\", \":\"),\n"
       "                      default=str).encode() + b\"\\n\"", CREDENTIAL),
    _m("hook_credential_in_the_answer", "the bearer token never reaches the spool", C,
       "    return data.replace(token, REDACTED) if token else data",
       "    return data", CREDENTIAL),
    _m("hook_token_never_read", "the caller's token is known to the scrub", C,
       "        token = headers.get(\"authorization\", \"\").removeprefix(\"Bearer \").strip().encode()",
       "        token = b\"\"", CREDENTIAL),
    _m("hook_media_inline", "inline media is spooled by digest", C,
       "    if isinstance(value, str) and value.startswith(\"data:\"):",
       "    if False:", MEDIA),
    _m("hook_media_top_level_only", "media inside nested parts is found", C,
       "    if isinstance(value, (list, tuple)):\n        return [redacted(item) for item in value]",
       "    if isinstance(value, (list, tuple)):\n        return list(value)", MEDIA),
    _m("hook_complete_when_lost", "content is complete only if the answer finished", C,
       "        content_complete=bool(held) and finished,", "        content_complete=bool(held),",
       BROKEN),
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
