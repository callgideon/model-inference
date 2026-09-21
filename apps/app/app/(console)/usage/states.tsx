import Link from "next/link";
import { Card, CardContent } from "@/components/ui/card";
import type { ViewState } from "./view-model";

/** The error branch of any `ViewState`; the panel takes the state itself, so nothing is re-derived. */
export type ErrorState = Extract<ViewState<unknown>, { kind: "error" }>;

/**
 * Markup only. What the user is offered comes from the view model's `recovery`: a reload for the
 * codes a reload can fix, the first page for a stale cursor, and nothing at all for a refusal that
 * another attempt cannot change.
 *
 * `Try again` is a plain anchor on purpose: it must re-run the server request, which a client-side
 * navigation to the URL already shown would not.
 */
export function ErrorPanel({
  title,
  state,
  href,
  firstPageHref,
}: {
  title: string;
  state: ErrorState;
  href: string;
  firstPageHref: string;
}) {
  return (
    <Card>
      <CardContent className="space-y-2 py-8 text-center">
        <p className="font-medium">{title}</p>
        <p className="text-sm text-muted-foreground">{state.message}</p>
        {state.recovery === "retry" ? (
          <a className="inline-block text-sm underline underline-offset-4" href={href}>
            Try again
          </a>
        ) : null}
        {state.recovery === "restart" ? (
          <Link className="inline-block text-sm underline underline-offset-4" href={firstPageHref}>
            Back to the first page
          </Link>
        ) : null}
        <p className="text-xs text-muted-foreground">Reference: {state.code}</p>
      </CardContent>
    </Card>
  );
}

/**
 * Keyset pagination. Links, so Tab and Enter work without any script, and a disabled direction is a
 * span rather than a dead link. There is no "last page" and no total: an opaque keyset cursor knows
 * neither, and inventing a count would mean counting the whole table on every page view.
 */
export function Pager({
  page,
  label,
  firstHref,
  previousHref,
  nextHref,
}: {
  page: number;
  label: string;
  firstHref: string;
  previousHref: string | null;
  nextHref: string | null;
}) {
  return (
    <nav aria-label={label} className="mt-3 flex items-center justify-end gap-3 text-sm">
      <span className="mr-auto text-muted-foreground">Page {page}</span>
      {page > 1 ? (
        <Link className="underline underline-offset-4" href={firstHref}>
          First
        </Link>
      ) : null}
      <PagerLink href={previousHref}>Previous</PagerLink>
      <PagerLink href={nextHref}>Next</PagerLink>
    </nav>
  );
}

function PagerLink({ href, children }: { href: string | null; children: React.ReactNode }) {
  if (href === null) {
    return (
      <span aria-disabled="true" className="text-muted-foreground">
        {children}
      </span>
    );
  }
  return (
    <Link className="underline underline-offset-4" href={href}>
      {children}
    </Link>
  );
}

export function EmptyPanel({ children }: { children: React.ReactNode }) {
  return (
    <Card>
      <CardContent className="py-10 text-center text-sm text-muted-foreground">{children}</CardContent>
    </Card>
  );
}
