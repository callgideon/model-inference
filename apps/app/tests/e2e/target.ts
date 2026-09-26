/**
 * E3A: where the browser journey may point. The runner (tests/integration/app/runner.py) sets
 * every URL; there is no default, and anything but plain http on loopback inside the task-local
 * `e4b` block (56801-56899) is refused, so a hosted App, auth server or gateway can never be the
 * target by accident. `localhost` is accepted beside 127.0.0.1 because Next's `nextUrl` rewrites
 * every loopback host to `localhost`: the App's own redirects land there, and its cookies with them. Imported by `node --test` (target.test.ts): relative `.ts` imports only (R48).
 */
export const BLOCK = { first: 56801, last: 56899 } as const;

export function loopback(name: string, env: Record<string, string | undefined>): URL {
  const raw = env[name];
  if (!raw) throw new Error(`${name} is not set: run tests/integration/app/runner.py (no default target)`);
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new Error(`${name} is not a URL`);
  }
  const port = Number(url.port);
  if (
    url.protocol !== "http:" ||
    !["127.0.0.1", "localhost"].includes(url.hostname) ||
    !(port >= BLOCK.first && port <= BLOCK.last) ||
    url.username !== "" ||
    url.password !== "" ||
    url.pathname !== "/" ||
    url.search !== ""
  ) {
    throw new Error(`${name} must be http://127.0.0.1|localhost:<port ${BLOCK.first}-${BLOCK.last}>/`);
  }
  return url;
}
