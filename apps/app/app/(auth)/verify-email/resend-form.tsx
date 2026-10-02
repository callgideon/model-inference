import { RESEND_UNAVAILABLE } from "../flow";

/**
 * Resending the verification email. The auth facade has no resend route yet (WR-AP09-RESEND), so
 * this says so instead of offering a button that sends nothing; the address is never echoed.
 */
export function ResendForm() {
  return (
    <p role="status" className="text-center text-xs text-muted-foreground">
      {RESEND_UNAVAILABLE}
    </p>
  );
}
