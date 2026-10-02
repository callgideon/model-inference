# Lab UI primitives — frozen interface (UX-00)

The contract the batch-2 Lab lanes (UX-03, UX-05, UX-06, UX-08, UX-09, UX-10) build on. Design
source: [02-foundations](../../../../research/design/v1/02-foundations.md). Change a signature here
only through the foundation owner (a coordinator-allocated UX-00 follow-up), never from a feature
lane; add lane-local components beside your pages instead.

## Ground rules

- **Scope.** `app/layout.tsx` puts `lab` (plus the Geist font variables) on `<html>`; `app/lab.css`
  holds every style, each selector `.lab …` or a `.lab-*` class (UX00-S01). No Tailwind, no global
  element rules, no inline colour values: use the tokens below. A lane's own styles go in a
  lane-local CSS module or a `.lab-<lane>-*` block added through the foundation owner.
- **Tokens** (on `.lab`, equal to the App's dark theme in `apps/app/app/globals.css`, UX00-S02):
  `--background --foreground --card --card-foreground --muted --muted-foreground --border --input
  --ring --primary --primary-foreground --destructive --success --warning --radius`, plus the Lab's
  `--inset` (code/form inset). Fonts: `var(--font-sans)`, `var(--font-mono)`.
- **Layout classes:** `lab-page` (max 1280px, 32/24/16px padding by width), `lab-stack` (16px grid
  gap, 24px block margin), `lab-id` (an identifier that wraps; pair it with `CopyButton`).
- **No cross-imports.** Nothing from `apps/app`; `@infrx/shared` stays contract-only. Icons come
  from `lucide-react` (1.47.0), behaviour from `@base-ui/react` (1.8.0); both pinned.
- **Fixtures.** Synthetic examples live only in `tests/ux/harness` (served by `tests/ux/browser.ts`);
  no module under `app/`, `lib/` or `components/` may import `tests/` (UX00-S03).
- **Server/client.** `Button`, `Field`/`Input`, `Badge`, `PageHeader`, `ServiceState` render from
  server components; `Dialog`, `Drawer` and `CopyButton` are client components (`"use client"`).

## Components

| Import (`@/components/ui/…`) | Signature | Behaviour you can rely on |
|---|---|---|
| `button` → `Button` | `ComponentProps<"button"> & { variant?: "primary" \| "secondary" \| "ghost" \| "danger"; size?: "md" \| "sm"; pending?: boolean }` | `type="button"` unless given; `pending` disables it and sets `aria-busy` (no double submit). One `primary` per page/step. |
| `button` → `buttonClass` | `(variant?, size?) => string` | The same look for a `<Link>` or a Base UI part. |
| `field` → `Field` | `{ label: ReactNode; description?: ReactNode; error?: string \| null; name?: string; children: ReactNode }` | Base UI ties label (`htmlFor`), description and error (`aria-describedby`) to the control and sets `aria-invalid` while `error` is set. Pass `error` only after interaction/submit; keep the user's value. |
| `field` → `Input` | Base UI `Field.Control` props | The styled control; `render={<textarea />}` or a select for other controls. Must sit inside a `Field`. |
| `badge` → `Badge` | `{ tone?: "neutral" \| "success" \| "warning" \| "danger" \| "info"; children: ReactNode }` | Icon + words, never colour alone. Map domain states to tones in your view model; `success` only for a proven outcome; `active` is not globally healthy. |
| `page-header` → `PageHeader` | `{ title: string; purpose?: ReactNode; breadcrumb?: { href: string; label: string }[]; actions?: ReactNode }` | The page's single `h1`, breadcrumb `nav`, purpose line, action slot. Render it in loading, empty and failure states too. |
| `service-state` → `ServiceState` | `{ state: ServiceStateKind; title: string; explanation: ReactNode; action?: ReactNode; diagnosticId?: string }` | `role="alert"` for `unavailable`, `role="status"` otherwise; `h2` title; `data-state` attribute. |
| `service-state` → `SERVICE_STATES` | `["loading", "empty", "unavailable", "denied", "not_found", "stale"]` | **The fixture-state names** every lane's fixtures and tests use. A transport failure is `unavailable`, never `empty`; `stale` labels the last successful observation and forbids mutations based on it. |
| `dialog` → `Dialog` | `{ title: ReactNode; description?: ReactNode; children: ReactNode; trigger?: ReactElement; open?: boolean; onOpenChange?: (open: boolean) => void; closeLabel?: string }` | WAI modal: focus enters, Tab stays inside, background inert, Escape and the close button dismiss, focus returns to the trigger; closed content is unmounted (UX00-K01). Controlled (`open` + `onOpenChange`) or uncontrolled. A one-time-secret dialog must still explain the loss on dismissal. |
| `dialog` → `Drawer` | same as `Dialog` | The same modal at the start edge, `min(280px, 85vw)` wide: the mobile navigation drawer (UX00-K02). Close it on route change in the shell (`usePathname` effect). |
| `copy-button` → `CopyButton` | `{ value: string; label: string }` | "Copied" only after the clipboard write resolves; a failure says so and shows `value` as selectable text (UX00-K04). `label` is the accessible name ("Copy deployment id"). |

## Verified (tests/ux, synthetic harness, Chromium)

`node --test tests/ux/foundations.test.ts` (part of `pnpm test` / `make lab-test`) runs
UX00-S01..S03 statically and UX00-K01..K06 in a browser at 1440px, 390px and 320px; the mutant list
is `node tests/ux/run-mutants.mjs`. A lane adding examples to the harness keeps them synthetic and
takes no screenshots of real services.
