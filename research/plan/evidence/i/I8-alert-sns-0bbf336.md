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

## Fix round (verifier ACCEPT_WITH_FIXES on 3e5e725e; code head 49c92e8d)

| id | fix | regression (red before, green after) |
|---|---|---|
| F1 (medium) | `post(url, …)` builds the Request and opens it inside one try; `except Exception` → -1 (exit 4, kept). `InvalidURL` (a ValueError) no longer escapes as a traceback carrying URL fragments. Non-https → BLOCKED through `send()` (exit 3, kept), no SystemExit. | `test_ops_alert_sns__a_malformed_webhook_url_is_kept_and_never_printed`: subprocess under `python -X dev` with `https://…/<token> x` (exit 4), `https://…:<token>/x` (exit 4), `http://…/<token>` (exit 3); token and host absent from stdout+stderr; UNDELIVERED written each time. The verifier's three probes replayed by hand: exit 4/4/3, token_leak 0, kept 1. |
| F2 (low) | `publish()` catches `Exception` → `sns=<Type> topic=<name>`, -1 (exit 4, kept); the botocore import is gone (no longer needed). | `…a_failed_publish_is_kept_and_retried` adds `RuntimeError(<marker>)`: exit 4, kept, `sns=RuntimeError` printed, marker absent. |
| F3 (low) | status default -1 (computed inside the try, so a non-dict answer is a failure too). | same case: `{"MessageId"}` and `None` answers → exit 4, kept. |
| F4 (low) | 72 refuses `[[:cntrl:]]` in ALERT_OWNER / ALERT_ESCALATION (exit 2) before any write. | `test_ops_continuous__the_monitor_takes_an_sns_topic_as_the_other_destination` adds a newline-owner and a CR-escalation case: exit 2, no env file, no aws/systemctl call. |

Mutants added and killed: `webhook_value_error_escapes` (F1), `sns_missing_metadata_is_success` (F3),
`observe_install_owner_multiline` (F4); all 13 delivery/SNS mutants rerun: 13/13 killed.

| cmd (apps/infrx-api) | exit | result |
|---|---|---|
| `INFRX_D_TASK=i8 uv run --frozen pytest -q tests/i/test_alert_sns.py tests/i/test_ops_steps.py -k sns` before the fix | 1 | 3 failed (F1, F2/F3, F4 cases) |
| `INFRX_D_TASK=i8 uv run --frozen pytest -q tests/i/test_observe.py tests/i/test_ops_steps.py tests/i/test_alert_sns.py` | 0 | 32 passed, 1 xfailed |
| `uv run --frozen pytest -q tests/i/test_rollout.py` | 0 | 8 passed |
| `INFRX_D_TASK=i8 uv run --frozen pytest -q tests/i/test_mutants.py` | 0 | 50 passed (its first ~25 s overlapped the focused run above; passed regardless) |
| `uv run --frozen python tests/i/mutants.py <13 delivery/SNS mutants>` | 0 | 13/13 killed |
| `ruff check` (changed Python), `bash -n` 72/74 | 0 | clean |

Runbook `observe.md` delivery section: non-https BLOCKED; failed-send list (malformed URL, any SNS
error, no 2xx), only the error type printed. Remaining estimate unchanged (0 / 0.25 / 1 h, high).
