// AP-09: the generated client's answers as the console's own `Result`s (lib/contracts/types.ts).
// Pure, relative imports only, so node --test loads it (R48).
//
// What crosses: the R270 error envelope's code when the contract knows it (else "not now"), with
// the App's fixed text - never the server's message; a transport failure is
// `dependency_unavailable`; an answer this cannot read exactly is `internal_error`. Never a zero, a
// fixture or an empty success.
import type { ApiError, Result as ApiResult } from "@infrx/api-client/transport";
import { ERROR_CODES, type ErrorCode, type Result } from "../contracts/types.ts";
import { parseCredit, parseUsd, type Credit, type Usd } from "../contracts/v2/money-units.ts";

export const UNAVAILABLE = "Account data is unavailable right now.";
export const INEXACT = "Account data could not be read exactly.";

/** A wire value that is not what the contract says. Thrown inside a mapper, caught by `read`. */
export class Malformed extends Error {}

export function fail<T>(code: ErrorCode, message: string): Result<T> {
  return { ok: false, error: { code, message } };
}

/** The contract code an API failure stands for. 401 is a session that ended: `forbidden`. */
export function codeOf(error: ApiError): ErrorCode {
  if (error.kind === "unavailable") return error.reason === "malformed" ? "internal_error" : "dependency_unavailable";
  if (error.status === 401) return "forbidden";
  const code = error.kind === "refusal" ? error.reason : error.code;
  return code !== null && (ERROR_CODES as readonly string[]).includes(code) ? (code as ErrorCode) : "dependency_unavailable";
}

export function failure<T>(error: ApiError, message = UNAVAILABLE): Result<T> {
  const code = codeOf(error);
  return fail(code, code === "internal_error" ? INEXACT : message);
}

/** One call's answer, mapped. A mapper that throws (a malformed document) is `internal_error`. */
export async function read<W, T>(call: Promise<ApiResult<W>>, map: (wire: W) => T, message = UNAVAILABLE): Promise<Result<T>> {
  let answer: ApiResult<W>;
  try {
    answer = await call;
  } catch {
    return fail("dependency_unavailable", message);
  }
  if (!answer.ok) return failure(answer.error, message);
  try {
    return { ok: true, value: map(answer.data) };
  } catch {
    return fail("internal_error", INEXACT);
  }
}

/** UTC instants in the one comparable form the console DTOs carry (`YYYY-MM-DDTHH:MM:SS.ffffffZ`). */
const INSTANT = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,6}))?(?:Z|\+00:00)$/;

export function instant(value: unknown): string {
  const match = typeof value === "string" ? INSTANT.exec(value) : null;
  if (match === null) throw new Malformed("not a UTC instant");
  return `${match[1]}.${(match[2] ?? "").padEnd(6, "0")}Z`;
}

export function optionalInstant(value: unknown): string | null {
  return value === null || value === undefined ? null : instant(value);
}

type Money = { amount: string; unit: string };

/** A CREDIT amount, refusing any other unit: money is never relabelled. */
export function credit(money: Money): Credit {
  if (money.unit !== "CREDIT") throw new Malformed("not CREDIT");
  return parseCredit(money.amount);
}

export function usd(money: Money): Usd {
  if (money.unit !== "USD") throw new Malformed("not USD");
  return parseUsd(money.amount);
}

/** An amount in the unit its owner states (a request's regime), refusing any other. */
export function amountIn(money: Money | null, unit: "CREDIT" | "USD"): string | null {
  if (money === null) return null;
  return unit === "CREDIT" ? credit(money) : usd(money);
}

/**
 * One answer per GET per request (as U1R WR-6's one `getUser()`): the layout's account, the
 * sidebar's balance and the page's reads agree on one state, and an outage is one outage.
 */
export function onceGets(send: typeof fetch): typeof fetch {
  const seen = new Map<string, Promise<Response>>();
  return ((input: string | URL | Request, init?: RequestInit) => {
    if ((init?.method ?? "GET").toUpperCase() !== "GET") return send(input, init);
    const key = String(input);
    let answer = seen.get(key);
    if (answer === undefined) seen.set(key, (answer = send(input, init)));
    return answer.then((response) => response.clone());
  }) as typeof fetch;
}
