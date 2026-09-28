// The real half's fixtures (real/dump.py): D7 run records from lab_run_status on PostgreSQL and B2
// reports from infrx.evaluation.reports.compare, committed as produced.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import type { Report, Run } from "../../lib/services/evaluation/port.ts";

const load = (name: string) => JSON.parse(readFileSync(new URL(`./real/${name}.json`, import.meta.url), "utf8"));
export const RUNS: Record<"succeeded" | "cancelled" | "queued", Run> = load("runs");
export const REPORTS: Record<"reject" | "inconclusive", Report> = load("reports");
// A one-line diff: node elides long deepEqual diffs with "..." lines, which the mutant harness reads as the end.
export const same = (actual: unknown, expected: unknown, message?: string) => assert.equal(JSON.stringify(actual), JSON.stringify(expected), message);
