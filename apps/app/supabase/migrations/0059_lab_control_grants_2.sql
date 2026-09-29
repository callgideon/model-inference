-- WR-LR7-GRANT (lane lab-rollout-7, 0-LR7-RV-1; merge #62, R259): the release page on the Lab
-- control unit (`infrx.lab.control.app`, R237) runs on the unit's own login
-- `infrx_lab_control` (0043). Its `progress` reads D9's Live (0054's `lab_release_live`,
-- WR-LIVE-PAGE, R244) and its read-time verdict reads B4's experiments (0043's
-- `lab_experiments`, WR-LR6-VERDICT's B2 report); 0056 granted neither, so
-- `/lab/v1/releases` answered 503 (InsufficientPrivilege) on the unit as soon as one release
-- was listed. This file grants that login EXECUTE on those two functions and nothing else.
-- Additive: grants only; no table, function or other role's privilege is touched
-- (service_role keeps 0004's default EXECUTE; anon, authenticated and infrx_runtime gain
-- nothing). R251: the control login's grant set is pinned in tests/d/test_code_mutants_lw8.py.
-- LOCAL-ONLY (R151/R201): never applied hosted; 0057 (lab-capture-2) and 0058 (lab-sql-lw9)
-- arrive with their lanes.
--
-- ROLLBACK (this file alone): revoke execute on both functions below from infrx_lab_control.

grant execute on function infrx.lab_release_live(jsonb), infrx.lab_experiments(jsonb)
  to infrx_lab_control;
