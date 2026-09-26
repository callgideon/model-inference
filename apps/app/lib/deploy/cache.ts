/**
 * I2A: who may cache what. Private by default: every response except Next's static assets is sent
 * `private, no-store`, so a new route is private without anyone remembering to say so. Next itself
 * rewrites Cache-Control on rendered pages — dynamic pages get its own private no-store, static
 * prerenders get a shared-cache s-maxage — so the page-level guarantee is that no private page is
 * prerendered (tests/i2a I2A-BUILT-02); this rule covers route handlers, redirects and the rest.
 *
 * Imported by next.config.ts and by `node --test`: no imports.
 */

export const PRIVATE_NO_STORE = "private, no-store, max-age=0";
export const PRIVATE_HEADERS = Object.freeze({ "Cache-Control": PRIVATE_NO_STORE });

/**
 * Pages that serve nobody's data and may be prerendered and cached. The console's Docs and Models
 * pages are NOT here: their content is public, but they render inside the console shell, which
 * shows the signed-in person's balance.
 */
export const PUBLIC_PAGES: readonly string[] = Object.freeze(["/", "/login", "/signup", "/verify-email", "/forgot-password", "/update-password"]);

export const isPrivatePath = (path: string) => !PUBLIC_PAGES.includes(path);

/** The next.config.ts `headers()` rules. */
export function cacheHeaders() {
  return [
    {
      source: "/:path((?!_next/static/|_next/image|favicon\\.ico).*)",
      headers: [{ key: "Cache-Control", value: PRIVATE_NO_STORE }],
    },
  ];
}
