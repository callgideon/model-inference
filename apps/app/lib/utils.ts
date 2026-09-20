export { cn } from "cn";

/**
 * A ?next= target we are willing to redirect to: same-site paths only, so an
 * attacker-supplied `https://evil.example` (or protocol-relative `//evil`) cannot
 * turn the login round-trip into an open redirect.
 */
export function safeNext(next: string | null | undefined, fallback = "/models") {
  return next && next.startsWith("/") && !next.startsWith("//") ? next : fallback;
}
