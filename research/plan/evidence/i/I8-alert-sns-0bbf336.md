# I8 ALERT-SNS — P-25's SNS destination form (0bbf336)

Base `b0858371`, head `0bbf3363` (code), branch `codex/alert-sns`. Nothing created in AWS,
nothing run on the box; boto3 faked in every test (no network).

## Changed paths
- `infra/observe/deliver.py`: `send()` picks exactly one destination; `publish()` (SNS, lazy
  boto3, region from the ARN, Subject = first line ASCII ≤100, Message = whole text). Both /
  neither / malformed ARN / no boto3 → BLOCKED (exit 3, kept in UNDELIVERED); ClientError or
  BotoCoreError → exit 4, kept, state untouched (retried). `post()` (webhook) unchanged; its URL
  is never printed; the SNS topic name is.
- `infra/rollout/steps/72-observe-install.sh`: `ALERT_SNS_TOPIC_ARN=<arn>` (plain value) written
  into `/etc/infrx-alert.env` (0600); both destinations or a malformed ARN → exit 2 before writing.
- `infra/rollout/steps/74-alert-test.sh`: passes `ALERT_SNS_TOPIC_ARN` through; semantics unchanged
  (BLOCKED exit 3 until the env file exists).
- `infra/observe/systemd/infrx-observe.service`: comment names the variable (no behaviour change).
- `infra/runbooks/observe.md`: both forms, IAM statement, test-alert flow.
- `apps/infrx-api/tests/i/test_alert_sns.py` (new, 5 cases), `test_ops_steps.py` (+2 cases),
  `mutants.py` (+7).

## Commands (apps/infrx-api)
| cmd | exit | result |
|---|---|---|
| `INFRX_D_TASK=i8 uv run --frozen pytest -q tests/i/test_alert_sns.py` against base deliver.py | 1 | 5 failed (red first) |
| `... pytest -q tests/i/test_ops_steps.py -k sns` against base 72/74/deliver | 1 | 2 failed (red first) |
| `INFRX_D_TASK=i8 uv run --frozen pytest -q tests/i/test_observe.py tests/i/test_ops_steps.py tests/i/test_alert_sns.py` | 0 | 31 passed, 1 xfailed |
| `uv run --frozen pytest -q tests/i/test_rollout.py` | 0 | 8 passed (step list unchanged) |
| `INFRX_D_TASK=i8 uv run --frozen pytest -q tests/i/test_mutants.py` | 0 | 50 passed (list well-formed, every case covered, default subset killed) |
| `uv run --frozen python tests/i/mutants.py <7 new>` | 0 | 7/7 killed |
| `uv run --frozen python tests/i/mutants.py <5 existing delivery/72/74>` | 0 | 5/5 killed |
| `ruff check` (changed Python), `bash -n` 72/74 | 0 | clean |

New mutants: sns_failure_swallowed, both_destinations_accepted, sns_subject_unbounded,
sns_without_boto3_passes, sns_branch_dropped, observe_install_drops_the_topic,
alert_test_drops_the_topic — all killed.

## Wiring requests
1. IAM (coordinator/infra, not applied): the box's instance role gets
   `{"Effect":"Allow","Action":"sns:Publish","Resource":"arn:aws:sns:us-east-1:641134885443:infrx-pilot-alerts"}`.
2. Operator: create the topic + e-mail subscription and confirm it (SNS accepts publishes with no
   confirmed subscriber).
3. Box: `python3` (system) must import boto3 for the SNS form (`apt install python3-boto3`);
   otherwise delivery is BLOCKED with that message. Verify with `python3 -c 'import boto3'`.
4. `infra/observe/observe.sh` line 9 comment still says "to ALERT_WEBHOOK_URL" (not owned; cosmetic).

## Open issues
- `sns=200` proves acceptance by SNS, not receipt: the owner's nonce confirmation stays the proof.

## Estimate
Remaining: optimistic 0 h, likely 0.25 h, pessimistic 1 h (review/merge); confidence high; basis:
all named suites and mutants green; box/AWS steps are coordinator ops.
