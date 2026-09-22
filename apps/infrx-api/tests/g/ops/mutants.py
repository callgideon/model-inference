#!/usr/bin/env python3
"""R32/R40 for G6B: one single-edit defect per invariant `tests/g/ops` claims.

Vocabulary (`Mutant`, `Outcome`, `Result`, `_failing_ids`) is the contracts list's.
The runner is this file's because two of the mutated files sit outside `infrx/`
(`client_example.py`, which imports `models/marlin2b/bench.py`), so the throwaway copy
keeps the repository layout: `<tmp>/apps/infrx-api/{infrx,tests,client_example.py}` and
`<tmp>/models` linked read-only to the real tree. A kill needs pytest exit 1, failures
only among the named cases, and **every** named case failing (Q1's r2 rule).

    uv run --frozen pytest -q tests/g/ops/test_mutants.py                  # subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/g/ops/test_mutants.py
    uv run --frozen python tests/g/ops/mutants.py --list
"""
from __future__ import annotations

import argparse
import ast
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

API_DIR = pathlib.Path(__file__).resolve().parents[3]
REPO = API_DIR.parents[1]
SUITE = "tests/g/ops"

if str(API_DIR) not in sys.path:        # `python tests/g/ops/mutants.py`
    sys.path.insert(0, str(API_DIR))

from tests.contracts.mutants import Mutant, Outcome, Result, _failing_ids  # noqa: E402

S = "infrx/operations/service.py"
C = "infrx/operations/cli.py"
X = "client_example.py"


def _m(name, invariant, file, old, new, *cases) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=file, old=old, new=new, cases=cases)


