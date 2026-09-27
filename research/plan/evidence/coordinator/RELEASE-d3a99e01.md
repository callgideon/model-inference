# RELEASE freeze: d3a99e01 (d3a99e01869b6b9c85173e25888abbbf9ed4dd29)

Second candidate, frozen 2026-09-26T22:35Z: b640c35c + the E1B-MUTANTS merge (tests/i/mutants.py only — six mutants for the E1B window launcher and the tests/i copy layout; the b640c35c freeze failed G2 on that coverage). Every non-test identity is unchanged from [RELEASE-b640c35c.md](RELEASE-b640c35c.md); `certify.py --hashes` differs only in `git.sha`. GO-LIVE order (user decision 19:50Z) unchanged. Gate definitions: [infra/rollout/README.md §0](../../../../infra/rollout/README.md).

## Identities (E4C-runbook §2; step 0.1)

| Identity | Value |
|---|---|
| RELEASE | `d3a99e01869b6b9c85173e25888abbbf9ed4dd29` (`dirty: false`) |
| step 0.1 (P-18 committed before the run) | `e83572edef9177826af3f7b5b1d25ec0b8bc51cc 2026-09-25T19:43:41+00:00`; E4B-protocol amendment 6 present |
| serving-version.json / serve.sh | `66f9f5e6893831c744817632bf0afd528cffdef464af153f369917c68fee663a` / `9e1f473bd7d8d7ac593468e2e4c4049b0bbff1c78f93cdbf72577b894fb6dd11` |
| engine_options_digest | `sha256:3c4bbface108e019b55a71121e1f3aaa23268bc1d1bd100257b0e2c68c036147` (recomputed equal: True) |
| runtime image | `vllm/vllm-openai@sha256:4cbfd34aac145fd1870381c030131c7f868fcad45448f401ecdb5fd4ed020b42` |
| model commit | `fd111fca4fc7897876fb0d7e9df22ca5ac8ab965` |
| weights_sha256 (R148) | `sha256:91a81b77f73b647ff84d32cad09ded838966b1dea605024d1e082ae7f3b20bfc` |
| P-06 processor / preprocessor | `sha256:d89ef49ce9cd37fbf510158e13c1ef063d9286411c1ec9049932dbe0487143b1` / `sha256:27225450ac9c6529872ee1924fcb0962ff5634834f817040f444118116f4e516` |
| migrations / deploy tree / rollout tree / alert rules | `7beb3c1004634b93cbfe54c11c64a4ac5a14f676c97a8e502a95702a5995a841` / `c2e05d8bb38681ab5c9bfb44fd05696831869da58d87d346478225e2edcff228` / `1dcd6dd50dc9b24b48c8962118cb7df9e4d980500b3895ea3b47ed523245d4a3` / `8eec8014fdc120bd54a7779b69a7226d470198a5569c4f7cb6d131a37d5c1243` |
| uv.lock / infrx package / contract_limits | `52723deb4b1ed8a993d993017953efeb973f10a7004e435997919bd195a10bb2` / `97737c309972cee51b801000cf1fc0ba5a7562e0b845954d2f92109ff8b9ed16` / `57dd15f4dbb62aadb8177fd21c4cfa475e9b869cca72241d62382322848ff61d` |

Full output: `scratchpad/wave4b/freeze-d3a99e01/certify-hashes.json` (coordinator host).

## Gates (README §0)

| Gate | Result | Where |
|---|---|---|
| G1 | PASS 22:35:56Z | freeze-d3a99e01/G1.log |
| G2 make check (e2c form, Q 55430) | run 1 (22:36–01:57Z, started as an async job `… &` of a non-interactive bash): api-test 4576 passed / 43 skipped / 9 xfailed (37 min), api-mutants + console stages PASS, **bench-test FAIL** 1 failed / 117 passed — `test_leaks.py::test_a_second_ctrl_c_cannot_lose_the_summary` "handler restored" (SIG_IGN, not default_int_handler): bash starts async children with SIGINT ignored and the test pins the inherited handler as the default (a test assumption, not a tree defect; fix lane BENCH-SIGINT on the tip). `make bench-test` at d3a99e01 under `setsid` (SIGINT default): 118 passed. **Run 2 under `setsid -f` (02:00–05:20Z): PASS exit 0** — api-test 4576 passed / 43 skipped / 9 xfailed (36:54), api-mutants 3755 passed / 33 skipped (2:25:29), E4B list, console-test/lint/typecheck/built/mutants and bench-test all green (freeze-d3a99e01-2/G2-make-check.log) | freeze-d3a99e01/G2-make-check.log; freeze-d3a99e01-2/G2-make-check.log |
| G2 tests/i on e2c | PASS exit 0 (05:19–05:24Z) | freeze-d3a99e01-2/G2-tests-i.log |
| G3 / G4 / G4b / G5 | PASS: G4 `[]`, G4b `ok`, G3 build + probe `"ok": true` (image `infrx-runtime:d3a99e01`), G5 `REHEARSAL PASSED` (REHEARSAL_NS=infrx-pre, 0 leftovers) — 22:36–22:40Z | checks-d3a99e01-gates.log |
| G6 | operator inputs (readiness log) | — |
| BACKEND-LOCAL re-proof (E3C final, e3c block) | PASS exit 0: 16/16 scenarios, 12/12 controls detected (28 PASS rows, 0 FAIL); 22:36–22:50Z; teardown 0 containers | checks-d3a99e01-e3c-run.log; e3c-d3a99e01/verdict.json |
| APP-LOCAL re-proof (make app-e2e, e4b) | PASS exit 0: preflight, console-test, console-lint, console-typecheck, browser journey 20/20 (103.4 s); 22:36–22:38Z; 78 gate unit tests before pnpm install | checks-d3a99e01-app.log; /tmp/infrx-e2c-app-e2e-pcd4nr9g/verdict.json |

