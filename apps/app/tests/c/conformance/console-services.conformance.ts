/**
 * The EXPORTED console conformance suite, run against C1's real services.
 *
 * Deliberately not named `*.test.ts`: `pnpm test` must stay green, and this file cannot be green yet
 * because `runConsoleServicesConformance` covers the whole interface, including the mutations C3 owns
 * and the trace content C2 owns. `../console-conformance.test.ts` runs this file in a child process
 * and asserts, case by case, which cases pass and that the failures are exactly the ones whose
 * operations are not C1's — so an unimplemented operation is *visible* rather than quietly skipped,
 * and a read case that regresses fails `pnpm test`.
 *
 * Run it directly to read the case list:
 *   node --test --test-reporter=spec tests/c/conformance/console-services.conformance.ts
 */

import { runConsoleServicesConformance } from "../../../lib/contracts/conformance.ts";
import { makeConsoleHarness } from "../harness.ts";

runConsoleServicesConformance(makeConsoleHarness, "C1 ConsoleServices (in-memory query port)");