MUTANTS: tuple[Mutant, ...] = (
    # --- G6B.a: who may operate, and on whose behalf ---------------------------
    _m("operator_audience_unchecked", "only an operator-audience key row opens an operator session",
       S, "        if auth.audience is not CredentialAudience.operator:", "        if False:",
       "test_api_auth__a_forged_operator_is_refused"),
    _m("tenant_audience_unchecked", "only a consumer key reads a tenant's own state",
       S, "        if auth.audience is not CredentialAudience.consumer:", "        if False:",
       "test_api_auth__a_forged_operator_is_refused"),
    _m("issued_key_is_operator", "this tool mints consumer keys only (no privilege escalation)",
       S, "                         audience=CredentialAudience.consumer, key_hash=hash_key(secret),",
       "                         audience=CredentialAudience.operator, key_hash=hash_key(secret),",
       "test_api_auth__a_forged_operator_is_refused"),
    _m("revoked_key_accepted", "a revoked key authenticates nothing",
       S, "        if row is None or row.revoked_at is not None:", "        if row is None:",
       "test_api_auth__a_revoked_key_reads_nothing",
       "test_api_auth__a_revoked_operator_key_is_refused",
       "test_api_auth__rotation_issues_before_it_revokes"),
    _m("rotation_skips_the_revoke", "rotation revokes the old key after issuing the new one",
       S, "        await self.revoke_key(org_id, key_id, idempotency_key=idempotency_key + \":revoke\",",
       "        0 and await self.revoke_key(org_id, key_id, idempotency_key=idempotency_key + \":revoke\",",
       "test_api_auth__rotation_issues_before_it_revokes"),
    _m("rotation_ignores_the_personal_org", "a rotated key stays in the individual's personal org",
       S, "        if identity.personal_org_id != org_id:", "        if False:",
       "test_api_auth__a_foreign_tenant_key_is_not_found_through_another_org"),
    # --- the secret ------------------------------------------------------------
    _m("secret_stored_in_clear", "the key row holds sha256(secret), never the secret",
       S, "                         audience=CredentialAudience.consumer, key_hash=hash_key(secret),",
       "                         audience=CredentialAudience.consumer, key_hash=secret,",
       "test_api_ops__the_secret_is_stored_only_as_its_hash_and_never_audited"),
    _m("lookup_by_clear_secret", "authentication looks the row up by the hash",
       S, "        row = await self.tenants.key_by_hash(hash_key(secret))",
       "        row = await self.tenants.key_by_hash(secret)",
       "test_api_ops__a_verified_individual_is_granted_keyed_and_reads_its_balance"),
    _m("secret_in_repr", "an IssuedKey's repr carries no secret",
       S, "    secret: str | None = dataclasses.field(default=None, repr=False)",
       "    secret: str | None = dataclasses.field(default=None, repr=True)",
       "test_api_ops__the_secret_is_stored_only_as_its_hash_and_never_audited"),
    _m("secret_revealed_on_replay", "a secret is revealed once, never on a replay",
       S, "        reveal = secret if result[\"inserted\"] and not replayed else None",
       "        reveal = secret",
       "test_api_ops__the_secret_is_revealed_once"),
    # --- idempotency and audit -------------------------------------------------
    _m("replay_ignores_the_request", "one idempotency key names one request",
       S, "            if prior.after.get(\"operation\") != operation or prior.after.get(\"request\") != request:",
       "            if prior.after.get(\"operation\") != operation:",
       "test_api_ops__a_replayed_adjustment_is_deduplicated"),
    _m("replay_is_not_looked_up", "a replay returns the recorded result",
       S, "        if prior is not None:", "        if False:",
       "test_api_ops__a_replayed_adjustment_is_deduplicated",
       "test_api_ops__the_secret_is_revealed_once",
       "test_credit_identity__a_replayed_grant_is_deduplicated"),
    _m("operation_id_not_deterministic", "the port dedupes a crash replay by operation id",
       S, "        operation_id = stable_id(operation, idempotency_key)",
       "        operation_id = stable_id(operation, idempotency_key, str(uuid.uuid4()))",
       "test_api_ops__a_replayed_adjustment_is_deduplicated",
       "test_api_ops__the_secret_is_revealed_once"),
    _m("reason_optional", "every operator write states a reason",
       S, "        if not reason.strip() or len(reason) > MAX_REASON:",
       "        if len(reason) > MAX_REASON:",
       "test_api_ops__every_write_needs_a_reason_and_an_idempotency_key"),
    _m("idempotency_key_unbounded", "the idempotency key is 1..255 characters",
       S, "        if not idempotency_key or len(idempotency_key) > MAX_IDEMPOTENCY_KEY:",
       "        if not idempotency_key:",
       "test_api_ops__every_write_needs_a_reason_and_an_idempotency_key"),
    # --- identity, wallet and suspension ---------------------------------------
    _m("unverified_identity_accepted",
       "only a verified individual is provisioned (declared: the defect surfaces as an "
       "AttributeError on the missing identity)",
       S, "        if identity is None:", "        if False:",
       "test_credit_identity__an_unverified_user_gets_no_grant_and_no_key"),
    _m("key_without_wallet", "no key is issued without a bound, metered wallet",
       S, "        await self.ops.bound_wallet(identity)          # no key without a metered wallet",
       "        pass",
       "test_credit_identity__no_key_without_a_metered_wallet",
       "test_credit_identity__a_wallet_bound_to_another_org_is_refused"),
    _m("wallet_binding_unchecked", "a wallet bound to another org is refused, not used",
       S, "        return v2ports.resolve_wallet(probe, wallet)", "        return wallet",
       "test_credit_identity__a_wallet_bound_to_another_org_is_refused"),
    _m("grant_skips_an_existing_binding", "a grant never lands in a wallet bound elsewhere",
       S, "        if await self.ops.wallets.consumer_wallet_for_user(user_id) is not None:",
       "        if False:",
       "test_credit_identity__a_wallet_bound_to_another_org_is_refused"),
    _m("zero_adjustment_accepted", "an adjustment moves a nonzero amount",
       S, "        if value.is_zero:", "        if False:",
       "test_api_ops__an_adjustment_moves_only_the_individuals_wallet"),
    _m("malformed_amount_untyped",
       "a malformed amount is a typed invalid_request (declared: the defect surfaces as the "
       "parser's own exception instead)",
       S, "        except (ValueError, ArithmeticError, TypeError):", "        except ArithmeticError:",
       "test_api_ops__an_adjustment_moves_only_the_individuals_wallet"),
    _m("direct_balance_edit", "operations never edit a balance directly",
       S, "        wallet = await self.ops.bound_wallet(identity)\n\n        async def write(operation_id):\n            entry,",
       "        wallet = (await self.ops.bound_wallet(identity)).model_copy(update={\"ledger_total\": value})\n\n        async def write(operation_id):\n            entry,",
       "test_api_ops__operations_never_touch_a_balance"),
    _m("suspension_ignored", "a suspended org receives no new key",
       S, "        if await self.ops.tenants.suspension(org_id) is not None:", "        if False:",
       "test_api_ops__suspension_refuses_new_keys_and_keeps_prose_in_the_audit"),
    _m("suspension_code_free_text", "the org carries a closed reason code, not prose",
       S, "        if reason_code is not None and reason_code not in SUSPENSION_REASONS:",
       "        if False:",
       "test_api_ops__suspension_refuses_new_keys_and_keeps_prose_in_the_audit"),
    # --- G6B.b: publication, a tenant's own reads, cancellation, reconciliation -
    _m("private_deployment_published", "a private or inactive deployment never reaches the public alias",
       S, "        if (deployment.visibility is not Visibility.public\n                or deployment.state is not DeploymentState.active):",
       "        if False:",
       "test_api_auth__a_private_deployment_is_never_published_and_is_not_found"),
    _m("mispriced_card_published", "a card that prices another revision is refused (R69)",
       S, "        if (card.deployment_revision_id != deployment.deployment_revision_id",
       "        if (False",
       "test_credit_rate__an_unpriced_or_mispriced_deployment_is_unserveable"),
    _m("draining_deployment_published", "a public but not-active deployment is not published",
       S, "                or deployment.state is not DeploymentState.active):",
       "                or False):",
       "test_api_auth__a_private_deployment_is_never_published_and_is_not_found"),
    _m("card_serving_unchecked", "a card for another serving revision is refused",
       S, "                or card.serving_version_id != serving.serving_version_id\n",
       "                or False\n",
       "test_credit_rate__an_unpriced_or_mispriced_deployment_is_unserveable"),
    _m("card_model_unchecked", "a card for another model is refused",
       S, "                or card.model_id != serving.model_id):", "                or False):",
       "test_credit_rate__an_unpriced_or_mispriced_deployment_is_unserveable"),
    _m("equal_effective_card_accepted", "a replacement card must be strictly newer",
       S, "                and card.effective_at <= active.effective_at):",
       "                and card.effective_at < active.effective_at):",
       "test_credit_rate__a_stale_card_is_refused"),
    _m("card_not_written", "publication writes the card future admissions resolve",
       S, "for r in (serving, deployment, card)]", "for r in (serving, deployment)]",
       "test_credit_rate__an_admitted_request_keeps_its_admitted_rate",
       "test_api_ops__the_pinned_marlin_release_is_published_and_quoted"),
    _m("alias_not_moved", "publication moves the public alias to the new deployment",
       S, "            await self.ops.registry.move_alias(requested_model, deployment.deployment_revision_id)",
       "            pass",
       "test_api_ops__the_pinned_marlin_release_is_published_and_quoted"),
    _m("provisional_card_unlabelled", "a P-01 provisional card says so in its version",
       S, "    provisional = \"P-01\" in approved_by", "    provisional = False",
       "test_api_ops__the_pinned_marlin_release_is_published_and_quoted",
       "test_api_ops__the_cli_publishes_the_provisional_marlin_release"),
    _m("usage_not_keyed_by_org", "a tenant's usage is read by its authenticated org",
       S, "        return await self.ops.accounts.usage(self.auth.org_id)",
       "        return await self.ops.accounts.usage(self.auth.key_id)",
       "test_api_ops__a_tenant_reads_only_its_own_usage_holds_and_jobs"),
    _m("holds_not_keyed_by_org", "a tenant's holds are read by its authenticated org",
       S, "        return await self.ops.accounts.holds(self.auth.org_id)",
       "        return await self.ops.accounts.holds(self.auth.key_id)",
       "test_api_ops__a_tenant_reads_only_its_own_usage_holds_and_jobs"),
    _m("job_not_keyed_by_org", "a tenant's job is read by its authenticated org",
       S, "        return await self.ops.jobs.get_owned(self.auth.org_id, job_handle)",
       "        return await self.ops.jobs.get_owned(self.auth.key_id, job_handle)",
       "test_api_ops__a_tenant_reads_only_its_own_usage_holds_and_jobs"),
    _m("cancel_audited_under_the_wrong_action", "a cancellation is audited in the D1 vocabulary",
       S, "\"job_cancel\": \"admin_set_entitlements\",", "\"job_cancel\": \"calibration_label\",",
       "test_api_ops__operator_cancellation_is_tenant_scoped_and_audited"),
    _m("reconcile_at_a_future_clock", "reconciliation is decided at the present time",
       S, "operation_id,\n                                                    self.principal, self.ops.clock())",
       "operation_id,\n                                                    self.principal, self.ops.clock().replace(year=9999))",
       "test_api_ops__reconciliation_waits_for_its_interval_and_is_audited"),
    _m("cli_accepts_a_naive_time",
       "publication times carry an explicit offset (declared: the naive time surfaces as a "
       "record validation error instead of the refusal)",
       C, "        if created.utcoffset() is None or effective.utcoffset() is None:", "        if False:",
       "test_api_ops__the_cli_publishes_the_provisional_marlin_release"),
    # --- G6B.c: the headless client -------------------------------------------
    _m("resume_resends_finished_items", "a resume skips items whose outcome is terminal",
       X, "    todo = [i for i in items if prior.get(i[\"item_key\"], {}).get(\"status\") not in TERMINAL]",
       "    todo = list(items)",
       "test_api_ops__a_resumed_sweep_resends_the_same_key_and_payload_and_skips_done_items"),
    _m("idempotency_key_per_run", "the Idempotency-Key is a function of the item, not the run",
       X, "                          \"idempotency_key\": bench.IDEMPOTENCY_PREFIX + key})",
       "                          \"idempotency_key\": bench.IDEMPOTENCY_PREFIX + key + str(id(it))})",
       "test_api_ops__a_resumed_sweep_resends_the_same_key_and_payload_and_skips_done_items"),
    _m("retry_after_ignored", "a 429 waits the server's Retry-After",
       X, "            await SLEEP(min(wait if wait is not None else 2 ** attempt, MAX_RETRY_AFTER_S))",
       "            await SLEEP(min(2 ** attempt, MAX_RETRY_AFTER_S))",
       "test_api_ops__failures_are_explicit_and_never_retried_blindly"),
    _m("client_errors_retried", "a 400/409 is quarantined, never retried unchanged",
       X, "RETRYABLE = frozenset({429, 500, 502, 503, 504})",
       "RETRYABLE = frozenset({400, 409, 429, 500, 502, 503, 504})",
       "test_api_ops__failures_are_explicit_and_never_retried_blindly"),
    _m("expired_key_quarantined", "a 410 asks for a re-derived re-run",
       X, "        if resp.status_code == 410:", "        if False:",
       "test_api_ops__failures_are_explicit_and_never_retried_blindly"),
    _m("usage_less_200_accepted", "a 200 without usage is a failure, not a result",
       X, "        row[\"status\"] = \"failed\"          # a 200 without usage or content is not a result",
       "        row[\"status\"] = \"done\"",
       "test_api_ops__failures_are_explicit_and_never_retried_blindly"),
    _m("wallet_exhaustion_ignored", "a 402 pauses the sweep instead of burning the dataset",
       X, "            cfg[\"stop\"].set()             # do not burn the dataset against 401/402/403",
       "            pass",
       "test_api_ops__an_exhausted_wallet_pauses_the_sweep"),
    _m("credential_failure_continues", "a 401/403 stops the sweep like a 402",
       X, "STOP = {401: \"stopped_credential\", 403: \"stopped_credential\", 402: \"paused_wallet\"}",
       "STOP = {402: \"paused_wallet\"}",
       "test_api_ops__an_exhausted_wallet_pauses_the_sweep"),
    _m("concurrency_bound_loose", "no more than --concurrency requests are in flight",
       X, "    gate = asyncio.Semaphore(cfg[\"concurrency\"])",
       "    gate = asyncio.Semaphore(cfg[\"concurrency\"] + 1)",
       "test_api_ops__concurrency_is_bounded_per_key"),
    _m("per_key_cap_unchecked", "--concurrency never exceeds the per-key cap of 8",
       X, "    if not 1 <= a.concurrency <= MAX_PER_KEY:", "    if not 1 <= a.concurrency:",
       "test_api_ops__concurrency_is_bounded_per_key"),
    _m("async_handle_not_persisted", "a 202's handle is written before polling",
       X, "            record(dict(row))             # persist the handle before polling (§3.5 step 2)",
       "            pass",
       "test_api_ops__an_async_item_is_persisted_then_polled_to_its_result"),
    _m("ordinary_chat_asks_for_202", "ordinary chat never asks for async",
       X, "    if cfg[\"respond_async\"]:", "    if True:",
       "test_api_ops__the_quickstart_runs_on_the_sync_path"),
    _m("key_in_args_unchecked", "an argument embedding the key is refused",
       X, "    bench.refuse_key_in_args(a, key)", "    pass",
       "test_api_auth__the_client_key_never_reaches_argv_or_state"),
    _m("inference_id_not_allowlisted", "a server header is recorded only through the allowlist",
       X, "        row[\"inference_id\"] = bench.allow(resp.headers.get(\"inference-id\"), bench.ID_OK,",
       "        row[\"inference_id\"] = resp.headers.get(\"inference-id\") or bench.allow(None, bench.ID_OK,",
       "test_api_auth__the_client_key_never_reaches_argv_or_state"),
    _m("error_code_not_allowlisted", "an error code is recorded only through the allowlist",
       X, "    return bench.allow(err.get(\"code\") if isinstance(err, dict) else None, bench.CODE_OK, key)",
       "    return err.get(\"code\") if isinstance(err, dict) else None",
       "test_api_auth__the_client_key_never_reaches_argv_or_state"),
    # --- the CLI ---------------------------------------------------------------
    _m("cli_accepts_a_key_on_argv", "a key on argv is refused",
       C, "        if token.startswith(\"sk-\") or service.KEY_PREFIX in token:", "        if False:",
       "test_api_ops__the_cli_refuses_a_key_on_argv_and_an_existing_secret_file"),
    _m("cli_overwrites_a_secret_file", "an existing secret file is never replaced",
       C, "        if os.path.exists(a.secret_file):     # refuse before a key exists, not after",
       "        if False:",
       "test_api_ops__the_cli_refuses_a_key_on_argv_and_an_existing_secret_file"),
    _m("cli_secret_file_world_readable", "the secret file is created 0600",
       C, "os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)", "os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)",
       "test_api_ops__the_cli_writes_the_secret_once_and_never_prints_it"),
    _m("cli_prints_the_secret", "stdout carries the result without the secret",
       C, "            \"replayed\": issued.replayed,", "            \"replayed\": issued.secret,",
       "test_api_ops__the_cli_writes_the_secret_once_and_never_prints_it"),
    _m("cli_error_echoes_the_credential", "a refusal names the error, never the credential",
       C, "        print(json.dumps({\"error\": e.code, \"message\": str(e)}), file=sys.stderr)",
       "        print(json.dumps({\"error\": e.code, \"message\": str(e) + secret}), file=sys.stderr)",
       "test_api_auth__the_cli_reports_a_refusal_without_the_secret"),
)


