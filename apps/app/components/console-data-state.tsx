import { PageHeader } from "@/components/page-header";

/**
 * The sidebar's copy for a balance that could not be read: no amount (not even a zero) and no error
 * text, because the reason for a failed read is a server log, not something to show a customer.
 */
export const BALANCE_UNAVAILABLE = "Balance unavailable";

export function ConsoleDataUnavailable({ title }: { title: string }) {
  return (
    <>
      <PageHeader title={title} subtitle="Account reporting is not available yet." />
      <p role="status" className="text-sm text-muted-foreground">
        There are no account results to display here yet. This page will show your data when reporting is ready.
      </p>
    </>
  );
}

export function ConsolePreviewNotice() {
  return (
    <p role="status" className="mb-4 rounded-md border p-3 text-sm">
      Demo data — these balances and requests are examples, not your account.
    </p>
  );
}
