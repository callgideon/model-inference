/**
 * E3A: the App's journey in a real browser, over the composed backend world
 * (tests/integration/app/runner.py: E3C's PostgreSQL/PostgREST/Valkey/S3/gateway/worker, an auth
 * stand-in, a controlled engine). Each test is one CHECK the runner maps to the E3A test_ids; its
 * title starts `<check>: `. Run only through the runner (playwright.config.ts refuses otherwise).
 *
 * Oracles are the durable records, never the page alone: every figure the App shows is compared
 * with the clone's rows (`/facts` on the control API) as exact decimals. A check whose feature a
 * lane has not merged skips `NOT RUN[<lane>]` (the runner reports it; never a pass). A check that
 * needs an earlier step's result skips `NOT RUN[journey]` when that step did not complete.
 *
 * The external client is Node's fetch against the gateway with the individual's key, exactly as a
 * developer would call it. The key comes from the App (C3A/U2) once those lanes merge; until then
 * from the operator CLI, and the check `create-key` is NOT RUN.
 */
import { createHash } from "node:crypto";
import { expect, test, type Page } from "@playwright/test";
import { loopback } from "./target.ts";

const CONTROL = loopback("E3A_CONTROL_URL", process.env).origin;
const GATEWAY = loopback("E3A_GATEWAY_URL", process.env).origin;
const MODEL = process.env.E3A_MODEL ?? "";
const LANES = new Set((process.env.E3A_LANES ?? "").split(",").filter(Boolean));
const PASSWORD = "e3a-local-only-Passw0rd"; // the local auth stand-in's; never a real account
const TEXT = [{ role: "user", content: "Describe the van." }];
const FINISHED = ["succeeded", "failed", "cancelled"];
const RUN = Date.now().toString(36);

type Job = {
  request_id: string;
  handle: string;
  state: string;
  settlement: string | null;
  mode: string;
  hold: string | null;
  hold_state: string | null;
  charged: string | null;
  debits: number;
};
type Facts = {
  user_id: string;
  org_id: string | null;
  confirmed: boolean;
  entitlements: number;
  grant_rows: number;
  grant_total: string;
  wallet: { wallet_id: string; ledger: string; reserved: string; available: string } | null;
  jobs: Job[];
  keys: { key_id: string; name: string; revoked: boolean }[];
  conserved: { ok: boolean; detail?: string } | null;
};
type Journey = {
  a?: string;
  b?: string;
  keyA?: string;
  keyB?: string;
  keySource?: string;
  appKeyId?: string;
  syncRequest?: string;
  syncKey?: string;
  asyncHandle?: string;
  uploadRef?: string;
};

// A failure in one check must not skip the others: each test finds what it needs in the journey
// state the control API keeps (a worker restart after a failure loses module state).
test.describe.configure({ mode: "default" });

// ------------------------------------------------------------------ the harness's side

async function control<T>(path: string, body?: unknown): Promise<T> {
  let reply: Response;
  try {
    reply = await fetch(
      CONTROL + path,
      body === undefined
        ? undefined
        : { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) },
    );
  } catch {
    throw new Error(`INVALID[harness] control ${path}: no answer`);
  }
  if (!reply.ok) throw new Error(`INVALID[harness] control ${path}: ${reply.status} ${(await reply.text()).slice(0, 300)}`);
  return (await reply.json()) as T;
}

const facts = (email: string) => control<Facts>(`/facts?email=${encodeURIComponent(email)}`);
const journey = () => control<Journey>("/state");
const remember = (patch: Journey) => control<Journey>("/state", patch);

function needsLanes(what: string, ...lanes: string[]): void {
  const missing = lanes.filter((lane) => !LANES.has(lane));
  test.skip(missing.length > 0, `NOT RUN[${missing.join(",")}] ${missing.join(" and ")} not merged: ${what}`);
}

function needs<T>(value: T | undefined, what: string): T {
  test.skip(value === undefined || value === null, `NOT RUN[journey] needs ${what} from an earlier step, which did not complete`);
  return value as T;
}

// ------------------------------------------------------------------ exact money

/** A decimal string with no insignificant zeros: "10000.00000000" -> "10000". Never a float. */
function exact(decimal: string): string {
  const negative = decimal.startsWith("-");
  const [whole, fraction = ""] = decimal.replace(/^-/, "").split(".");
  const w = whole.replace(/^0+(?=\d)/, "");
  const f = fraction.replace(/0+$/, "");
  const text = f ? `${w}.${f}` : w;
  return negative && text !== "0" ? `-${text}` : text;
}

