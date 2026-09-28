#!/usr/bin/env python3
"""R32/R40/R83 for P3: one single-edit defect per decision `test_training.py` claims, through
the shared runner with `require_every_case`. The real-service rerun
(`test_training_services.py`) is outside the runner (T2I/G8's pattern).

    uv run --frozen pytest -q tests/p/training/test_mutants.py                     # subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/p/training/test_mutants.py   # all
"""
from __future__ import annotations

import ast
import pathlib
import sys

API_DIR = pathlib.Path(__file__).resolve().parents[3]
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from tests.contracts import mutants as shared  # noqa: E402
from tests.contracts.mutants import Mutant, Outcome, Result, Runner  # noqa: E402,F401

SUITE = "tests/p/training/test_training.py"
P = "pipelines/training/__init__.py"

BUNDLE = "test_p3_a_bundle_names_train_and_dev_and_pins_the_holdout_without_shipping_it"
REFUSE = "test_p3_a_bundle_refuses_bad_config_other_exports_and_untrainable_data"
PIN = "test_p3_a_bundle_pins_only_a_stored_current_export_of_trainable_samples"
MANUAL = "test_p3_the_manual_workflow_round_trips_to_an_eligible_candidate"
HIDDEN = "test_p3_automatic_connectors_stay_hidden_until_p11"
AMBIG = "test_p3_an_ambiguous_submit_is_reconciled_without_a_second_paid_job"
CRASH = "test_p3_a_crash_mid_submit_never_resubmits"
REJECT = "test_p3_a_refused_submit_fails_and_releases_its_budget"
CONSENT = "test_p3_revoked_consent_stops_a_prepared_submission"
COST = "test_p3_reported_cost_is_settled_exactly_or_unknown"
DUP = "test_p3_a_redelivered_checkpoint_is_one_receipt_and_one_evaluation"
INVALID = "test_p3_invalid_artifacts_are_rejected_and_never_evaluated"
LATE = "test_p3_a_late_checkpoint_after_cancel_is_rejected"
HOLDOUT = "test_p3_only_the_frozen_holdout_evaluation_makes_a_candidate_eligible"
LOST = "test_p3_a_checkpoint_outcome_lost_in_a_crash_keeps_the_receipts_state"

RESULT = ('result.get("state"), result.get("dataset_ref"), result.get("split"),\n'
          '            result.get("holdout_sha256")) != ("succeeded", bundle["dataset_ref"], '
          '"holdout",')
DESCR = ('            descriptor.get("base_model"), descriptor.get("adaptation")) != (\n'
         '            config["base_model"], config["adaptation"]):')


def m(name, invariant, old, new, *cases, dies_by=(), occurrences=1):
    return Mutant(name=name, invariant=invariant, file=P, old=old, new=new, cases=cases,
                  dies_by=dies_by, occurrences=occurrences)