At b640c35c (same non-test tree): G3/G4/G4b/G5 PASS, E3C final PASS 16/16 + 12/12, app-e2e PASS 20/20, G2 FAIL (tests/i coverage) — the reason for this candidate.

## Staging (W1) and the pre-window steps — DONE by the coordinator 2026-09-27 (the user authorized production deploys at 04:1xZ: "no users at all … do not wait on my permissions")

| Step | Result |
|---|---|
| W1 bundle | `release-bundle.sh` uploaded `releases/d3a99e01….bundle` + `.sha256` (04:32Z) |
| W1 fetch | SSM 15298ba9: `d3a99e01….bundle: OK`; `refs/infrx/releases/d3a99e01…` in /home/ubuntu/model-inference; working tree unchanged (04:35Z) |
| W1 prepull | SSM 8efde5d7: `vllm/vllm-openai@sha256:4cbfd34a…` prepulled for d3a99e01 (04:45Z) |
| W3 inventory | logged (scratchpad/wave4b/w3-inventory-20260927T043xZ.log): infrx-worker, marlin2b-gateway, marlin2b-vllm active; env keys INFRX_MODE/INFRX_IMAGE/INFRX_RELEASE_SHA/SUPABASE_URL/ENGINE_MAX_NUM_SEQS; three `pre-*.head` markers (04:39Z) |
| W4 save edge | SSM: `/opt/dlami/nvme/w4-logs/Caddyfile.live-20260927T044546Z` sha256 `31df273cd06219145ffa330ae36e11c5c70a0546212e51a0066beb2b0be1c5a7` (04:45Z) |
| W2 snapshot | root volume `vol-091e45c92f7426291` → `snap-0a3e2debff48e47d0` started 04:46:15Z (pending; must be `completed` before W5) |
| Hosted project | the box runs against `fcbnscgsymzdykendbrc.supabase.co` (read-only step; keys never read) |

The original W1 commands (kept for the record):

```
env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN apps/infrx-api/deploy/release-bundle.sh d3a99e01869b6b9c85173e25888abbbf9ed4dd29
env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN infra/rollout/ssm.sh <out>/d3a99e01869b6b9c85173e25888abbbf9ed4dd29.fetch.sh
env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN infra/rollout/ssm.sh infra/rollout/steps/20-prepull.sh RELEASE=d3a99e01869b6b9c85173e25888abbbf9ed4dd29
```

## Verification log