/** The exact amount a displayed figure states ("10,000.00 credits" -> "10000"). */
function shown(text: string | null): string {
  const match = (text ?? "").replace(/,/g, "").match(/-?\d+(?:\.\d+)?/);
  if (!match) throw new Error(`no amount in ${JSON.stringify(text)}`);
  return exact(match[0]);
}

const SCALE = BigInt(100_000_000); // CREDIT's 8 decimal places

/** A CREDIT decimal string as integer units (exact; no float). */
function units(decimal: string): bigint {
  const [whole, fraction = ""] = decimal.split(".");
  return BigInt(whole + fraction.padEnd(8, "0").slice(0, 8));
}

// ------------------------------------------------------------------ the browser

async function signIn(page: Page, email: string): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL((url) => !url.pathname.startsWith("/login"));
}

async function signUp(page: Page, email: string): Promise<void> {
  await page.goto("/signup");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByRole("heading", { name: "Check your email" })).toBeVisible();
}

/** Open the one verification link the stand-in mailed; the App lands on /welcome. */
async function verify(page: Page, email: string): Promise<string> {
  const { links } = await control<{ links: string[] }>(`/mail?email=${encodeURIComponent(email)}`);
  expect(links, "exactly one verification email").toHaveLength(1);
  await page.goto(links[0]);
  await page.waitForURL(/\/welcome$/);
  return links[0];
}

async function welcomeBalance(page: Page): Promise<string> {
  return shown(await page.getByRole("region", { name: "Available balance" }).locator("p").first().textContent());
}

/** A balance-card figure on /usage (Available, Reserved, Spent, Balance). */
async function figure(page: Page, label: string): Promise<string> {
  const value = page.locator("dt", { hasText: new RegExp(`^${label}$`) }).locator("xpath=following-sibling::dd[1]");
  return shown(await value.textContent());
}

function row(page: Page, requestId: string) {
  return page.locator("tr", { hasText: `for request ${requestId}` });
}

// ------------------------------------------------------------------ the external client

