const ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
const KEY_PREFIX = "sk-infrx-";
const BODY_LENGTH = 40;
/** `sk-infrx-` + 8 body chars, stored in clear for display (spec §4). */
const PREFIX_LENGTH = KEY_PREFIX.length + 8;

/** `sk-infrx-` + 40 unbiased base62 chars from the CSPRNG. */
export function generateKey(): string {
  let out = "";
  const buf = new Uint8Array(BODY_LENGTH);
  while (out.length < BODY_LENGTH) {
    crypto.getRandomValues(buf);
    for (const b of buf) {
      if (b >= 248) continue; // 248 = 4 * 62: reject the tail so every char is equally likely
      out += ALPHABET[b % 62];
      if (out.length === BODY_LENGTH) break;
    }
  }
  return KEY_PREFIX + out;
}

export function keyPrefix(key: string): string {
  return key.slice(0, PREFIX_LENGTH);
}

/** SHA-256 hex, the same digest the gateway computes on the bearer token. */
export async function hashKey(key: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(key));
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}
