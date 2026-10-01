#!/usr/bin/env node
// W6 lab-A's mutant runner (R32; LANE-RULES addendum) over tests/l/shared, on the shared Lab harness:
// the `link:` dependency (LAB-01) and the shared package's Lab-only surface (LAB-02).
// Usage: node tests/l/shared/run-mutants.mjs [--only ID,ID]
import { m, runMutants } from "../shell/harness.mjs";

const SUITE = ["tests/l/shared/shared.test.ts"];
const PKG = "package.json";
const LOCK = "pnpm-lock.yaml";

const C = {
  s01: "W6A-01 @infrx/shared is a link: to packages/shared in package.json and the lockfile, never a file: copy",
};

const MUTANTS = [
  m("W6A-X01", "package.json declares a file: copy", PKG, '"@infrx/shared": "link:', '"@infrx/shared": "file:', [C.s01]),
  m("W6A-X02", "the lockfile's specifier is a file: copy", LOCK, "specifier: link:../../packages/shared", "specifier: file:../../packages/shared", [C.s01]),
  m("W6A-X03", "the lockfile resolves a file: copy", LOCK, "version: link:../../packages/shared", "version: file:../../packages/shared", [C.s01]),
  m("W6A-X04", "the lockfile still carries a file: package entry", LOCK, "\n  '@jridgewell/gen-mapping@0.3.13':\n    resolution:", "\n  '@infrx/shared@file:../../packages/shared':\n    resolution: {directory: ../../packages/shared, type: directory}\n\n  '@jridgewell/gen-mapping@0.3.13':\n    resolution:", [C.s01]),
];

process.exit(await runMutants({ suite: SUITE, prefix: "W6A", mutants: MUTANTS }));