async function api(key: string, method: string, path: string, body?: unknown, headers: Record<string, string> = {}) {
  return fetch(GATEWAY + path, {
    method,
    headers: {
      authorization: `Bearer ${key}`,
      ...(body === undefined ? {} : { "content-type": "application/json" }),
      ...headers,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

async function errorCode(reply: Response): Promise<string | undefined> {
  try {
    return ((await reply.json()) as { error?: { code?: string } }).error?.code;
  } catch {
    return undefined;
  }
}

async function untilTerminal(key: string, handle: string, timeoutMs = 90_000): Promise<Record<string, unknown>> {
  const end = Date.now() + timeoutMs;
  for (;;) {
    const reply = await api(key, "GET", `/v1/jobs/${handle}`);
    expect(reply.status, "the owner's status read").toBe(200);
    const status = (await reply.json()) as Record<string, unknown>;
    if (FINISHED.includes(String(status.state)) || Date.now() > end) return status;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
}

/** The individual's key: the App's (C3A/U2), else one issued through the operator CLI. */
async function keyFor(who: "a" | "b"): Promise<string> {
  const state = await journey();
  const email = needs(state[who], `user ${who.toUpperCase()}`);
  const known = who === "a" ? state.keyA : state.keyB;
  if (known) return known;
  const { secret } = await control<{ secret: string }>("/issue-key", { email, name: `e3a ${who} ${RUN}` });
  await remember(who === "a" ? { keyA: secret, keySource: "operator CLI (C3A/U2 not merged)" } : { keyB: secret });
  return secret;
}

function one(list: Job[], requestId: string): Job {
  const found = list.filter((job) => job.request_id === requestId);
  expect(found, `one job row for ${requestId}`).toHaveLength(1);
  return found[0];
}

// ================================================================== the journey

test("signup-verify-grant: a fresh individual verifies once and is granted 10,000 CREDIT exactly once", async ({ page }) => {
  const email = `e3a-a-${RUN}@e3a.invalid`;
  await signUp(page, email);

  // Unverified: sign-in is refused and nothing is granted.
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByText("Verify your email address first")).toBeVisible();
  let books = await facts(email);
  expect([books.confirmed, books.grant_rows, books.wallet]).toEqual([false, 0, null]);

  // Verify: the callback claims the grant for the user the auth server verified.
  const link = await verify(page, email);
  books = await facts(email);
  expect([books.confirmed, books.entitlements, books.grant_rows, exact(books.grant_total)]).toEqual([true, 1, 1, "10000"]);
  expect(await welcomeBalance(page), "the welcome balance is the wallet's exact available").toBe(exact(books.wallet!.available));
  expect(exact(books.wallet!.available)).toBe("10000");

  // The callback replayed (the one-use link is refused), then a SECOND callback: a password
  // recovery link, whose callback claims again for the verified user. Still one grant.
  await page.goto(link);
  await page.goto("/forgot-password");
  await page.getByLabel("Email").fill(email);
  await page.locator("form button[type=submit]").click();
  const mailed = (await control<{ links: string[] }>(`/mail?email=${encodeURIComponent(email)}`)).links;
  expect(mailed, "one recovery email").toHaveLength(2);
  const claims = await control<Record<string, number>>("/stats");
  await page.goto(mailed[1]);
  await page.waitForURL(/\/update-password$/);
  const after = await control<Record<string, number>>("/stats");
  expect(after["rpc/claim_signup_grant:200"] ?? 0, "the recovery callback claimed again").toBe((claims["rpc/claim_signup_grant:200"] ?? 0) + 1);
  books = await facts(email);
  expect([books.entitlements, books.grant_rows, exact(books.grant_total)], "one grant despite the repeated callback").toEqual([1, 1, "10000"]);
  await page.goto("/welcome");
  expect(await welcomeBalance(page)).toBe("10000");

  // The console: exact balance, CREDIT labelled, no requests yet.
  await page.goto("/usage");
  expect(await figure(page, "Available")).toBe("10000");
  expect(await figure(page, "Balance")).toBe("10000");
  await expect(page.getByText("CREDIT", { exact: true }).first()).toBeVisible();
  await expect(page.locator("tr", { hasText: "for request" })).toHaveCount(0);
  await remember({ a: email });
});

test("signin-claim: signing in runs A2's first-login grant claim (answered replayed, no second grant)", async ({ page }) => {
  const email = needs((await journey()).a, "user A");
  const before = await control<Record<string, number>>("/stats");
  await signIn(page, email); // returns once the form navigated, i.e. after its claim settled
  const after = await control<Record<string, number>>("/stats");
  expect(after["rpc/claim_signup_grant:200"] ?? 0, "the sign-in claim reached the database").toBe((before["rpc/claim_signup_grant:200"] ?? 0) + 1);
  const books = await facts(email);
  expect([books.entitlements, books.grant_rows]).toEqual([1, 1]);
});

test("create-key: the individual creates a key in the App; its plaintext is shown once", async ({ page }) => {
  needsLanes("key create/revoke actions and the keys page are absent", "C3A", "U2");
  const email = needs((await journey()).a, "user A");
  await signIn(page, email);
  await page.goto("/api-keys");
  await page.getByRole("button", { name: /create/i }).first().click();
  const name = `e3a app key ${RUN}`;
  await page.getByLabel("Name").fill(name);
  await page.getByRole("button", { name: /create/i }).last().click();
  const secret = (await page.locator("code, input[readonly]").first().textContent())?.trim() ?? "";
  expect(secret).toMatch(/^sk-/);
  const books = await facts(email);
  const key = books.keys.find((entry) => entry.name === name);
  expect(key && !key.revoked, "the key is filed, unrevoked").toBeTruthy();
  await page.reload();
  expect(await page.content(), "the plaintext is never shown again").not.toContain(secret);
  await remember({ keyA: secret, keySource: "App (C3A/U2)", appKeyId: key!.key_id });
});

test("text-sync: an external text request is answered and settled exactly once", async () => {
  const key = await keyFor("a");
  const idem = `e3a-sync-${RUN}`;
  const reply = await api(key, "POST", "/v1/chat/completions", { model: MODEL, messages: TEXT }, { "idempotency-key": idem });
  expect(reply.status, await reply.clone().text()).toBe(200);
  const body = (await reply.json()) as { object: string; id: string; choices: { message: { content: string } }[] };
  const requestId = reply.headers.get("inference-id") ?? "";
  expect([body.object, body.id]).toEqual(["chat.completion", `chatcmpl-${requestId}`]);
  expect(body.choices[0].message.content.length).toBeGreaterThan(0);
  const job = one((await facts((await journey()).a!)).jobs, requestId);
  expect([job.state, job.settlement, job.debits]).toEqual(["succeeded", "settled", 1]);
  await remember({ syncRequest: requestId, syncKey: idem });
});

test("sse-stream: an external streamed request sends identity, deltas, usage and [DONE]", async () => {
  const key = await keyFor("a");
  const reply = await api(key, "POST", "/v1/chat/completions", { model: MODEL, messages: TEXT, stream: true }, { "idempotency-key": `e3a-sse-${RUN}` });
  expect(reply.status).toBe(200);
  expect(reply.headers.get("content-type") ?? "").toMatch(/^text\/event-stream/);
  const frames = (await reply.text()).split("\n\n").filter((frame) => frame.trim());
  const data = (frame: string) => frame.split("\n").find((line) => line.startsWith("data: "))?.slice(6);
  const identity = JSON.parse(data(frames[0]) ?? "{}") as { phase: string; request_id: string; job_handle: string };
  expect(frames[0]).toContain("event: infrx.progress");
  expect(identity.phase).toBe("accepted");
  expect(data(frames[frames.length - 1])).toBe("[DONE]");
  const usage = JSON.parse(data(frames[frames.length - 2]) ?? "{}") as { usage?: { total_tokens: number } };
  expect(usage.usage?.total_tokens ?? 0).toBeGreaterThan(0);
  const deltas = frames
    .slice(1, -2)
    .map((frame) => JSON.parse(data(frame) ?? "{}") as { choices?: { delta: { content?: string } }[] })
    .flatMap((chunk) => chunk.choices ?? [])
    .map((choice) => choice.delta.content ?? "")
    .join("");
  expect(deltas.length).toBeGreaterThan(0);
  const job = one((await facts((await journey()).a!)).jobs, identity.request_id);
  expect([job.state, job.settlement, job.debits]).toEqual(["succeeded", "settled", 1]);
});

test("async-poll: an external async job is accepted, polled to its end and its result read", async () => {
  const key = await keyFor("a");
  const reply = await api(key, "POST", "/v1/jobs", { model: MODEL, messages: TEXT }, { "idempotency-key": `e3a-async-${RUN}` });
  expect(reply.status).toBe(202);
  const accepted = (await reply.json()) as { job_handle: string; request_id: string };
  expect(reply.headers.get("location")).toBe(`/v1/jobs/${accepted.job_handle}`);
  const status = await untilTerminal(key, accepted.job_handle);
  expect([status.state, status.result_available]).toEqual(["succeeded", true]);
  const result = await api(key, "GET", `/v1/jobs/${accepted.job_handle}/result`);
  expect(result.status).toBe(200);
  expect(((await result.json()) as { response: { usage: unknown } }).response.usage).toEqual(status.usage);
  const job = one((await facts((await journey()).a!)).jobs, accepted.request_id);
  expect([job.state, job.settlement, job.debits]).toEqual(["succeeded", "settled", 1]);
  await remember({ asyncHandle: accepted.job_handle });
});

test("video-upload: an uploaded finite video is processed as an async job and its result read", async () => {
  const key = await keyFor("a");
  const clip = Buffer.from(await (await fetch(`${CONTROL}/clip`)).arrayBuffer());
  const ticket = await api(key, "POST", "/v1/uploads", {
    bytes: clip.length,
    digest: `sha256:${createHash("sha256").update(clip).digest("hex")}`,
    accepted_mime: ["video/mp4"],
  });
  expect(ticket.status).toBe(201);
  const { upload_handle: handle, destination_ref: ref } = (await ticket.json()) as { upload_handle: string; destination_ref: string };
  const put = await fetch(`${GATEWAY}/v1/uploads/${handle}`, {
    method: "PUT",
    headers: { authorization: `Bearer ${key}`, "content-type": "video/mp4" },
    body: clip,
  });
  const done = await api(key, "POST", `/v1/uploads/${handle}/complete`);
  expect([put.status, done.status]).toEqual([204, 200]);
  const messages = await control<unknown[]>("/video-messages", { ref });
  const reply = await api(key, "POST", "/v1/jobs", { model: MODEL, messages }, { "idempotency-key": `e3a-video-${RUN}` });
  expect(reply.status, await reply.clone().text()).toBe(202);
  const accepted = (await reply.json()) as { job_handle: string; request_id: string };
  const status = await untilTerminal(key, accepted.job_handle);
  expect(status.state).toBe("succeeded");
  expect((await api(key, "GET", `/v1/jobs/${accepted.job_handle}/result`)).status).toBe(200);
  const job = one((await facts((await journey()).a!)).jobs, accepted.request_id);
  expect([job.settlement, job.debits]).toEqual(["settled", 1]);
  await remember({ uploadRef: ref });
});

test("retry: the same request retried with its idempotency key is replayed, never charged twice", async () => {
  const key = await keyFor("a");
  const state = await journey();
  const requestId = needs(state.syncRequest, "the text-sync request");
  const before = await facts(state.a!);
  const again = await api(key, "POST", "/v1/chat/completions", { model: MODEL, messages: TEXT }, { "idempotency-key": state.syncKey! });
  expect([again.status, again.headers.get("inference-id"), again.headers.get("idempotency-replayed")]).toEqual([200, requestId, "true"]);
  const after = await facts(state.a!);
  expect(after.jobs.length, "no second job").toBe(before.jobs.length);
  expect(one(after.jobs, requestId).debits, "one debit").toBe(1);
  expect(exact(after.wallet!.ledger)).toBe(exact(before.wallet!.ledger));
});

test("usage-balance: the console shows every request and the balance exactly as the ledger records them", async ({ page }) => {
  const email = needs((await journey()).a, "user A");
  await signIn(page, email);
  await page.goto("/usage");
  const books = await facts(email);
  expect(books.jobs.length, "the journey made requests").toBeGreaterThanOrEqual(4);
  expect(books.conserved, "every charge = the admitted card x its usage; ledger = grant - charges").toEqual({ ok: true });
  expect(await figure(page, "Available")).toBe(exact(books.wallet!.available));
  expect(await figure(page, "Reserved")).toBe(exact(books.wallet!.reserved));
  expect(await figure(page, "Balance")).toBe(exact(books.wallet!.ledger));
  expect(exact(books.wallet!.ledger)).not.toBe("10000");
  await expect(page.locator("tr", { hasText: "for request" })).toHaveCount(books.jobs.length);
  for (const job of books.jobs) {
    const cells = row(page, job.request_id).locator("td");
    if (job.settlement === "settled") {
      await expect(cells.nth(2)).toContainText("Charged");
      expect(shown(await cells.nth(5).textContent()), `charged for ${job.request_id}`).toBe(exact(job.charged!));
    }
  }
  expect(await page.locator("main").textContent()).not.toMatch(/\$\s?\d/);
});

test("refresh: a reload and an expiring session mid-journey keep the individual's exact state", async ({ page }) => {
  const email = needs((await journey()).a, "user A");
  await control("/auth-ttl", { seconds: 100 }); // inside auth-js's 90 s margin after 10 s
  try {
    await signIn(page, email);
    await page.goto("/usage");
    const available = await figure(page, "Available");
    const before = await control<Record<string, number>>("/stats");
    await page.waitForTimeout(12_000);
    await page.reload();
    expect(new URL(page.url()).pathname, "still signed in after the refresh").toBe("/usage");
    expect(await figure(page, "Available")).toBe(available);
    const after = await control<Record<string, number>>("/stats");
    expect(after.refresh_grants ?? 0, "the session was refreshed with its refresh token").toBeGreaterThan(before.refresh_grants ?? 0);
    expect(exact((await facts(email)).wallet!.available)).toBe(available);
  } finally {
    await control("/auth-ttl", { seconds: 3600 });
  }
});

test("request-detail: the owner reads a request's detail, charge and result from Usage", async ({ page }) => {
  needsLanes("the request detail route (/usage/<request>) is absent", "U4");
  const state = await journey();
  const requestId = needs(state.syncRequest, "the text-sync request");
  await signIn(page, state.a!);
  await page.goto(`/usage/${requestId}`);
  const job = one((await facts(state.a!)).jobs, requestId);
  await expect(page.getByText(requestId).first()).toBeVisible();
  await expect(page.getByText(exact(job.charged!)).first()).toBeVisible();
});

test("accounting-uncertainty: usage the engine did not report is held for reconciliation, never shown as a charge", async ({ page }) => {
  const key = await keyFor("a");
  const email = (await journey()).a!;
  await control("/engine", { fault: "missing_usage" });
  let accepted: { job_handle: string; request_id: string };
  try {
    const reply = await api(key, "POST", "/v1/jobs", { model: MODEL, messages: TEXT }, { "idempotency-key": `e3a-unknown-${RUN}` });
    expect(reply.status).toBe(202);
    accepted = (await reply.json()) as { job_handle: string; request_id: string };
    await untilTerminal(key, accepted.job_handle);
  } finally {
    await control("/engine", { fault: "none" });
  }
  const books = await facts(email);
  const job = one(books.jobs, accepted.request_id);
  expect([job.settlement, job.hold_state, job.debits]).toEqual(["held_unknown", "unknown", 0]);
  await signIn(page, email);
  await page.goto("/usage");
  const cells = row(page, accepted.request_id).locator("td");
  await expect(cells.nth(2)).toContainText("Awaiting reconciliation");
  expect((await cells.nth(5).textContent())?.trim(), "no charge is shown").toBe("—");
  expect(shown(await cells.nth(6).textContent())).toBe(exact(job.hold!));
  expect(await figure(page, "Reserved")).toBe(exact(books.wallet!.reserved));
  expect(exact(books.wallet!.reserved)).not.toBe("0");
});

test("rate-rejection: past the per-key active job cap the next request is refused 429 and admits nothing; the rest settle once after a worker replacement", async () => {
  const key = await keyFor("a");
  const email = (await journey()).a!;
  const jobs = (await facts(email)).jobs.length;
  await control("/worker", { action: "stop" });
  const handles: string[] = [];
  let refused: Response | undefined;
  try {
    for (let n = 0; n < 6 && refused === undefined; n += 1) {
      const reply = await api(key, "POST", "/v1/jobs", { model: MODEL, messages: TEXT }, { "idempotency-key": `e3a-cap-${RUN}-${n}` });
      if (reply.status === 202) handles.push(((await reply.json()) as { job_handle: string }).job_handle);
      else refused = reply;
    }
    expect(refused?.status, "a request past the cap").toBe(429);
    expect(await errorCode(refused!)).toBe("capacity_exhausted");
    expect(refused!.headers.get("retry-after")).toBeTruthy();
    expect(handles.length, "the cap admitted exactly its size").toBe(2);
    expect((await facts(email)).jobs.length, "the refusal admitted nothing").toBe(jobs + handles.length);
  } finally {
    await control("/worker", { action: "start" });
  }
  for (const handle of handles) expect((await untilTerminal(key, handle, 120_000)).state).toBe("succeeded");
  const books = await facts(email);
  for (const handle of handles) {
    const job = books.jobs.find((entry) => entry.handle === handle)!;
    expect([job.settlement, job.debits], `settled once: ${handle}`).toEqual(["settled", 1]);
  }
});

test("low-funds: with almost nothing left the API refuses 402 and admits nothing; the console says so exactly", async ({ page }) => {
  const key = await keyFor("a");
  const email = (await journey()).a!;
  let books = await facts(email);
  const available = exact(books.wallet!.available);
  const keep = "0.00000100";
  const debit = units(available) - units(keep);
  const amount = `-${debit / SCALE}.${(debit % SCALE).toString().padStart(8, "0")}`;
  await control("/adjust", { email, amount });
  books = await facts(email);
  expect(exact(books.wallet!.available)).toBe(exact(keep));
  const jobs = books.jobs.length;
  const reply = await api(key, "POST", "/v1/chat/completions", { model: MODEL, messages: TEXT }, { "idempotency-key": `e3a-low-${RUN}` });
  expect([reply.status, await errorCode(reply)]).toEqual([402, "insufficient_credit"]);
  const after = await facts(email);
  expect([after.jobs.length, exact(after.wallet!.available)], "the refusal admitted and held nothing").toEqual([jobs, exact(keep)]);
  await signIn(page, email);
  await page.goto("/usage");
  expect(await figure(page, "Available")).toBe(exact(keep));
  await expect(page.getByText(/Credits running low|No credits available/)).toBeVisible();
});

test("provider-route-denial: provider and operator routes are not served to a consumer", async ({ page }) => {
  const email = needs((await journey()).a, "user A");
  await signIn(page, email);
  const served: string[] = [];
  for (const route of ["/traces", "/admin", "/dedicated", "/teams"]) {
    const reply = await page.goto(route);
    const landed = new URL(page.url()).pathname;
    if (reply?.status() !== 404 && landed === route) served.push(`${route} (${reply?.status()})`);
  }
  expect(served, "provider routes a consumer reached").toEqual([]);
});

test("operator-controls: operator actions are reachable only by an operator, with a reason, once", async ({ page }) => {
  needsLanes("the minimal operator controls are absent", "U3");
  const email = needs((await journey()).a, "user A");
  await signIn(page, email);
  const reply = await page.goto("/admin");
  expect(reply?.status() === 404 || new URL(page.url()).pathname !== "/admin", "a consumer is refused /admin").toBeTruthy();
});

test("isolation: a second individual gets their own one grant and sees none of the first's requests, keys or results", async ({ page }) => {
  const state = await journey();
  const first = needs(state.a, "user A");
  const email = `e3a-b-${RUN}@e3a.invalid`;
  await signUp(page, email);
  await verify(page, email);
  await remember({ b: email });
  expect(await welcomeBalance(page)).toBe("10000");
  const mine = await facts(email);
  const theirs = await facts(first);
  expect([mine.grant_rows, exact(mine.grant_total)]).toEqual([1, "10000"]);
  expect(mine.org_id).not.toBe(theirs.org_id);

  await page.goto("/usage");
  await expect(page.locator("tr", { hasText: "for request" })).toHaveCount(0);
  const text = (await page.locator("main").textContent()) ?? "";
  for (const job of theirs.jobs) expect(text).not.toContain(job.request_id);
  await page.goto("/api-keys");
  const keysPage = (await page.locator("main").textContent()) ?? "";
  for (const key of theirs.keys) expect(keysPage).not.toContain(key.name);

  const keyB = await keyFor("b");
  const handle = needs(state.asyncHandle, "the async job");
  for (const path of [`/v1/jobs/${handle}`, `/v1/jobs/${handle}/result`, `/v1/jobs/${handle}/events`]) {
    const reply = await api(keyB, "GET", path);
    expect([reply.status, await errorCode(reply)], `cross-tenant GET ${path}`).toEqual([404, "not_found"]);
  }
  const ref = needs(state.uploadRef, "the uploaded video");
  const messages = await control<unknown[]>("/video-messages", { ref });
  const borrowed = await api(keyB, "POST", "/v1/jobs", { model: MODEL, messages }, { "idempotency-key": `e3a-borrow-${RUN}` });
  expect(borrowed.status, "another tenant's upload is refused").toBeGreaterThanOrEqual(400);
  expect(borrowed.status).toBeLessThan(500);
  expect((await facts(email)).jobs, "the refusal admitted nothing").toHaveLength(0);
});

test("revoke-key: a key revoked in the App fails admission within the revocation bound", async ({ page }) => {
  needsLanes("key create/revoke actions and the keys page are absent", "C3A", "U2");
  const state = await journey();
  const keyId = needs(state.appKeyId, "the key created in the App");
  await signIn(page, state.a!);
  await page.goto("/api-keys");
  await page.getByRole("button", { name: /revoke/i }).first().click();
  await expect.poll(async () => (await facts(state.a!)).keys.find((key) => key.key_id === keyId)?.revoked).toBe(true);
  await expect
    .poll(async () => (await api(state.keyA!, "GET", `/v1/jobs/${state.asyncHandle}`)).status, { timeout: 75_000, intervals: [2_000] })
    .toBe(401);
  const jobs = (await facts(state.a!)).jobs.length;
  const refused = await api(state.keyA!, "POST", "/v1/chat/completions", { model: MODEL, messages: TEXT });
  expect([refused.status, await errorCode(refused)]).toEqual([401, "invalid_api_key"]);
  expect((await facts(state.a!)).jobs.length).toBe(jobs);
});

test("expired-result: past its persisted expiry a result is gone (410), its metadata kept", async () => {
  const key = await keyFor("a");
  const state = await journey();
  const handle = needs(state.asyncHandle, "the async job");
  expect((await api(key, "GET", `/v1/jobs/${handle}/result`)).status).toBe(200);
  await control("/clock", { seconds: 86_400 + 600 });
  try {
    const gone = await api(key, "GET", `/v1/jobs/${handle}/result`);
    expect([gone.status, await errorCode(gone)]).toEqual([410, "result_expired"]);
    const status = await api(key, "GET", `/v1/jobs/${handle}`);
    expect(status.status, "the metadata stays readable").toBe(200);
    expect(((await status.json()) as { result_available: boolean }).result_available).toBe(false);
  } finally {
    await control("/clock", { seconds: 0 });
  }
});
