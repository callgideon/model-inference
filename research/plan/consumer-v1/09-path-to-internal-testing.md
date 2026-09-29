# Path to v1 internal testing (Lab) — completion plan

Written 2026-09-29T07:35Z by the coordinator at tip `b0504b44` (merge batches #1–#35, #37; 45 tasks implemented; rulings through R221). The consumer v1 is live; this plan closes the post-launch Lab program locally and deploys it for **internal testing**.

## Definition of done

1. **COMPLETE-LOCAL**: every Lab gate runner (E3L, E5L, E6L, E7L, E8L) has run for real on its stack at one tip SHA with **no FAIL cell**; every NOT RUN cell is outside local scope (a GPU, a staging target, an external training/teacher provider) and carries its exact rerun command. Ruling (coordinator, to number at the next merge): a local gate is accepted on that basis.
2. **INTERNAL-TESTING**: the Lab services run on the pilot box under the R151 window with every switch ON, the Lab app is deployed for the operator's testers, and one tester completes the internal-testing checklist in the runbook.

## Phase A — local closure (autonomous; ETA 2026-09-30T00:00Z)

| Lane | What it closes | State (2026-09-29T07:35Z) | ETA |
|---|---|---|---|
| merge #36 (composition-4) | LAB_TEACHERS, the P2 collect() caller, P3's Evaluations port; E7L 20 PASS / 0 FAIL / 2 NOT RUN | verify lens | +1 h |
| lab-rollout-3 | E8L final: WR-E8L-3, k09 with the real ControlReads, `make lab-rollout` | gate run | +3 h |
| lab-app-control | the Lab App's control HTTP adapter; the UI journey J01/J02 on the real L3 | implementing | +3 h |
| lab-observe-2 | E5L final: the runner sidesteps the foreign e5l volumes (compose project override), o06 bound, `make lab-observe` | dispatched | +4 h |
| composition-5 | WR-B3-3/suites, the rollout pass loop (WR-R2-3), the judge/outbox/import-job passes (0049–0051); E6L j09 checkpoint half | dispatched | +7 h |
| lab-deploy-prep | `make lab-local` = every switch ON locally (the E4-ON regression), the box runbook, the Lab rollout step scripts + tests | dispatched | +8 h |
| lab-evaluate-2 | E6L final run after composition-5 | after composition-5 | +10 h |
| COMPLETE-LOCAL | all five gates accepted at one SHA; the manifest flips E5L/E6L/E7L/E8L | after the above | +16 h |

Cells that stay NOT RUN by design: E7L i07 training half (P-11, an external training provider), E7L i09 / E8L k08 (a staging GPU target), E8L k10's provider-UI e2e (tracked as WR-C4-UI, not a gate cell).

## Phase B — internal-testing deployment (needs the operator's inputs; ETA within 24 h of the inputs)

Inputs the coordinator cannot supply (each is a user-held action):

1. **Host cleanup** (the foreign leftovers block the E5L and b3 keys): `docker rm -f infrx-d2-valkey infrx-d1-postgres infrx-t2f-postgres infrx-b3-postgres` and `docker volume rm infrx-e5l_clickhouse-data infrx-e5l_postgres-data infrx-e5l_s3-data` (check `/tmp/infrx-*.lock` holders first; lab-observe-2 no longer needs the volumes gone).
2. **P-21**: `sudo sysctl -w net.ipv4.ip_local_reserved_ports=57000-57599` on the development host (every gate provisioning collision this week was in that band).
3. **P-08**: the Lab deployment project (a Vercel project for `apps/lab`, its origin and the Supabase auth callback URL), the operator identity and the first tester memberships; the `infrx_lab_control` login password as an SSM parameter (WR-I2L-4).
4. **The R151 window**: approval to apply migrations 0027–0051 hosted. The coordinator runs the known-good re-proof and the `EXPECTED_PENDING` patch through a lane first; the apply runs through `infra/rollout/ssm.sh` inside the window.
5. **E4C** (backend certify on the box): the certify window start line, already queued.

Then, in order (the lab-deploy-prep runbook `08-lab-internal-testing-rollout.md`): apply the migrations → install the Lab units and env files (SSM names only) → switches ON one family at a time with a smoke after each → deploy the Lab app → onboard the testers → run the internal-testing checklist → record the first tester's pass as INTERNAL-TESTING accepted.

## Out of scope for internal testing (deferred, not blocking)

G5 (callbacks, C1), I4 (fleet, C2), X1–X6 (expansion, C3): conditional waves per `07-post-launch-waves.md`; no work is scheduled.

## Verification log

- 2026-09-29T07:35Z: written at tip b0504b44; three lanes dispatched (composition-5, lab-observe-2, lab-deploy-prep) alongside the three in flight.
