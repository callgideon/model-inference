# Runbooks — headless Marlin endpoint (I3B.c)

Operator procedures for the single-GPU pilot box `i-0e8449a4ffca29bab` (g6e.2xlarge,
us-east-1d) and the hosted Supabase project `fcbnscgsymzdykendbrc` (us-east-2). Every alert
in [`infra/alerts/alerts.json`](../alerts/alerts.json) links to a section here, and every
procedure is backed by an executable drill in `tests/integration/backend/recovery/`.

| Runbook | Answers alerts | Drilled locally by | Box / hosted drill |
|---|---|---|---|
| [restart.md](restart.md) — engine, worker, gateway, host, drain, saturation | ComponentDown, GpuUnavailable, PlatformFailureRate, LeaseLost, ReaperTerminalized, HostMemoryLow, InflightSaturated, RejectionsHigh, JournalSlow | `test_i3b_rc01`, `rc02`, `rc08`, `rc09` | pending coordinator |
| [restore.md](restore.md) — hosted Supabase backup/restore, box snapshot | — (planned, and before every hosted migration) | `test_i3b_bk01`, `bk01b`, `bk01c`, `bk02`, `bk03` | pending coordinator — **the hosted project has no backup today** |
| [rollout.md](rollout.md) — phase-2 checklist: order, hosted backup, settings, triggers | — | W6 block run by ROLLOUT-PREP (backup, restore check, copy apply) | pending coordinator |
| [rollback.md](rollback.md) — rollout rollback, maintenance switch | — | `test_i3b_bk04`; `rc10` pending I2B | pending I2B + coordinator |
| [disk.md](disk.md) — disk exhaustion, slow preparation | DiskAlmostFull, DiskFilling, PreparationSlow | `test_i3b_rc07` | pending coordinator |
| [index-loss.md](index-loss.md) — queue index loss, stall, saturation | QueueStalled, QueueSaturated, ComponentDown (index) | `test_i3b_rc06`, `rc09`; E3B `dr13` | pending coordinator |
| [reconcile.md](reconcile.md) — money drift, unknown usage, unsettleable jobs | ReconciliationDrift, ReconciliationStale, UnsettleableJobs, UnknownUsageBacklog, SettlementSlow, MetricsSanitizerRejections | `reconcile()` after every drill | pending D5/Q3 |

## Rules every runbook follows

1. **Log before you act.** Every paid or irreversible step (instance stop/start, volume
   swap, hosted migration, maintenance switch on hosted, DNS/security-group change) is
   written into the coordinator's session record with its purpose, expected cost and
   rollback **before** it runs (session-02 operating rule 1). Steps marked **[irreversible]**
   or **[cost]** below need that entry.
2. **Names, never values.** Secrets are SSM parameter *names*; a value is read into a shell
   variable on the machine that uses it, never echoed, never put in a command's argument
   list (pass it through the environment: `docker run -e PGPASSWORD …`), never committed.
3. **Windows are not measured yet.** Every recovery window is `⚠️ TO BE VERIFIED (P-18)`
   until the coordinator's box drill records it. Local drill timings are quoted as
   `meas. local` with the test id; they bound the software, not the box.
4. **Single GPU.** Automatic process recovery on one host is not high availability (P-16):
   while the engine or the host is down, the endpoint is down, and the runbooks say so.
5. **Never repair money by hand.** Wallet totals move only through the ledger trigger;
   no runbook step updates a balance, a hold or a job row directly ([reconcile.md](reconcile.md)).

## Running a step on the box (SSM)

There is no session-manager plugin on the coordinator host; every box step is a script
sent through `AWS-RunShellScript`, base64-wrapped so quoting survives. The shell's stale
`AWS_*` keys must be unset (CLAUDE.md).

```bash
AWS="env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN aws --region us-east-1"
BOX=i-0e8449a4ffca29bab

ssm() {  # ssm step.sh  -> runs step.sh on the box as root, prints status, stdout, stderr
  local payload id
  payload=$(python3 -c 'import json,sys; print(json.dumps({"commands": [sys.argv[1]]}))' \
            "echo $(base64 -w0 "$1") | base64 -d | bash")
  id=$($AWS ssm send-command --instance-ids "$BOX" --document-name AWS-RunShellScript \
        --parameters "$payload" --query Command.CommandId --output text)
  $AWS ssm wait command-executed --command-id "$id" --instance-id "$BOX" || true
  $AWS ssm get-command-invocation --command-id "$id" --instance-id "$BOX" \
       --query '[Status,StandardOutputContent,StandardErrorContent]' --output text
}
```

A step is written as a `bash` block meant to be saved to a file and passed to `ssm`; blocks
marked *coordinator host* run locally instead.

## Unit and path names

Today's box runs the pre-refactor monolith (I1B inventory): `marlin2b-vllm.service`
(engine, `127.0.0.1:8000`), `marlin2b-gateway.service` (`127.0.0.1:8001`), the `caddy`
container. The worker, Valkey and reaper units, the drain/rollback scripts and the
`/metrics` wiring arrive with I2B/W3; where a step needs one of them it says **PENDING I2B**
(or W3) and gives the name proposed in `infra/README.md` §2, to be replaced by the name I2B
ships.

## Verification log

- 2026-09-22 (I3B.c): Index written with the six runbooks. Every procedure is drilled
  locally (test ids above) on E2's stack; no step has been run on the box or on hosted
  Supabase. Recovery windows remain ⚠️ until the coordinator's drills.
- 2026-09-23 (ROLLOUT-PREP): rollout.md indexed (the phase-2 order; its W6 block was run once
  against hosted, read-only, and restored into a local copy).