def run_mutant(mutant: Mutant) -> Result:
    if not mutant.cases:
        return Result(Outcome.misdeclared, "declares no case")
    with tempfile.TemporaryDirectory(prefix=f"g6b-mutant-{mutant.name}-") as tmp:
        api = pathlib.Path(tmp) / "apps" / "infrx-api"
        junk = shutil.ignore_patterns("__pycache__", ".venv")
        for name in ("infrx", "tests"):
            shutil.copytree(API_DIR / name, api / name, ignore=junk)
        if (API_DIR / X).exists():
            shutil.copy2(API_DIR / X, api / X)
        os.symlink(REPO / "models", pathlib.Path(tmp) / "models")
        target = api / mutant.file
        source = target.read_text()
        found = source.count(mutant.old)
        if found != 1:
            return Result(Outcome.misdeclared,
                          f"anchor appears {found} times in {mutant.file}: {mutant.old[:60]!r}")
        target.write_text(source.replace(mutant.old, mutant.new, 1))
        done = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider",
             "-rf", "--tb=no", "-o", "addopts=--import-mode=importlib",
             "-o", "testpaths=tests", SUITE, "-k", " or ".join(mutant.cases)],
            cwd=api, capture_output=True, text=True, timeout=300,
            env={"PYTHONPATH": str(api), "PATH": "/usr/bin:/bin"})
        stdout = done.stdout or ""
        lines = (stdout or done.stderr).strip().splitlines()
        summary = lines[-1] if lines else "no output"
        if done.returncode not in (0, 1):
            return Result(Outcome.broken_runner, f"pytest exit {done.returncode}: {summary}")
        if "no tests ran" in summary or not any(w in summary for w in ("passed", "failed")):
            return Result(Outcome.misdeclared, f"no case matched: {summary}")
        failed, errored = _failing_ids(stdout)
        if errored:
            return Result(Outcome.broken_runner, f"errors: {errored[:3]}")
        if done.returncode == 0 or not failed:
            return Result(Outcome.survived, summary)
        stray = [t for t in failed if not any(case in t for case in mutant.cases)]
        if stray:
            return Result(Outcome.broken_runner, f"failures outside the named cases: {stray[:3]}")
        unproven = [c for c in mutant.cases if not any(c in t for t in failed)]
        if unproven:
            return Result(Outcome.misdeclared, f"named cases that did not notice: {unproven}")
        return Result(Outcome.killed, summary)


def case_names() -> set[str]:
    """Every `test_*` function in this suite except the list's own claims."""
    names = set()
    for path in sorted((API_DIR / SUITE).glob("test_*.py")):
        if path.name == "test_mutants.py":
            continue
        tree = ast.parse(path.read_text())
        names |= {n.name for n in tree.body
                  if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")}
    return names


def main() -> int:
    parser = argparse.ArgumentParser(description="run G6B's mutation list")
    parser.add_argument("names", nargs="*")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    if args.list:
        for mutant in MUTANTS:
            print(f"{mutant.name:36s} {mutant.invariant}")
        print(f"\n{len(MUTANTS)} mutants over "
              f"{len({c for m in MUTANTS for c in m.cases})} named cases")
        return 0
    chosen = [m for m in MUTANTS if not args.names or m.name in args.names]
    bad = []
    for mutant in chosen:
        result = run_mutant(mutant)
        print(f"[{result.outcome:13s}] {mutant.name}: {result.detail}")
        if not result.killed:
            bad.append(mutant.name)
    print(f"\n{len(chosen) - len(bad)}/{len(chosen)} killed" + (f"; not killed: {bad}" if bad else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
