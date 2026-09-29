-- SR-LCR-1 (lane lab-sql-lw8, LCR-F1): the Lab control unit (`infrx.lab.control.app`, R237:
-- the only /lab/v1/* server on the box) composes every Lab family on its own login
-- `infrx_lab_control` (0043), which held EXECUTE only on the lab_control_* RPCs and L2's reads,
-- so every family but control answered its typed 503 there. This file grants that login
-- EXECUTE on the D7/D8/D9 functions the families' ROUTE halves call on the unit (derived from
-- `pilot._lab` / `_lab_checkpoints` and the routes' services) and nothing else. Additive:
-- grants only; no table, function or other role's privilege is touched (service_role keeps
-- 0004's default EXECUTE; anon, authenticated and infrx_runtime gain nothing). Worker-only
-- claims (import-job claim/heartbeat/finish, evaluation leases and attempts, the outbox pass,
-- judge and teacher submission work) stay refused to this login.
-- LOCAL-ONLY (R150/R151/R201): never applied hosted; the number is the next free at merge.
--
--   datasets        lab_resolve, lab_publish, lab_accessible_samples (lineage status),
--                   lab_import_job_enqueue, lab_import_job (0051's queue, R243); 0041's reads
--                   lab_blocked_samples (lineage.status) and lab_permitted_samples
--                   (lineage.permitted: a version's derive/export/read_part, pipelines'
--                   label imports/select/export, training prepare). 0041's writes
--                   (lab_bound_samples, lab_tombstone_samples) stay worker/T3-only.
--   evaluations     lab_run_status, lab_cancel_run
--   pipelines       D8's label log (lab_label_append, lab_label_events), the external-run
--                   ledger (lab_external_run_get/_move, lab_run_reserve/_release/_settle,
--                   lab_pipeline_note/_noted), checkpoint import and approval (D7's
--                   lab_receive_checkpoint, lab_checkpoint_transition, 0053's
--                   lab_checkpoint_receipt; D8's lab_checkpoint_subscriptions; B1's freeze:
--                   lab_put_evaluator, lab_create_run); teacher batches (read: lab_judge_run,
--                   lab_teacher_failures)
--   releases        lab_releases_in (0048), lab_release (0039), lab_release_decisions (0053),
--                   lab_release_proposals, lab_propose_release (0043)
--   checkpoints     lab_checkpoint_record_event (D8) beside D7's receive above
--
-- ROLLBACK (this file alone): revoke execute on every function below from infrx_lab_control.

grant execute on function
  infrx.lab_resolve(jsonb), infrx.lab_publish(jsonb), infrx.lab_accessible_samples(jsonb),
  infrx.lab_import_job_enqueue(jsonb), infrx.lab_import_job(jsonb),
  infrx.lab_blocked_samples(jsonb), infrx.lab_permitted_samples(jsonb),
  infrx.lab_run_status(jsonb), infrx.lab_cancel_run(jsonb),
  infrx.lab_put_evaluator(jsonb), infrx.lab_create_run(jsonb),
  infrx.lab_receive_checkpoint(jsonb), infrx.lab_checkpoint_transition(jsonb),
  infrx.lab_checkpoint_receipt(jsonb),
  infrx.lab_label_append(jsonb), infrx.lab_label_events(jsonb),
  infrx.lab_external_run_get(jsonb), infrx.lab_external_run_move(jsonb),
  infrx.lab_run_reserve(jsonb), infrx.lab_run_release(jsonb), infrx.lab_run_settle(jsonb),
  infrx.lab_pipeline_note(jsonb), infrx.lab_pipeline_noted(jsonb),
  infrx.lab_checkpoint_record_event(jsonb), infrx.lab_checkpoint_subscriptions(jsonb),
  infrx.lab_judge_run(jsonb), infrx.lab_teacher_failures(jsonb),
  infrx.lab_releases_in(jsonb), infrx.lab_release(jsonb), infrx.lab_release_decisions(jsonb),
  infrx.lab_release_proposals(jsonb), infrx.lab_propose_release(jsonb)
  to infrx_lab_control;