- 2026-09-26T22:38Z: candidate frozen; identities recomputed (only git.sha differs from b640c35c); G1 PASS; G2, gates, E3C and App re-proofs running.
- 2026-09-26T22:52Z: G3/G4/G4b/G5, E3C final and app-e2e PASS at d3a99e01; G2 make check running (api-test ~46% at 22:50Z).
- 2026-09-27T02:00Z: G2 run 1 FAIL at the last stage (bench-test) under an async-job SIGINT disposition; bench-test alone PASS 118 under setsid; G2 run 2 started under setsid (G1 PASS again). The freeze starter now uses `setsid -f`. RELEASE stays d3a99e01.
- 2026-09-27T04:47Z: W1 (bundle, fetch, prepull), W3, W4 and W2 (snapshot pending) done by the coordinator after the user's deploy authorization; G2 run 2 at the mutant stage ~85% (04:42Z).
- 2026-09-27T05:24Z: **FREEZE COMPLETE — every gate green at d3a99e01** (G1, G2 make check + tests/i, G3, G4, G4b, G5; E3C final 16/16 + 12/12; app-e2e 20/20). G6 inputs: SSM parameters present (pg_journal_url, supabase_url, supabase_service_role_key, runtime/monitor passwords, operator_key, e4b_api_key), the SNS subscription left unconfirmed by the user's decision (alerts skipped for v1). The install maintenance follows.
- 2026-09-27T05:26Z: **W5 done** (30-pause.sh RELEASE=d3a99e01…; SSM status Success; public /health 503). The maintenance window is open; no users (user decision).
- 2026-09-27T05:29Z: W6/W6b rehearsal 1 (`hosted-migrate.sh --through w6b`, no hosted write) stopped at the equality check: the only problem was `default_acls differ` on the platform-managed `realtime` schema (hosted's default for `postgres` carries grant options `a*r*`, the fresh copy's does not — Supabase changed it under hosted; the dump never carries that schema). Fix 5fe592de: pgrestore's `default_acls` comparison is scoped to the dumped schemas + the global entry, with a test_restore case (26 passed on the d4 Supabase image); hosted-migrate.sh's input check covers the three migration inputs. Rehearsal 2 rerun from the RELEASE checkout with the fixed tool.
- 2026-09-27T05:35Z: `infra/rollout/operator-cli.sh` added (the operator CLI with its environment read from SSM by name) for W7d/W7f/H-steps/X6.
- 2026-09-27T05:36Z: **W6/W6b rehearsal 2 PASS** (hosted-migrate.sh --through w6b at the RELEASE checkout with the fixed tool): verified dump `~/infrx-backups/hosted-20260927T053257Z` (3 checksummed files, `"equal": true`), hosted applied 0001–0018, **COPY_DIGEST = 4a6bffd9fd4112f793facdac79e58eaeadbd2681850f4978c546a52025f74909**, the copy applied exactly 0019–0026, flags credit_admission=false legacy_usd_admission=true signup_grant=true, drift_rows 0; no hosted write. W7d read-only check: signup_grant enabled (operator-cli.sh). The real W7 (`--through w7 --expect 4a6bffd9…`) was refused by the tool policy at 05:37Z and waits for the operator's allow rule; the script gained `--release` and a diff-equality check of the migration inputs against RELEASE so it runs from the integration tip as the first token.
- 2026-09-27T06:53Z: **W7 DONE (hosted apply 0019–0026)** via `hosted-migrate.sh --release d3a99e01… --through w7 --expect 4a6bffd9…` after the user's allow rules: fresh W6 dump `~/infrx-backups/hosted-20260927T065013Z` ("equal": true), W6b copy apply 0019–0026, hosted plan digest = COPY_DIGEST, apply exit 0, post plan `applied: 0001 … 0026 fenced_result` + `nothing pending`, hosted flags credit_admission=false legacy_usd_admission=true signup_grant=true, drift_rows 0. **MIGRATION_DIGEST = 4a6bffd9fd4112f793facdac79e58eaeadbd2681850f4978c546a52025f74909.** Log `~/infrx-backups/migrate-20260927T065013Z.log`.
- 2026-09-27T06:54Z: **X3 done**: Vercel production (callgideon/infrx-app) now carries NEXT_PUBLIC_SUPABASE_URL, NEXT_PUBLIC_SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY, NEXT_PUBLIC_APP_URL (pre-existing) + INFRX_API_BASE_URL=https://marlin2b.callbill.ai and CONSOLE_CURSOR_SECRET (generated, sensitive).
- 2026-09-27T06:54Z: **W7f H1** publish-card rc_marlin2b_20260925_launch (400/1200; deployment revision c0000004; effective 2026-09-27T06:00Z; idempotency pub-20260925-launch) exit 0. **Precheck** 10-inventory (SSM 43bc8670): infrx-worker inactive, marlin2b-gateway stopped (failed state after the drain), vllm active. **H2a** credit-transition --dry-run exit 0: blockers [], drift [], restart_with credit + the launch card; sha256 e59d1cb4615ca5196cc0fcedbf4bdf976f03100c6cb3c1437e7ab223ee9bd554 (scratchpad/wave4b/p02-dryrun.json) — P-02 satisfied. Key inventory: b5b74b8e (consumer, 2026-09-20), 04af08ec (consumer, 2026-09-20), d554db80 (operator), 142c7d81 (certify tenant, e4b), 0fdbb31f (revoked). **H2b** credit-transition exit 0 (06:56Z; idempotency cutover-20260925-launch; sha256 10664286b055ea87612febd04d454df9ba86518b7be57abff7c0773389880113): hosted flags **credit_admission=true, legacy_usd_admission=false, signup_grant=true**; restart_with ACCOUNTING_REGIME=credit ACTIVE_RATE_CARD_VERSION=rc_marlin2b_20260925_launch. CREDIT is the live regime from here (P-01).
- 2026-09-27T06:58Z: tool-policy refusals (server-side, despite the operator's allow rules): the main fast-forward push, W8 40-checkout, the H3 revocations. The remaining steps (H3, W8–W11, main push, W12 with a fresh revoked key, the internal test user) are packaged in `infra/rollout/go-live-remaining.sh` for the operator (reviewed before hand-over); the coordinator reads its logs and records W13.
