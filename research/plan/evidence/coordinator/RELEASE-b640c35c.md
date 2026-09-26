# RELEASE freeze: b640c35c (b640c35ccf6f3f6ff1dc60610978a531cd7bf421)

Candidate frozen 2026-09-26T21:34Z after the G2-FIX merge (all program lanes merged; GO-LIVE order, user decision 19:50Z: install and App release ahead of BACKEND-READY, certify + E1B on the live system afterwards). Every check runs on a fixed `git clone --shared` at this sha; e2c and i8 are held free for the G2 run. Gate definitions: [infra/rollout/README.md §0](../../../../infra/rollout/README.md).

## Identities (E4C-runbook §2; step 0.1)

| Identity | Value |
|---|---|
| RELEASE | `b640c35ccf6f3f6ff1dc60610978a531cd7bf421` (`dirty: false`) |
| step 0.1 (P-18 committed before the run) | `e83572edef9177826af3f7b5b1d25ec0b8bc51cc 2026-09-25T19:43:41+00:00` (`git log -1 -S'"latency_p95_s": 9.0' -- tests/integration/backend/certify.py`); E4B-protocol amendment 6 (2026-09-25) present at line 15 |
| serving-version.json | sha256 `66f9f5e6893831c744817632bf0afd528cffdef464af153f369917c68fee663a`; serve.sh `9e1f473bd7d8d7ac593468e2e4c4049b0bbff1c78f93cdbf72577b894fb6dd11` |
| engine_options_digest | `sha256:3c4bbface108e019b55a71121e1f3aaa23268bc1d1bd100257b0e2c68c036147` (recomputed equal) |
| runtime image | `vllm/vllm-openai@sha256:4cbfd34aac145fd1870381c030131c7f868fcad45448f401ecdb5fd4ed020b42` (= the prepulled pin on the box) |
| model commit | `fd111fca4fc7897876fb0d7e9df22ca5ac8ab965` |
| weights_sha256 (R148 shard-set digest) | `sha256:91a81b77f73b647ff84d32cad09ded838966b1dea605024d1e082ae7f3b20bfc` (shards 5d78fa4d…, 01d40ec9…) |
| P-06 processor / preprocessor (served bytes) | `sha256:d89ef49ce9cd37fbf510158e13c1ef063d9286411c1ec9049932dbe0487143b1` / `sha256:27225450ac9c6529872ee1924fcb0962ff5634834f817040f444118116f4e516` (G4b ok) |
| tokenizer / chat_template | `sha256:06b9509352d2af50381ab2247e083b80d32d5c0aba91c272ca9ff729b6a0e523` / `sha256:273d8e0e683b885071fb17e08d71e5f2a5ddfb5309756181681de4f5a1822d80` |
| migrations (0001–0026) | `7beb3c1004634b93cbfe54c11c64a4ac5a14f676c97a8e502a95702a5995a841` |
| deploy tree / rollout tree / alert rules | `c2e05d8bb38681ab5c9bfb44fd05696831869da58d87d346478225e2edcff228` / `1dcd6dd50dc9b24b48c8962118cb7df9e4d980500b3895ea3b47ed523245d4a3` / `8eec8014fdc120bd54a7779b69a7226d470198a5569c4f7cb6d131a37d5c1243` |
| uv.lock / infrx package | `52723deb4b1ed8a993d993017953efeb973f10a7004e435997919bd195a10bb2` / `97737c309972cee51b801000cf1fc0ba5a7562e0b845954d2f92109ff8b9ed16` |
| contract_limits | `57dd15f4dbb62aadb8177fd21c4cfa475e9b869cca72241d62382322848ff61d` |

The full `certify.py --hashes` output is the freeze.json of E4C-runbook §7 (`scratchpad/wave4b/freeze-b640c35c/certify-hashes.json` on the coordinator host; copied into the box's certify tree at the window).

## Gates (README §0)

| Gate | Result | Where |
|---|---|---|
| G1 | PASS 21:34:46Z (`HEAD` = RELEASE, porcelain empty) | freeze-b640c35c/G1.log |
| G2 make check (e2c form, Q 55430) | RUNNING from 21:35Z (~3.5 h) | freeze-b640c35c/G2-make-check.log |
| G2 tests/i on e2c | after make check | freeze-b640c35c/G2-tests-i.log |
| G3 | PASS (`"ok": true`; image `infrx-runtime:b640c35c`) | checks-b640c35c-gates.log |
| G4 | PASS `[]` | checks-b640c35c-gates.log |
| G4b (P-06) | PASS `ok` | checks-b640c35c-gates.log |
| G5 | PASS `REHEARSAL PASSED` (REHEARSAL_NS=infrx-pre, 0 leftovers) | checks-b640c35c-gates-g5.log |
| G6 | operator inputs (recorded in the readiness log) | — |
| BACKEND-LOCAL re-proof (E3C final on the e3c block) | PASS (exit 0; 16/16 scenarios s01–s16, 12/12 controls detected: 3 bypass, 2 revert, 2 DB defect, 1 classify, 3 SQL; 21:40–21:56Z; teardown 0 containers) | checks-b640c35c-e3c-run.log; e3c-b640c35c/verdict.json |
| APP-LOCAL re-proof (make app-e2e on e4b) | PASS (exit 0; preflight, console-test 0 fail, console-lint 0 errors, console-typecheck, browser journey 20/20 PASS in 102.9 s; 21:35–21:37Z; 78 gate unit tests before pnpm install) | checks-b640c35c-app.log; /tmp/infrx-e2c-app-e2e-uemiti5r/verdict.json |

Pre-checks on the previous tip e8222205 (G2-FIX is the only difference): G3/G4/G4b/G5 PASS, app-e2e PASS (journey 20/20).

## Staging (W1) — user-run

The coordinator's W1 call (`release-bundle.sh` → `ssm.sh <out>/<sha>.fetch.sh` → `20-prepull.sh`) was denied by the tool policy ("Production Deploy") at 21:38Z and is not retried; the operator runs it from the repo root once the gates are green (or now, outside the window — the bundle is inert until W8):

```
env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN apps/infrx-api/deploy/release-bundle.sh b640c35ccf6f3f6ff1dc60610978a531cd7bf421
env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN infra/rollout/ssm.sh <out>/b640c35ccf6f3f6ff1dc60610978a531cd7bf421.fetch.sh
env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN infra/rollout/ssm.sh infra/rollout/steps/20-prepull.sh RELEASE=b640c35ccf6f3f6ff1dc60610978a531cd7bf421
```

## Verification log

- 2026-09-26T21:40Z: identities computed on the fixed clone; G1/G3/G4/G4b/G5 PASS; G2, E3C and App re-proofs running; W1 user-run.
- 2026-09-26T21:58Z: E3C final PASS and app-e2e PASS at RELEASE; BACKEND-LOCAL re-proven, APP-LOCAL accepted (GO-LIVE decision) in the overlay (rev 141). G2 still running.