MUTANTS: tuple[Mutant, ...] = (
    # --- P3.a the bundle (PIPELINE-LINEAGE, DATA-SPLIT, DATA-RIGHTS)
    m("p3_lora_is_an_objective", "LoRA is an adaptation, not an objective",
      'objective: Literal["sft", "preference"]', 'objective: Literal["sft", "preference", "lora"]',
      REFUSE),
    m("p3_config_open", "a config names only known keys", 'extra="forbid", strict=True)',
      'extra="ignore", strict=True)', REFUSE),
    m("p3_export_of_any_dataset", "the bundled export is of this dataset",
      'if (record["format"], record["dataset_ref"]) != (export["format"], dataset_ref):',
      "if False:", REFUSE),
    m("p3_export_kind_open", "the export is N2's or P1's (0-P3-EXPORT-PIN-UNVERIFIED)",
      "if kind is None or not isinstance(export_id, str)", "if not isinstance(export_id, str)",
      PIN),
    m("p3_export_expiry_unchecked", "an expired export is not pinned",
      'now >= datetime.fromisoformat(record["expires_at"])', "False", PIN),
    m("p3_export_cancel_unchecked", "a cancelled export is not pinned",
      'if await objects.head(f"{base}/cancelled") is not None or', "if False or", PIN),
    m("p3_export_bytes_unchecked", "the export's bytes are what it recorded",
      "if hashlib.sha256(data).hexdigest() != digest:", "if False:", PIN),
    m("p3_export_parts_unpinned", "an N2 export's content digest is of its recorded parts",
      'if kind == "exports" and sha256 != hashlib.sha256(', "if False and sha256 != hashlib.sha256(",
      PIN),
    m("p3_export_ids_unchecked", "an export carries only samples under a current training grant",
      "if carried - set(train + dev):", "if False:", PIN),
    m("p3_label_export_ids_unread", "a P1 export's samples are its lineage",
      'ids = {x["sample_id"] for x in record.get("lineage", ())}', "ids = set()", PIN),
    m("p3_dataset_export_ids_unread", "an N2 export's samples are its items",
      'ids |= {json.loads(line)["sample_id"] for line in data.splitlines()}', "ids |= set()", PIN),
    m("p3_pin_from_caller", "the pin is the stored export's digest",
      'return {"format": record["format"], "export_id": export_id, "sha256": sha256}, ids',
      'return {"format": record["format"], "export_id": export_id, '
      '"sha256": export.get("sha256")}, ids', PIN),
    m("p3_bundle_gate_is_access", "the bundle reads the training gate",
      'purpose="training"))\n    train = ', 'purpose="provider_sharing"))\n    train = ', REFUSE),
    m("p3_revoked_train_bundled", "train ids are under a current training grant",
      "train = [i for i in manifest.splits.train if i in allowed]",
      "train = list(manifest.splits.train)", REFUSE),
    m("p3_revoked_dev_bundled", "dev ids are under a current training grant",
      "dev = [i for i in manifest.splits.validation if i in allowed]",
      "dev = list(manifest.splits.validation)", REFUSE),
    m("p3_untrainable_bundled", "a bundle without a trainable sample is refused",
      "    if not train:\n", "    if False:\n", REFUSE),
    m("p3_omissions_unlisted", "left-out samples are listed",
      '"omitted": sorted(set(manifest.splits.train + manifest.splits.validation) - allowed)',
      '"omitted": []', REFUSE),
    m("p3_holdout_shipped", "the holdout never ships", '"train": train, "dev": dev,',
      '"train": train + holdout, "dev": dev,', BUNDLE),
    m("p3_holdout_unpinned", "the bundle pins the frozen holdout's ids",
      '"sha256": hashlib.sha256(lab.canonical(holdout)).hexdigest()', '"sha256": ""', BUNDLE),
    m("p3_holdout_size_dropped", "the pin carries the holdout size", '"size": len(holdout)',
      '"size": 0', BUNDLE),
    m("p3_run_not_training", "the external run is for training",
      '"purpose": "training", "connector": connector',
      '"purpose": "external_judging", "connector": connector', BUNDLE),
    m("p3_bundle_rewritable", "a bundle is write-once",
      "await write_once(objects, _key(provider_org_id, external_run_id), lab.canonical(bundle))",
      "await objects.put_if_absent(_key(provider_org_id, external_run_id), "
      "lab.canonical(bundle), 'application/json')", BUNDLE),
    # --- P3.b submission (TRAIN-RECOVER, PIPELINE-BUDGET)
    m("p3_default_advertises_automatic", "only the manual connector is advertised by default",
      "ADVERTISED = frozenset({MANUAL})", 'ADVERTISED = frozenset({MANUAL, "protocol-test"})',
      HIDDEN),
    m("p3_unadvertised_submits", "an unadvertised connector never submits",
      ' or connector.name not in advertised:', ":", HIDDEN),
    m("p3_any_connector", "a run is sent only through its own connector",
      'if run["connector"] != connector.name or ', "if ", HIDDEN),
    m("p3_submit_role_unchecked", "a submitter is a current developer or above",
      "    await _member(members, provider_org_id, user_id)\n    bundle = await _bundle(objects, "
      "provider_org_id, external_run_id)\n    allowed",
      "    bundle = await _bundle(objects, provider_org_id, external_run_id)\n    allowed",
      MANUAL),
    m("p3_viewer_is_a_member", "the role must hold run_evaluation",
      "membership.permits(ProviderCapability.run_evaluation, now,",
      "membership.permits(ProviderCapability.read_aggregate_health, now,", MANUAL),
    m("p3_consent_unchecked", "a submission re-reads the grants",
      'if set(bundle["train"] + bundle["dev"]) - allowed:', "if False:", CONSENT),
    m("p3_consent_gate_is_access", "the submission gate is training",
      'purpose="training"))\n    if set(bundle', 'purpose="provider_sharing"))\n    if set(bundle',
      CONSENT),
    m("p3_budget_unreserved", "an automatic submission reserves its budget first",
      '        await ledger.reserve(key, provider_org_id=provider_org_id, payer_ref=run["payer_ref"],'
      '\n                             limit=run["limit"])\n', "        pass\n", AMBIG),
    m("p3_manual_reserves", "the manual bundle holds no reservation (1-PIPE-R1)",
      "paid = connector.name != MANUAL", "paid = True", MANUAL),
    m("p3_manual_cancel_settles", "cancelling a manual run settles nothing",
      'if run["connector"] != MANUAL:                 # the manual bundle reserved nothing',
      "if True:", LATE, dies_by=("KeyError",)),
    m("p3_finish_role_unchecked", "only a current developer finishes a manual run",
      "    await _member(members, provider_org_id, user_id)\n    run = await _run(",
      "    run = await _run(", MANUAL),
    m("p3_finish_automatic", "an automatic run is never finished by hand",
      'if run["connector"] != MANUAL:\n        raise errors.StateConflict', "if False:\n        raise errors.StateConflict",
      MANUAL),
    m("p3_finish_replay_conflicts", "finishing a finished manual run is a replay",
      '    if run["state"] == "completed":\n        return run\n', "", MANUAL,
      dies_by=("StateConflict",)),
    m("p3_rejection_ambiguous", "a definite refusal is failed", "except Rejected as refused:",
      "except KeyError as refused:", REJECT),
    m("p3_rejection_keeps_budget", "a refused submission releases its reservation",
      "            await ledger.release(key, provider_org_id=provider_org_id)\n",
      "            pass\n", REJECT),
    m("p3_unknown_is_failed", "an unknown outcome is ambiguous",
      'return await ledger.move(external_run_id, provider_org_id=provider_org_id,\n'
      '                                 expected="submitting", target="ambiguous")',
      'return await ledger.move(external_run_id, provider_org_id=provider_org_id,\n'
      '                                 expected="submitting", target="failed")', AMBIG),
    m("p3_submitted_without_key", "the connector gets the run's submit key",
      "job_id = await connector.submit(bundle, key=key)",
      'job_id = await connector.submit(bundle, key=key + "-retry")', AMBIG),
    m("p3_http_key_dropped", "the protocol carries the idempotency key",
      'headers={"Idempotency-Key": key}', "headers={}", AMBIG),
    m("p3_http_4xx_ambiguous", "a 4xx is a definite refusal",
      "if 400 <= response.status_code < 500:", "if False:", REJECT),
    m("p3_ambiguous_unreconciled", "an ambiguous run is reconciled on resume",
      'if run["state"] in ("submitting", "ambiguous"):', 'if run["state"] in ("submitting",):',
      AMBIG),
    m("p3_stale_submitting_left", "a stale submitting run is reconciled on resume",
      'if run["state"] in ("submitting", "ambiguous"):', 'if run["state"] in ("ambiguous",):',
      CRASH),
    m("p3_stale_submitting_not_ambiguous", "a stale submit becomes ambiguous, never retried",
      'if run["state"] == "submitting":', "if False:", CRASH),
    m("p3_lookup_ignored", "reconciliation looks the key up",
      "job_id = await connector.lookup(lab.submit_key(external_run_id))", "job_id = None", AMBIG),
    m("p3_lookup_other_key", "reconciliation looks up the submit key",
      "job_id = await connector.lookup(lab.submit_key(external_run_id))",
      "job_id = await connector.lookup(external_run_id)", AMBIG),
    m("p3_not_found_is_failed", "a lookup that finds nothing stays ambiguous",
      "    if job_id is None:\n        return run\n",
      "    if job_id is None:\n        return await ledger.move(external_run_id, "
      "provider_org_id=provider_org_id, expected=\"ambiguous\", target=\"failed\")\n", CRASH),
    m("p3_http_404_raises", "a key the server never saw is not found, not an error",
      "        if response.status_code == 404:\n            return None\n", "", CRASH,
      dies_by=("KeyError",)),
    m("p3_poll_settles_running", "a running job settles nothing",
      'if status.get("state") not in ("completed", "failed", "cancelled"):', "if False:", COST,
      dies_by=("StateConflict",)),
    m("p3_cost_estimated", "an unreported cost is unknown, never estimated",
      "    except ValidationError:\n        return None\n",
      '    except ValidationError:\n        return "12.00000000"\n', COST),
    m("p3_poll_unsettled", "a finished job settles its reported cost",
      '    cost = _cost(status.get("cost"))\n    await ledger.settle(lab.submit_key(external_run_id),'
      ' provider_org_id=provider_org_id,\n                        cost=cost)\n',
      '    cost = _cost(status.get("cost"))\n', COST),
    m("p3_cancel_cost_dropped", "a cancelled job keeps its reported cost",
      'cost = _cost((await connector.cancel(run["job_id"])).get("cost"))',
      'cost = _cost(None) if await connector.cancel(run["job_id"]) else None', COST),
    m("p3_cancel_prepared_refused", "a prepared run cancels without its connector",
      '    if run["state"] == "prepared":\n        return await ledger.move(external_run_id, '
      'provider_org_id=provider_org_id,\n                                 expected="prepared", '
      'target="cancelled")\n', "", COST, dies_by=("StateConflict",)),
    m("p3_cancel_unknown_run", "an ambiguous run is reconciled before any cancel",
      '    if run["state"] != "submitted":\n        raise errors.StateConflict(f"a {run',
      '    if False:\n        raise errors.StateConflict(f"a {run', CRASH,
      dies_by=("KeyError",)),
    m("p3_refused_cancel_cancels", "a cancel the provider refused is not a cancel",
      '        response = await self.client.post(f"/jobs/{job_id}/cancel")\n'
      '        response.raise_for_status()\n',
      '        response = await self.client.post(f"/jobs/{job_id}/cancel")\n', COST),
    # --- P3.c checkpoints and eligibility (TRAIN-RECOVER, CHECKPOINT-IDEM via B3)
    m("p3_foreign_artifact_key", "an artifact lives under its run's prefix",
      'if not artifact_key.startswith(f"lab/{provider_org_id}/training/{external_run_id}/"):',
      "if False:", INVALID),
    m("p3_redelivery_reprocessed", "a redelivery is the first outcome",
      "    if prior is not None:", "    if False:", DUP),
    m("p3_late_checkpoint_accepted", "a checkpoint of an inactive run is rejected",
      'if run["state"] not in ACTIVE:', "if False:", LATE),
    m("p3_prepared_is_active", "a never-submitted run takes no checkpoint",
      'ACTIVE = ("submitted", "completed")', 'ACTIVE = ("submitted", "completed", "prepared")',
      LATE),
    m("p3_missing_artifact_crashes", "a missing artifact is rejected, not a crash",
      '    if data is None:\n        return "missing_artifact"\n', "", INVALID,
      dies_by=("TypeError",)),
    m("p3_digest_unchecked", "the bytes match the declared digest",
      'if "sha256:" + hashlib.sha256(data).hexdigest() != digest:', "if False:", INVALID),
    m("p3_base_model_unchecked", "a checkpoint is of the bundle's base model", DESCR,
      '            descriptor.get("adaptation"), 0) != (\n            config["adaptation"], 0):',
      INVALID),
    m("p3_adaptation_unchecked", "a checkpoint is of the bundle's adaptation", DESCR,
      '            descriptor.get("base_model"), 0) != (\n            config["base_model"], 0):',
      INVALID),
    m("p3_format_unchecked", "a descriptor names its format",
      'descriptor.get("format") != DESCRIPTOR or', "False or", INVALID),
    m("p3_non_dict_descriptor", "a descriptor is an object",
      "not isinstance(descriptor, dict) or ", "", INVALID, dies_by=("AttributeError",)),
    m("p3_non_json_crashes", "bytes that are no descriptor are incompatible",
      "    except ValueError:\n", "    except KeyError:\n", INVALID,
      dies_by=("JSONDecodeError",)),
    m("p3_rejected_receipt_validated", "a rejected checkpoint's receipt is rejected",
      '"rejected" if reason else "validated"', '"validated"', INVALID),
    m("p3_lost_rejection_evaluated", "a rejected receipt stays rejected after a crash",
      'reason = "rejected" if receipt["state"] == "rejected" else None', "reason = None", LOST),
    m("p3_eval_on_validation", "the evaluation runs on the holdout",
      'dataset_ref=bundle["dataset_ref"], split="holdout",',
      'dataset_ref=bundle["dataset_ref"], split="validation",', MANUAL),
    m("p3_eval_holdout_unpinned", "the evaluation names the frozen holdout",
      'holdout_sha256=bundle["holdout"]["sha256"])', 'holdout_sha256="")', MANUAL),
    m("p3_approve_role_unchecked", "an approver is a current developer or above",
      "    await _member(members, provider_org_id, user_id)\n    bundle = await _bundle(objects, "
      "provider_org_id, external_run_id)\n    outcome",
      "    bundle = await _bundle(objects, provider_org_id, external_run_id)\n    outcome", MANUAL),
    m("p3_approve_rejected", "only a validated checkpoint is eligible",
      '(outcome.get("state"), outcome.get("external_run_ref")) != (\n'
      '            "validated", bundle["external_run_ref"])',
      '(outcome.get("external_run_ref"),) != (\n            bundle["external_run_ref"],)',
      INVALID),
    m("p3_approve_other_run", "a checkpoint is eligible only under its own run",
      '(outcome.get("state"), outcome.get("external_run_ref")) != (\n'
      '            "validated", bundle["external_run_ref"])',
      '(outcome.get("state"),) != (\n            "validated",)', HOLDOUT),
    m("p3_eval_failed_accepted", "only a succeeded evaluation counts", RESULT,
      RESULT.replace('result.get("state")', '"succeeded"'), HOLDOUT, MANUAL),
    m("p3_eval_other_dataset_accepted", "the evaluation is of the bundle's dataset", RESULT,
      RESULT.replace('result.get("dataset_ref")', 'bundle["dataset_ref"]'), HOLDOUT),
    m("p3_eval_other_split_accepted", "the evaluation is on the holdout split", RESULT,
      RESULT.replace('result.get("split")', '"holdout"'), HOLDOUT),
    m("p3_eval_other_holdout_accepted", "the evaluation is on the pinned holdout", RESULT,
      RESULT.replace('result.get("holdout_sha256")', 'bundle["holdout"]["sha256"]'), HOLDOUT),
)


def case_names() -> set[str]:
    tree = ast.parse((API_DIR / SUITE).read_text())
    return {node.name for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")}


RUNNER = Runner(name="p3", targets=(SUITE,), require_every_case=True)


def run_mutant(mutant: Mutant) -> Result:
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run the P3 training mutation list"))
