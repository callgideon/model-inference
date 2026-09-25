/**
 * A3: what the Models and Docs pages know about a model, read from the gateway's published
 * projection (G7, `GET /v1/models`) and parsed by F2C.c's `parsePublishedModel`. Nothing here
 * states a model, limit, rate or retention of its own (RV-01): every figure a page shows comes
 * from a record admission itself publishes, so a page cannot claim what the gateway refuses.
 *
 * A read that does not yield contract-valid records - no configured origin, an unreachable
 * gateway, a non-200, a body that is not the published list, one record the contract refuses -
 * is `unavailable`. It is never a fixture and never an empty catalog dressed as a real one.
 *
 * Imported by `node --test`, so relative `.ts` imports only (R48).
 */
import { tryMoneyFromUnits, tryParseMoneyUnits } from "../../../lib/contracts/money.ts";
import type { Credit, Usd } from "../../../lib/contracts/v2/money-units.ts";
import {
  parsePublishedModel,
  priceRequest,
  type PricedResolution,
  type PublishedModel,
} from "../../../lib/contracts/v2/published-model.ts";

/** Server-only: the public API origin (no path), e.g. `https://api.example.com`. */
export const API_BASE_ENV = "INFRX_API_BASE_URL";
export const MODELS_PATH = "/v1/models";
export const CATALOG_TIMEOUT_MS = 5000;

export type UnavailableReason = "not_configured" | "unreachable" | "malformed";

export type Catalog =
  | { status: "ok"; baseUrl: string; models: PublishedModel[] }
  | { status: "unavailable"; reason: UnavailableReason };

const LOOPBACK = new Set(["127.0.0.1", "localhost", "[::1]"]);

/**
 * The configured origin, or null. https only (plain http to loopback for local development);
 * no credentials, path, query or fragment, because the examples append `/v1/...` to it.
 */
export function apiBaseUrl(env: Record<string, string | undefined>): string | null {
  const raw = env[API_BASE_ENV]?.trim();
  if (!raw) return null;
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return null;
  }
  const secure = url.protocol === "https:" || (url.protocol === "http:" && LOOPBACK.has(url.hostname));
  if (!secure || url.username || url.password || url.search || url.hash) return null;
  if (url.pathname !== "/" && url.pathname !== "") return null;
  return url.origin;
}

type Fetch = (input: string, init?: RequestInit) => Promise<Response>;

/** The published list at `baseUrl`, all of it contract-valid, or why there is none. */
export async function loadCatalog(baseUrl: string | null, fetchImpl: Fetch = fetch): Promise<Catalog> {
  if (baseUrl === null) return { status: "unavailable", reason: "not_configured" };
  let reply: Response;
  try {
    reply = await fetchImpl(`${baseUrl}${MODELS_PATH}`, {
      cache: "no-store",
      headers: { accept: "application/json" },
      signal: AbortSignal.timeout(CATALOG_TIMEOUT_MS),
    });
  } catch {
    return { status: "unavailable", reason: "unreachable" };
  }
  if (reply.status !== 200) return { status: "unavailable", reason: "unreachable" };
  let models: PublishedModel[];
  try {
    const body: unknown = await reply.json();
    const data = (body as { object?: unknown; data?: unknown } | null)?.data;
    if ((body as { object?: unknown }).object !== "list" || !Array.isArray(data)) throw new TypeError("list");
    models = data.map(parsePublishedModel);
  } catch {
    return { status: "unavailable", reason: "malformed" };
  }
  return { status: "ok", baseUrl, models };
}

/** The one price identity a record names: the CREDIT card, or the legacy USD price. Never both. */
export type PriceView =
  | {
      unit: "CREDIT";
      version: string;
      input: Credit;
      output: Credit;
      provisional: boolean;
      effectiveAt: string;
      maxHold: Credit;
    }
  | { unit: "USD"; version: string; input: Usd; output: Usd };

export function priceView(model: PublishedModel): PriceView {
  const { pricing } = model;
  if (pricing.regime === "credit") {
    const card = pricing.credit!;
    return {
      unit: "CREDIT",
      version: card.rate_card_version,
      input: card.input_rate_per_million,
      output: card.output_rate_per_million,
      provisional: card.provisional,
      effectiveAt: card.effective_at,
      maxHold: holdCredit(model, null),
    };
  }
  const usd = pricing.legacy_usd!;
  return { unit: "USD", version: usd.price_version, input: usd.input_rate_per_million, output: usd.output_rate_per_million };
}

const PER_MILLION = BigInt(1_000_000);

/**
 * The CREDIT an admission holds for one request with `maxTokens` (null: the model's output cap):
 * input ceiling = min(max_input_tokens, max_context_tokens - output), each side at its rate per
 * million, the sum rounded up to 8 places. Mirrors `validate.ceilings` and the card's
 * `ceiling_8` hold rounding for display; admission's computation is the authority.
 */
export function holdCredit(model: PublishedModel, maxTokens: number | null): Credit {
  const card = model.pricing.credit;
  if (!card) throw new TypeError("no CREDIT card in this record");
  const cap = model.capability;
  const output = maxTokens ?? cap.max_output_tokens;
  // validate.ceilings takes min(max_input, context - output); the record guarantees
  // max_input + max_output <= context, so that minimum is always max_input here.
  const input = cap.max_input_tokens;
  const scaled =
    BigInt(input) * tryParseMoneyUnits(card.input_rate_per_million)! +
    BigInt(output) * tryParseMoneyUnits(card.output_rate_per_million)!;
  const units = (scaled + PER_MILLION - BigInt(1)) / PER_MILLION;
  return tryMoneyFromUnits(units)! as unknown as Credit;
}

/**
 * Each spelling a caller may send (`aliases`, the id among them) with the pins admission records
 * for it (P-22, R109). A page lists these; they must all name the same revision and price.
 */
export function aliasResolutions(model: PublishedModel, published: readonly PublishedModel[]): PricedResolution[] {
  return model.aliases.map((alias) => priceRequest(alias, published));
}
