#!/usr/bin/env node
// UX-11 final acceptance: the Lab accessibility pass's mutant runner (R32) on the shared Lab harness
// (tests/l/shell/harness.mjs). Each decision a UXV-L case claims - focus indicator, reduced motion, reflow
// at 200% zoom, AA contrast, named controls, focus return - is one edit to the Lab that a case it names
// must fail by assertion. The probe's own decisions are killed by the App's UXV-A01
// (apps/app/tests/ux/matrix/run-mutants.mjs). Each mutant serves its own copy of the harnesses.
// Usage: node tests/ux/matrix/run-mutants.mjs [--only ID,ID]
import { m, runMutants } from "../../l/shell/harness.mjs";

const SUITE = ["tests/ux/matrix/a11y.check.ts"];
const CSS = "app/lab.css";
const DIALOG = "components/ui/dialog.tsx";
const POPUP = "<Base.Popup className={`lab-dialog lab-dialog--${placement}`}>";

const C = {
  l01: "UXV-L01 at 200% zoom (640×400 CSS px) neither page scrolls sideways, and every Tab stop is drawn in view, uncovered, with a focus indicator",
  l02: "UXV-L02 at 200% zoom the dialog and the drawer open by keyboard, fit, and close back to their trigger",
  l03: "UXV-L03 every control is named and all active text meets AA contrast, on both pages and inside the open dialog and drawer",
  l04: "UXV-L04 reduced motion: the pending spinner stands still, and opening the dialog or the drawer moves nothing",
};

const MUTANTS = [
  m("UXV-LX01", "the focus indicator is removed", CSS, ".lab :focus-visible {\n  outline: 2px solid var(--ring);", ".lab :focus-visible {\n  outline: none;", [C.l01]),
  m("UXV-LX02", "the spinner spins under reduced motion", CSS, "  .lab-spin {\n    animation: none;\n  }\n", "", [C.l04]),
  m("UXV-LX03", "a long identifier widens the page at 200%", CSS, ".lab-id {\n  overflow-wrap: anywhere;", ".lab-id {\n  white-space: nowrap;", [C.l01]),
  m("UXV-LX04", "secondary text drops below AA", CSS, "  --muted-foreground: oklch(0.708 0 0);", "  --muted-foreground: oklch(0.38 0 0);", [C.l03]),
  m("UXV-LX05", "the overlay's close button loses its name", DIALOG, "aria-label={closeLabel}", "", [C.l03]),
  m("UXV-LX06", "focus is not returned to the trigger", DIALOG, POPUP, POPUP.replace("<Base.Popup ", "<Base.Popup finalFocus={false} "), [C.l02]),
  m("UXV-LX07", "the overlays turn under reduced motion", DIALOG, POPUP, POPUP.replace("<Base.Popup ", '<Base.Popup style={{ animation: "lab-spin 400ms" }} '), [C.l04]),
];

process.exit(await runMutants({ suite: SUITE, prefix: "UXV", mutants: MUTANTS }));
