// LAB-E2E's shared node half: one suite = its backend (`<suite>/backend.py` on l4: the Lab routes and
// the Supabase stand-in, stack.py) + the Lab app as built (`next start`) pointed at it, and a cookie-jar
// "browser" that signs in through the Lab's own sign-in form and submits the pages' own forms the way a
// browser without JavaScript does (Next's progressive enhancement: the form's hidden action fields,
// multipart, 303 back). Pages are read as served HTML, never as source.
import assert from "node:assert/strict";
import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { existsSync, lstatSync, mkdirSync, writeFileSync } from "node:fs";
import { createServer } from "node:net";
import { join, resolve } from "node:path";

export const REAL = process.env.LAB_E2E_REAL === "1";
export const SKIP = !REAL && "LAB_E2E_REAL=1 (Docker, the l4 key: INFRX_D_TASK=l4)";
export const lab = resolve(import.meta.dirname, "../..");
const api = process.env.INFRX_API_DIR ?? resolve(lab, "../infrx-api");
export const PASSWORD = "lab-e2e-password"; // stack.py's, task-local
/** The Lab origin the app is configured with: https, so the production config accepts it (config.ts). */
export const ORIGIN = "https://lab.e2e.invalid";

// ------------------------------------------------------------------ HTML, as a no-JS browser reads it
const ENTITIES: Record<string, string> = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " " };
export const decode = (s: string) =>
  s.replace(/&(#x[0-9a-f]+|#\d+|\w+);/gi, (all, e: string) =>
    e[0] === "#" ? String.fromCodePoint(e[1] === "x" || e[1] === "X" ? parseInt(e.slice(2), 16) : Number(e.slice(1))) : (ENTITIES[e] ?? all));

/** The page's visible text (scripts, styles and React's text separators dropped), whitespace folded. */
export function text(html: string): string {
  const body = html.replace(/<(script|style|template)\b[\s\S]*?<\/\1>/gi, " ").replace(/<!--[\s\S]*?-->/g, "");
  return decode(body.replace(/<[^>]+>/g, " ")).replace(/\s+/g, " ").trim();
}

const attr = (tag: string, name: string): string | null => {
  const hit = new RegExp(`\\s${name.replace("$", "\\$")}="([^"]*)"`).exec(tag);
  return hit === null ? null : decode(hit[1]);
};

export type Form = { text: string; fields: [string, string][]; names: string[] };

/** Every form on the page with the values it would submit untouched (hidden inputs, defaults, the
 * selected option) and the names of every field it has. */
export function forms(html: string): Form[] {
  return [...html.matchAll(/<form\b[^>]*>([\s\S]*?)<\/form>/gi)].map(([, inner]) => {
    const fields: [string, string][] = [];
    const names: string[] = [];
    for (const [tag] of inner.matchAll(/<input\b[^>]*>/gi)) {
      const name = attr(tag, "name");
      if (name === null) continue;
      names.push(name);
      const type = attr(tag, "type");
      if (type === "checkbox" || type === "radio") continue;
      fields.push([name, attr(tag, "value") ?? ""]);
    }
    for (const [, open, body] of inner.matchAll(/(<select\b[^>]*>)([\s\S]*?)<\/select>/gi)) {
      const name = attr(open, "name");
      if (name === null) continue;
      names.push(name);
      const options = [...body.matchAll(/<option\b[^>]*>/gi)].map(([o]) => o);
      const chosen = options.find((o) => /\sselected(=""|\s|>)/.test(o)) ?? options[0];
      fields.push([name, chosen === undefined ? "" : (attr(chosen, "value") ?? "")]);
    }
    for (const [, open, body] of inner.matchAll(/(<textarea\b[^>]*>)([\s\S]*?)<\/textarea>/gi)) {
      const name = attr(open, "name");
      if (name === null) continue;
      names.push(name);
      fields.push([name, decode(body)]);
    }
    return { text: text(inner), fields, names };
  });
}

/** The one form whose text (its button, its headings) matches `which`. */
export function form(html: string, which: RegExp): Form {
  const found = forms(html).filter((f) => which.test(f.text));
  assert.equal(found.length, 1, `one form matching ${which}, found ${found.length}: ${forms(html).map((f) => f.text).join(" | ")}`);
  return found[0];
}

/** The HTML of the page's `<section aria-label="{label}">` (a record's own section), or null. */
export function section(html: string, label: string): string | null {
  const open = html.indexOf(`<section aria-label="${label}">`);
  return open === -1 ? null : html.slice(open, html.indexOf("</section>", open));
}

// ------------------------------------------------------------------ the browser
export type Page = { status: number; html: string; text: string; location: string | null };

export class Browser {
  cookies = new Map<string, string>();
  base: string;
  constructor(base: string) {
    this.base = base;
  }

  private remember(response: Response) {
    for (const line of response.headers.getSetCookie()) {
      const [pair] = line.split(";");
      const at = pair.indexOf("=");
      const [name, value] = [pair.slice(0, at).trim(), pair.slice(at + 1)];
      if (value === "" || /;\s*max-age=0\b/i.test(line) || /expires=Thu, 01 Jan 1970/i.test(line)) this.cookies.delete(name);
      else this.cookies.set(name, value);
    }
  }

  private headers(extra: Record<string, string> = {}) {
    return { cookie: [...this.cookies].map(([k, v]) => `${k}=${v}`).join("; "), ...extra };
  }

  async get(path: string): Promise<Page> {
    const response = await fetch(this.base + path, { headers: this.headers(), redirect: "manual" });
    this.remember(response);
    const html = await response.text();
    return { status: response.status, html, text: text(html), location: response.headers.get("location") };
  }

  /** Submit `f` from the page at `path` as a browser without JavaScript would, `values` filling its fields. */
  async submit(path: string, f: Form, values: Record<string, string> = {}): Promise<Page> {
    for (const name of Object.keys(values)) assert.ok(f.names.includes(name), `the form has no field ${name}: ${f.names.join(", ")}`);
    const body = new FormData();
    for (const [name, value] of f.fields) if (!(name in values)) body.append(name, value);
    for (const [name, value] of Object.entries(values)) body.append(name, value);
    const response = await fetch(this.base + path, {
      method: "POST", body, redirect: "manual",
      headers: this.headers({ origin: this.base, accept: "text/html" }),
    });
    this.remember(response);
    const html = await response.text();
    return { status: response.status, html, text: text(html), location: response.headers.get("location") };
  }

  /** Sign in through the Lab's own sign-in form; the session cookie is what the Lab's client set. */
  async signIn(email: string, password = PASSWORD): Promise<Page> {
    const home = await this.get("/");
    return this.submit("/", form(home.html, /Sign in/i), { email, password });
  }
}

// ------------------------------------------------------------------ the stack
const freePort = () =>
  new Promise<number>((done) => {
    const s = createServer().listen(0, "127.0.0.1", () => {
      const { port } = s.address() as { port: number };
      s.close(() => done(port));
    });
  });

function ready(child: ChildProcess, what: string, match: RegExp): Promise<RegExpMatchArray> {
  return new Promise((done, failed) => {
    let out = "";
    child.stdout!.on("data", (chunk) => {
      out += chunk;
      const hit = out.match(match);
      if (hit) done(hit);
    });
    child.on("exit", (code) => failed(new Error(`${what} exited ${code}: ${out.slice(-2000)}`)));
  });
}

export type Stack<W> = { api: string; web: string; world: W; browser: () => Browser; stop: () => void };

/** The suite's backend and the Lab app pointed at it. The app is built first unless LAB_E2E_BUILT=1
 * (`make lab-e2e` builds once for the four suites); a copy without a build (the mutant runner) builds. */
export async function stack<W>(suite: string, env: Record<string, string> = {}): Promise<Stack<W>> {
  if (process.env.LAB_E2E_BUILT !== "1" || !existsSync(join(lab, ".next", "BUILD_ID"))) {
    // A copy whose node_modules is a link to the checkout's (the mutant runner's) builds with webpack:
    // Turbopack refuses a node_modules outside its root.
    const bundler = lstatSync(join(lab, "node_modules")).isSymbolicLink() ? ["--webpack"] : [];
    const built = spawnSync(process.execPath, [join(lab, "node_modules/next/dist/bin/next"), "build", ...bundler], { cwd: lab, encoding: "utf8", env: { ...process.env, NODE_ENV: "production" } });
    assert.equal(built.status, 0, `next build: ${built.stdout.slice(-1500)}${built.stderr.slice(-1500)}`);
  }
  // INFRX_PYTHON: an interpreter already on the API's environment (a gate runner's mutant copy, which has
  // no uv on its PATH and imports its own mutated package from INFRX_API_DIR).
  const script = join(lab, "tests/e2e", suite, "backend.py");
  const python = process.env.INFRX_PYTHON;
  const backend = spawn(python ?? "uv", python ? [script] : ["run", "--frozen", "--project", api, "python", script], {
    cwd: lab, stdio: ["ignore", "pipe", "inherit"], env: { ...process.env, INFRX_API_DIR: api },
  });
  const stops: (() => void)[] = [() => backend.kill("SIGINT")];
  const stop = () => stops.forEach((s) => s());
  try {
    const [, port, world] = await ready(backend, "backend", /READY (\d+) (.*)\n/);
    const apiUrl = `http://127.0.0.1:${port}`;
    const webPort = await freePort();
    const web = spawn(process.execPath, [join(lab, "node_modules/next/dist/bin/next"), "start", "-H", "127.0.0.1", "-p", String(webPort)], {
      cwd: lab, stdio: ["ignore", "pipe", "inherit"],
      env: {
        ...process.env, NODE_ENV: "production", NEXT_PUBLIC_LAB_URL: ORIGIN,
        LAB_API_URL: apiUrl, // AP-09: every family, the sign-in and the memberships
        ...env,
      },
    });
    stops.push(() => web.kill("SIGINT"));
    await ready(web, "next start", /Ready in|started server/i);
    const webUrl = `http://127.0.0.1:${webPort}`;
    return { api: apiUrl, web: webUrl, world: JSON.parse(world) as W, browser: () => new Browser(webUrl), stop };
  } catch (error) {
    stop();
    throw error;
  }
}

/** A test-only door on the backend (`/_test/*`): the operator, a worker, the grantor. */
export async function door(api: string, path: string, body: unknown = {}): Promise<Record<string, unknown>> {
  const r = await fetch(`${api}/_test/${path}`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  assert.equal(r.status, 200, `${path}: ${await r.clone().text()}`);
  return r.json();
}

/** LAB_E2E_OUT=<dir>: the suite's own record of what it ran over (the gate runners read it). */
export function record(suite: string, value: Record<string, unknown>) {
  const out = process.env.LAB_E2E_OUT;
  if (!out) return;
  mkdirSync(out, { recursive: true });
  writeFileSync(join(out, `${suite}.json`), JSON.stringify({ suite, ...value }, null, 2));
}
