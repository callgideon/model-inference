# Central credits and individual signup grant

## Confirmed policy

The user confirmed **one-time 10,000 promotional credits per individual user**, not per organization and not a recurring allowance. The initial product has only a free plan. No payment collection, subscription, top-up or provider payout workflow is included in this launch.

Implementation policy: grant after verified email/identity onboarding. This verification timing is an engineering default, not a new plan tier. Granting must be an authoritative server/database operation; a page render, client claim or auth callback retry is never a second entitlement.

## Wallet and idempotency

- Introduce a CREDIT wallet with immutable `owner_user_id`. Map the user's initial personal consumer organization to that wallet; API keys continue to identify their consumer org. The server resolves the wallet, never a request-body wallet ID.
- Keep a unique initial grant entitlement for `(user_id, initial_signup_grant)`. The campaign/version is audit metadata; changing a campaign version must not make a user eligible again. Record wallet, amount, verification evidence reference and ledger operation ID.
- One transaction creates/locks the wallet, inserts the unique entitlement, appends `+10000.00000000` CREDIT and updates the wallet total. A retry returns the existing result. Persist before showing onboarding as credited.
- Creating another organization, joining a provider workspace, reconnecting an identity or returning after inactivity does not issue another grant. Do not permit consumer org creation/wallet transfer in the initial UI. Provider orgs have no signup credit entitlement of their own.
- Existing eligible verified users can receive the same initial grant once through an idempotent backfill/first-login path. Account deletion/recreation abuse requires the identity team's documented retention and eligibility policy; a UUID uniqueness constraint alone does not prove one real human.
- Email verification, signup rate limits and configurable abuse controls bound free-credit exposure. Record denied/held onboarding reasons without exposing account enumeration. No automatic replenishment on exhaustion.

Future team wallets and pooling require an explicit product policy. Do not let multiple memberships implicitly spend or transfer an individual's promotional balance.

Lab preview inference uses a distinct `provider_dev` wallet owned by the provider organization, initialized at zero and funded through an audited operator allocation. It is an internal capped testing budget, not a consumer plan or signup grant. Preview credentials resolve only that wallet and an approved internal CREDIT rate card; hold/settlement rules are identical. It cannot transfer balance to consumer wallets. External judge/training spending remains a separate USD budget. This minimal wallet-kind contract is frozen in F2P/D1R before Lab work, without making App depend on Lab UI.

## Credit units and rate cards

Credits are a product unit, not USD. Use exact `numeric(20,8)` CREDIT amounts and decimal strings across JSON; do not display a dollar symbol. This precision retains the durable plan's arithmetic model while separating units. Provider infrastructure costs, teacher budgets and any future payouts remain separate explicitly denominated USD amounts. No exchange rate is implied.

Initial meter: input and output tokens at model-specific credits per million, with preprocessing included. Publish a clear explanation for video that billed input tokens come from the versioned media processor. A future per-second/per-frame/session meter requires an explicit meter contract, maximum reservation rule and tests; the first implementation must not accept arbitrary billing formulas.

Each immutable rate card includes model/deployment applicability, CREDIT unit, meter kind/version, input/output rates, effective time, author/approver and rounding. Providers may propose rates for their models; platform operators set or approve publication initially. Until Lab exists, operators seed the identical records. The gateway accepts only approved, active cards. Changing a rate affects newly admitted requests, never prior holds or settlements.

Before sending a request, consumers see the current rate and supported limits. Do not describe 10,000 credits as 10,000 requests or promise a fixed amount of inference. Test fixture rates are not production prices. Production rates are an explicit launch input based on measured costs and promotional budget.

## Reservation and settlement

Preserve the existing durable protocol: reserve the validated maximum input/output credit cost before execution; reject insufficient available credit with stable 402 error; settle once with authoritative engine usage; release unused hold. Maximum holds round up; the final charge rounds half-up once to eight places. No binary floats.

Wallet total = sum of CREDIT ledger entries. Available = total minus active CREDIT reservations. There is one balance across eligible catalog models. Idempotency/recovery cannot double-charge. A failed request with no authoritative billable usage releases its hold; execution with authoritative consumed usage follows the published failure/cancellation charging policy. **Proposed initial policy:** charge measured consumed inference tokens even on cancellation or downstream disconnect, display the outcome alongside the charge; do not charge rejected or never-executed requests. This policy must be disclosed in App docs before launch.

For unknown usage, retain the prior reconciliation rule: quarantine, fence execution, wait the configured 24h interval, release only once terminality is established, and do not later debit the user. Provider/engine usage exceeding the reserved envelope is a platform incident, not an unreserved debit.

Signed grant adjustments require operator identity, allowed type, reason and durable idempotency. Providers cannot mint consumer credits. Corrections are new compensating entries. Internal evaluation/dev budgets never share a consumer hold implicitly.

## Existing USD records: do not relabel them

The baseline schema's `credit_ledger.delta_usd` and `usage_events.cost_usd` are historical USD values. They are not CREDIT units and must never be renamed or copied numerically into CREDIT totals.

1. Add separate CREDIT wallet/ledger/hold fields or relations; retain historical USD rows and summaries unchanged. Record an accounting regime/unit on all new usage and admissions.
2. Inventory existing users, USD balances and any accepted jobs in the remote implementation. Do not assume this checkout's old schema is still current there.
   Existing organizations with multiple members require an explicit billing-owner transition. Do not attach several users' individual grants to one shared wallet or silently give other members access to a personal wallet. Resolve that account mapping before cutover; consumer team pooling is deferred.
3. Freeze new old-regime admission at cutover, drain/fence old accepted work and reconcile it in its original denomination. No job changes unit mid-flight.
4. Issue eligible users' initial CREDIT grant independently and exactly once. Preserve the old USD balance in a separately labeled legacy statement.
5. If old balances must become spendable credits, require an explicit conversion policy/rate and a single audited conversion operation per source balance. **No conversion rate is chosen by this plan.** An unresolved nonzero legacy spendable balance is a rollout hold for that account, not permission to silently discard value.
6. Validate migration up/down compatibility without deleting accepted jobs, grants, holds or historical evidence. Rollback stops new admission if old code cannot read CREDIT records; never reactivate old debits against new wallets.

The architecture and new-user implementation can proceed before a conversion decision. Production cutover of affected existing accounts must resolve it.

## Required acceptance cases

| ID | Action | Passing condition |
|---|---|---|
| CREDIT-GRANT | Replay and race verification callbacks, initial login and backfill | Exactly one +10,000 entry for the individual; all retries resolve to it |
| CREDIT-IDENTITY | Add membership/provider capability, attempt another org, change campaign metadata | No new signup grant; no wallet ownership change or cross-user spending |
| CREDIT-UNITS | Upgrade nonzero/negative USD histories and new CREDIT wallets | Historical USD unchanged; neither balances nor costs summed across units |
| CREDIT-RATE | Change published rate while requests are queued/running | Every request settles at its admitted rate and serving revision |
| CREDIT-SPEND | Concurrent calls to two models exhaust the same wallet | No negative available balance or second settlement; UI reconciles exactly |
| APP-JOURNEY | Fresh verified signup → grant → key → real request → usage → exhaustion | No operator grant needed; correct balance and actionable 402 state |

These tests are required implementation evidence, not tests run during this documentation task.
