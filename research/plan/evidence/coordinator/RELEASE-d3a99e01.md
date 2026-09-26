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
| G2 make check (e2c form, Q 55430) | RUNNING from 22:36Z (~3.5 h) | freeze-d3a99e01/G2-make-check.log |
| G2 tests/i on e2c | after make check | freeze-d3a99e01/G2-tests-i.log |
| G3 / G4 / G4b / G5 | PASS: G4 `[]`, G4b `ok`, G3 build + probe `"ok": true` (image `infrx-runtime:d3a99e01`), G5 `REHEARSAL PASSED` (REHEARSAL_NS=infrx-pre, 0 leftovers) — 22:36–22:40Z | checks-d3a99e01-gates.log |
| G6 | operator inputs (readiness log) | — |
| BACKEND-LOCAL re-proof (E3C final, e3c block) | PASS exit 0: 16/16 scenarios, 12/12 controls detected (28 PASS rows, 0 FAIL); 22:36–22:50Z; teardown 0 containers | checks-d3a99e01-e3c-run.log; e3c-d3a99e01/verdict.json |
| APP-LOCAL re-proof (make app-e2e, e4b) | PASS exit 0: preflight, console-test, console-lint, console-typecheck, browser journey 20/20 (103.4 s); 22:36–22:38Z; 78 gate unit tests before pnpm install | checks-d3a99e01-app.log; /tmp/infrx-e2c-app-e2e-pcd4nr9g/verdict.json |

At b640c35c (same non-test tree): G3/G4/G4b/G5 PASS, E3C final PASS 16/16 + 12/12, app-e2e PASS 20/20, G2 FAIL (tests/i coverage) — the reason for this candidate.

## Staging (W1) — user-run (tool-policy denial "Production Deploy" at 21:38Z on the previous candidate; the same three commands with this sha):

```
env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN apps/infrx-api/deploy/release-bundle.sh d3a99e01869b6b9c85173e25888abbbf9ed4dd29
env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN infra/rollout/ssm.sh <out>/d3a99e01869b6b9c85173e25888abbbf9ed4dd29.fetch.sh
env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN infra/rollout/ssm.sh infra/rollout/steps/20-prepull.sh RELEASE=d3a99e01869b6b9c85173e25888abbbf9ed4dd29
```

## Verification log

- 2026-09-26T22:38Z: candidate frozen; identities recomputed (only git.sha differs from b640c35c); G1 PASS; G2, gates, E3C and App re-proofs running.
- 2026-09-26T22:52Z: G3/G4/G4b/G5, E3C final and app-e2e PASS at d3a99e01; G2 make check running (api-test ~46% at 22:50Z).
