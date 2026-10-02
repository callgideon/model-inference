#!/usr/bin/env node
// UX-00's mutant runner (R32) on the shared Lab harness (tests/l/shell/harness.mjs): each foundation
// decision is one edit that a UX00 case it names must fail by assertion. Each mutant serves its own
// copy of the synthetic harness (next dev + Chromium), so a run takes a few minutes.
// Usage: node tests/ux/run-mutants.mjs [--only ID,ID]
import { m, runMutants } from "../l/shell/harness.mjs";

const SUITE = ["tests/ux/foundations.test.ts"];
const CSS = "app/lab.css";
const UI = "components/ui";

const C = {
  s01: "UX00-S01 every lab.css selector is scoped to the .lab root or a lab- class: no global element rule leaks",
  s02: "UX00-S02 the Lab's tokens are the App's dark theme values, one per shared name",
  s03: "UX00-S03 no production module reaches the synthetic harness or its fixtures",
  k01: "UX00-K01 the dialog takes focus, keeps Tab inside, closes on Escape and returns focus, at desktop and mobile widths",
  k02: "UX00-K02 the closed drawer has nothing focusable; the open drawer is a modal at the start edge",
  k03: "UX00-K03 a field's label, description and error are tied to its control, which is marked invalid",
  k04: "UX00-K04 the copy button says Copied only after the clipboard resolves, and a failure leaves selectable text",
  k05: "UX00-K05 every service state keeps the page heading, names itself and announces with the right role",
  k06: "UX00-K06 at 320px long identifiers wrap: the page never scrolls sideways",
};

const MUTANTS = [
  m("UX00-X01", "a bare element rule styles every page", CSS, ".lab body {", "body {", [C.s01]),
  m("UX00-X02", "a Lab token drifts from the App's", CSS, "  --warning: oklch(0.828 0.189 84.429);", "  --warning: oklch(0.8 0.2 90);", [C.s02]),
  m("UX00-X03", "the layout drops the .lab scope", "app/layout.tsx", "className={`lab ${sans.variable}", "className={`${sans.variable}", [C.s01]),
  m("UX00-X04", "a production component imports the harness", `${UI}/badge.tsx`, 'import type { ReactNode } from "react";', 'import type { ReactNode } from "react";\nimport type { Harness } from "../../tests/ux/browser";\nexport type Unused = Harness;', [C.s03]),
  m("UX00-X05", "a field with an error is not marked invalid", `${UI}/field.tsx`, "invalid={Boolean(error)}", "invalid={false}", [C.k03]),
  m("UX00-X06", "the error is not tied to the control", `${UI}/field.tsx`, '<Base.Error className="lab-field__error" match>\n          {error}\n        </Base.Error>', '<p className="lab-field__error">{error}</p>', [C.k03]),
  m("UX00-X07", "the dialog is not modal: Tab leaves it", `${UI}/dialog.tsx`, "<Base.Root open={open}", "<Base.Root modal={false} open={open}", [C.k01, C.k02]),
  m("UX00-X08", "focus is not returned to the trigger", `${UI}/dialog.tsx`, "<Base.Popup className={`lab-dialog lab-dialog--${placement}`}>", "<Base.Popup finalFocus={false} className={`lab-dialog lab-dialog--${placement}`}>", [C.k01, C.k02]),
  m("UX00-X09", "the closed drawer stays in the document", `${UI}/dialog.tsx`, "<Base.Portal>", "<Base.Portal keepMounted>", [C.k02]),
  m("UX00-X10", "the drawer opens in the middle of the page", `${UI}/dialog.tsx`, 'return <Modal placement="start" {...props} />;', 'return <Modal placement="center" {...props} />;', [C.k02]),
  m("UX00-X11", "Copied is shown before the clipboard resolves", `${UI}/copy-button.tsx`, '    setState("idle");\n', '    setState("copied");\n', [C.k04]),
  m("UX00-X12", "a refused copy reads as success", `${UI}/copy-button.tsx`, '() => setState("failed"),', '() => setState("copied"),', [C.k04]),
  m("UX00-X13", "a failed copy hides the value", `${UI}/copy-button.tsx`, '{state === "failed" ? <code className="lab-copy__fallback">{value}</code> : null}', "{null}", [C.k04]),
  m("UX00-X14", "an unavailable service is announced as mere status", `${UI}/service-state.tsx`, 'role={state === "unavailable" ? "alert" : "status"}', 'role="status"', [C.k05]),
  m("UX00-X15", "a pending submit can be sent again", `${UI}/button.tsx`, "disabled={disabled || pending}", "disabled={disabled}", [C.k05]),
  m("UX00-X16", "a long identifier widens the page", CSS, ".lab-id {\n  overflow-wrap: anywhere;", ".lab-id {\n  white-space: nowrap;", [C.k06]),
];

process.exit(await runMutants({ suite: SUITE, prefix: "UX00", mutants: MUTANTS }));
