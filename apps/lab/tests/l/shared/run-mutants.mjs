#!/usr/bin/env node
// W6 lab-A's mutant runner (R32; LANE-RULES addendum) over tests/l/shared, on the shared Lab harness:
// the `link:` dependency (LAB-01) and the shared package's Lab-only surface (LAB-02).
// Usage: node tests/l/shared/run-mutants.mjs [--only ID,ID]
import { m, runMutants } from "../shell/harness.mjs";

const SUITE = ["tests/l/shared/shared.test.ts"];
const PKG = "package.json";
const LOCK = "pnpm-lock.yaml";
const PORT = "lib/services/traces/port.ts";
const VIEW = "components/traces/list/view-model.ts";
const PROXY = "proxy.ts";

const C = {
  s01: "W6A-01 @infrx/shared is a link: to packages/shared in package.json and the lockfile, never a file: copy",
  s02: "W6A-02 the shared package exports only the Lab contracts, and every Lab import of it resolves",
  s03: "W6A-03 the Lab's TraceLossReason union is exactly the contract's TraceLossReason values",
};

const MUTANTS = [
  m("W6A-X01", "package.json declares a file: copy", PKG, '"@infrx/shared": "link:', '"@infrx/shared": "file:', [C.s01]),
  m("W6A-X02", "the lockfile's specifier is a file: copy", LOCK, "specifier: link:../../packages/shared", "specifier: file:../../packages/shared", [C.s01]),
  m("W6A-X03", "the lockfile resolves a file: copy", LOCK, "version: link:../../packages/shared", "version: file:../../packages/shared", [C.s01]),
  m("W6A-X04", "the lockfile still carries a file: package entry", LOCK, "\n  '@jridgewell/gen-mapping@0.3.13':\n    resolution:", "\n  '@infrx/shared@file:../../packages/shared':\n    resolution: {directory: ../../packages/shared, type: directory}\n\n  '@jridgewell/gen-mapping@0.3.13':\n    resolution:", [C.s01]),
  m("W6A-X05", "the trace page comes from the App's deleted contracts copy", PORT, "export type TracePage = { items: TraceDetail[]; next_cursor: string | null };", 'import type { Page } from "@infrx/shared/console/types.ts";\nexport type TracePage = Page<TraceDetail>;', [C.s02]),
  m("W6A-X06", "the loss vocabulary comes from the App's deleted contracts copy", VIEW, "type TraceLossReason =\n", 'import type { TraceLossReason as Deleted } from "@infrx/shared/console/types.ts";\ntype TraceLossReason = Deleted |\n', [C.s02]),
  m("W6A-X07", "a Lab file imports a subpath the package does not export", PORT, 'import type { Actor,', 'import type {} from "@infrx/shared/contracts";\nimport type { Actor,', [C.s02]),
  m("W6A-X08", "the Lab's loss vocabulary drops the contract's 'abandoned'", VIEW, '| "malformed" | "abandoned";', '| "malformed";', [C.s03]),
  m("W6A-X09", "a root file side-effect-imports a subpath the package does not export", PROXY, 'import type { NextRequest } from "next/server";', 'import "@infrx/shared/contracts";\nimport type { NextRequest } from "next/server";', [C.s02]),
];

process.exit(await runMutants({ suite: SUITE, prefix: "W6A", mutants: MUTANTS }));
