# ux-app (UX-04) — consumer model setup and documentation — evidence at 919de8e

Lane ux-app of wave 7 (LW7), branch `codex/w7-ux-app`, base `cd9f517c`, head `919de8e7` (implementation; this file and the update JSON are committed after it). No tasklocal key (fixtures only); no Docker, hosted Supabase, box, AWS or Vercel touched.

## Changed paths

- `apps/app/app/(console)/docs/examples.ts` — no credential in any form (each snippet requires `INFRX_API_KEY` and stops before any request without it; `{{KEY}}` gone); `videoUrlInput` (local https/no-credentials check, normalised); `VIDEO_URL_PLACEHOLDER` visibly incomplete and refused by the API; the URL written once per snippet as an escaped literal (POSIX single-quote, JSON string for Python/JS; curl builds bodies with `jq --arg`); async waits the 202's `Retry-After` before each status read and a 429/503's own hint, stops at a visible `MAX_POLLS`, reads a result only after `succeeded`; text example relabelled "Connectivity check (text)".
- `apps/app/components/snippet.tsx` — key selector and prefix+ellipsis substitution removed; footer states the key is never in the code; clipboard failure reports failure instead of "copied".
- `apps/app/app/(console)/models/first-call.ts` (new, pure) — guide state from U2 `keysPageModel` + first page of C0 `requests()`; completion only from a persisted `succeeded` row; bounded page with a cursor or a failed read => "Quickstart", no claim.
- `apps/app/app/(console)/models/first-call-panel.tsx`, `video-example.tsx` (new) — three steps (Create key via the existing `CreateKeyDialog` / existing keys as name+prefix metadata / Usage link), connectivity snippet, "Use a video" disclosure with the URL field (fills code only) and catalog `videoFacts`.
- `apps/app/app/(console)/models/page.tsx` — guide above the cards from `consumerSession()` (direct `.from("api_keys")` removed); compact card: "Accepting / Not accepting requests" with "listing is not a live health check", capability summary from modalities/modes, CREDIT figures, "Set up a call" / "API reference", limits/aliases/serving in a disclosure; retry link on an unavailable catalog.
- `apps/app/app/(console)/docs/page.tsx` — desktop Contents nav + mobile Contents disclosure in C-06 order; every existing anchor kept, `quickstart`/`datasets`/`errors` added; direct `.from("api_keys")` removed; `INFRX_API_KEY` set-up note; idempotency window stated with "after that the same key starts a new job"; bounded-poll/no-result-after-failure copy; example provenance as a secondary note (CI test double, not live acceptance).
- `apps/app/tests/a/examples.test.ts` — supplies the URL input so `example-calls.json` (the Python replay's input) is byte-identical; `tests/a/catalog-mutants.json` — four finds retargeted where UX-04 moved text (PAGE-PUBLIC-MODELS, PAGE-D-LIVE, EX-TOOLS, EX-HEADER).
- `apps/app/tests/ux/first-call/` (new) — `examples.test.ts` (8), `first-call.test.ts` (8), `pages.test.ts` (7), `fold.test.ts` (2, Chromium), `render.ts` (fixture harness), `run-mutants.mjs` + `mutants.json` (36).

## Commands (exit, counts)

| Command | Exit | Result |
|---|---|---|
| `node --test tests/ux/first-call/examples.test.ts` before slice 1 | 1 | red: `examples.ts does not provide an export named 'VIDEO_URL_PLACEHOLDER'` |
| `node --test tests/ux/first-call/first-call.test.ts` before slice 2 | 1 | red: `ERR_MODULE_NOT_FOUND` (models/first-call.ts) |
| `make console-test` (base cd9f517c) | 0 | 650 tests, 591 pass, 0 fail, 59 skipped |
| `make console-test` (head) | 0 | 675 tests, 618 pass, 0 fail, 57 skipped (+25 cases; the 2 fewer skips are tests/i2a, which run once `.next` exists) |
| `make console-lint` | 0 | 0 errors, 2 warnings (both pre-existing, `lib/contracts/fake-services.ts`) |
| `make console-typecheck` | 0 | clean |
| `make console-built` | 0 | build OK; tests/i2a 22 pass |
| `node tests/ux/first-call/run-mutants.mjs` | 0 | 2/2 self-checks; 36 mutants, 36 killed; every case declared by a mutant (runner guard) |
| `make console-mutants` | 0 | contracts 212/212, u 217/217, c 195/195, a 47/47, catalog 46/46, feedback 21/21 |
| `cd apps/infrx-api && uv run --frozen pytest -q tests/g/test_app_examples.py` | 0 | 1 passed (example-calls.json unchanged vs base) |
| `make api-lint` / `make api-typecheck` | n/a | no Python touched |

Failed-then-passed during the lane: `DOCS-ANCHOR` survived once because TAP escapes `#` in a case name (`/docs\#`); the case was renamed and the mutant then killed. `EX-TOOLS`/`EX-HEADER`/`PAGE-D-LIVE`/`PAGE-PUBLIC-MODELS` went stale after the edits and were retargeted (all killed). The async test's 503 hint was raised to 2 s (202 hint 1 s) after seeing that ignoring the 503 hint would otherwise survive.

## Oracle notes

- UX-T03 fixture half: `fold.test.ts` renders the real panel (TypeScript-transpiled TSX, React server renderer, the App's Tailwind compiled from `app/globals.css`) inside the console shell's layout classes and a w-60 sidebar column, in Chromium at 1280x720. New account: Create key at y=221..253. Existing key: no Create key button, the key listed by name+prefix, no code block carries the prefix. Approximation (ponytail note in `render.ts`): system fonts, sidebar as an empty column; the full-page visual suite is UX-11's.
- Completion: `first-call.test.ts` + mutants FC-*; no browser storage anywhere in `app/ components/ lib/` (`pages.test.ts`), no new route handler (create-file mutant ROUTE-INFER), no product table/RPC/fetch in the owned pages.
- Escaping: a URL carrying `'`, `$(...)`, backticks, `${...}` and `\` reaches the fake gateway byte-for-byte from curl, Python and JavaScript, and creates no file.

## Wiring requests

1. `Makefile` `console-mutants`: append `	cd apps/app && node tests/ux/first-call/run-mutants.mjs` (composed test: `make console-mutants` exits 0 with the line "36 mutants, 36 killed").

## Open items

- The published record has no display name or one-line description, so the card heads with the canonical id and the guide says "Make your first request" (not "Marlin 2B" / "Make your first Marlin request"): hard-coding the name would be a model fact outside the catalog. A `display_name`/`summary` on the published record is an API-contract request for the AP owner.
- `fold.test.ts` launches Playwright's Chromium inside `make console-test`; a host without `pnpm exec playwright install chromium` fails it with that instruction (never a skip). If the coordinator prefers browsers outside `console-test`, the file moves to a separate target (Makefile wiring).
- "Continue to quickstart" inside the key dialog was not added: the dialog is reused in place on /models and closes back to the guide (router.refresh), so no return route or URL state is needed; the dialog file (UX-07/U2) is unchanged.
- Clipboard failure in `Snippet` is not covered by a test (needs a denied-clipboard browser fixture).

## Estimate (remaining, review + merge fixes)

optimistic 1 h, likely 2 h, pessimistic 4 h; confidence medium; basis: all slices done and green; remaining work is review findings and a possible merge reconciliation with ux-foundations (UX-01 edits `docs/content.ts` and may touch `tests/a/catalog-mutants.json`).
