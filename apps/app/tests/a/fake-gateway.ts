/**
 * A3 test double: a loopback HTTP gateway that answers the public routes with the frozen v1 wire
 * fixtures and REFUSES what the published record and the validator refuse (closed parameter set,
 * key-set-exact messages and parts, one video, the four video-reference forms, mime and byte caps,
 * R94 idempotency, owned handles, revoked keys). It is a fake: the real-route half of the check is
 * the Python replay of `example-calls.json` (A3 wiring request 1).
 *
 * Every request is recorded (method, route template, header names, JSON body, status) so a test can
 * hold the examples to the gateway's route table and header vocabulary.
 */
import { createHash, randomBytes } from "node:crypto";
import { readFileSync } from "node:fs";
import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import type { AddressInfo } from "node:net";
import {
  ContractRefusal,
  resolveModel,
  type PublishedModel,
} from "../../lib/contracts/v2/published-model.ts";

const V1 = new URL("../../../infrx-api/infrx/contracts/fixtures/v1/", import.meta.url);
const fixture = (name: string) => JSON.parse(readFileSync(new URL(name, V1), "utf8"));

export const ENVELOPES = fixture("error_envelopes.json") as Record<
  string,
  { http_status: number; envelope: { error: Record<string, unknown> }; in_stream_only: boolean }
>;
const NONSTREAM = fixture("chat_success_nonstream.json");
const SSE = fixture("chat_stream_sse.json") as {
  frames: { comment?: string; id?: string; event?: string; data?: unknown }[];
};
const ACCEPTED = fixture("job_accepted.json");
const STATUS = fixture("job_status.json");
const RESULT = fixture("job_result.json");
const UPLOAD_CREATED = fixture("upload_created.json");
const UPLOAD_COMPLETED = fixture("upload_completed.json");

/** The text a successful answer carries (the fixture's). */
export const ANSWER_TEXT: string = NONSTREAM.choices[0].message.content;

export type Call = {
  method: string;
  route: string;
  headers: string[];
  body: unknown;
  status: number;
};

class Refusal extends Error {
  readonly code: string;

  constructor(code: string) {
    super(code);
    this.code = code;
  }
}

const ROLES = new Set(["system", "user", "assistant"]);
const UPLOAD_FIELDS = new Set(["max_bytes", "bytes", "accepted_mime", "digest"]);
const HANDLE = /^(job|upl)_[A-Za-z0-9_-]{43}$/;
const newHandle = (kind: "job" | "upl") => `${kind}_${randomBytes(32).toString("base64url")}`;
const sameKeys = (value: object, keys: string[]) =>
  Object.keys(value).length === keys.length && keys.every((k) => k in value);

function renderSse(frames: typeof SSE.frames): string {
  return frames
    .map((f) => {
      if (f.comment !== undefined) return `: ${f.comment}\n\n`;
      const lines = [];
      if (f.id !== undefined) lines.push(`id: ${f.id}`);
      if (f.event !== undefined) lines.push(`event: ${f.event}`);
      lines.push(`data: ${typeof f.data === "string" ? f.data : JSON.stringify(f.data)}`);
      return `${lines.join("\n")}\n\n`;
    })
    .join("");
}

export type FakeGateway = {
  baseUrl: string;
  calls: Call[];
  close: () => Promise<void>;
};

export async function startFakeGateway(
  published: PublishedModel,
  { keys, revoked = [] }: { keys: string[]; revoked?: string[] },
): Promise<FakeGateway> {
  const cap = published.capability;
  const video = cap.video!;
  const calls: Call[] = [];
  const jobs = new Map<string, { key: string }>();
  const idem = new Map<string, { hash: string; handle: string }>();
  const uploads = new Map<
    string,
    { key: string; max: number; mime: string[]; bytes?: number; digest?: string; data?: Buffer; done: boolean }
  >();

  function checkVideoRef(ref: unknown, key: string): void {
    if (typeof ref !== "object" || ref === null || !sameKeys(ref, ["url"])) throw new Refusal("invalid_request");
    const url = (ref as { url: unknown }).url;
    if (typeof url !== "string") throw new Refusal("invalid_request");
    if (url.startsWith("https://") || url.startsWith("http://")) return;
    if (url.startsWith("data:")) {
      const match = /^data:([a-z0-9.+-]+\/[a-z0-9.+-]+);base64,/.exec(url);
      if (!match || !video.mime_types.includes(match[1])) throw new Refusal("unsupported_media");
      if (Buffer.from(url.slice(match[0].length), "base64").length > video.max_bytes) {
        throw new Refusal("request_too_large");
      }
      return;
    }
    if (url.startsWith("infrx-upload:")) {
      const upload = uploads.get(url.slice("infrx-upload:".length));
      if (!upload || upload.key !== key || !upload.done) throw new Refusal("not_found");
      return;
    }
    throw new Refusal("unsupported_media");
  }

  function checkChat(body: unknown, key: string): Record<string, unknown> {
    if (typeof body !== "object" || body === null || Array.isArray(body)) throw new Refusal("invalid_request");
    const b = body as Record<string, unknown>;
    for (const name of Object.keys(b)) {
      if (!cap.parameters.includes(name)) throw new Refusal("unsupported_parameter");
      if (b[name] === null) throw new Refusal("invalid_request");
    }
    try {
      resolveModel(String(b.model ?? published.id), [published]);
    } catch (error) {
      if (error instanceof ContractRefusal) throw new Refusal("not_found");
      throw error;
    }
    const max = b.max_tokens ?? b.max_completion_tokens;
    if (max !== undefined && (!Number.isInteger(max) || (max as number) < 1 || (max as number) > cap.max_output_tokens)) {
      throw new Refusal("invalid_request");
    }
    if ("stream" in b && typeof b.stream !== "boolean") throw new Refusal("invalid_request");
    const messages = b.messages;
    if (!Array.isArray(messages) || messages.length === 0) throw new Refusal("invalid_request");
    let videos = 0;
    for (const m of messages) {
      if (typeof m !== "object" || m === null || !sameKeys(m, ["role", "content"])) throw new Refusal("invalid_request");
      if (!ROLES.has(m.role)) throw new Refusal("invalid_request");
      if (typeof m.content === "string") continue;
      if (!Array.isArray(m.content) || m.content.length === 0) throw new Refusal("invalid_request");
      for (const part of m.content) {
        if (part?.type === "text" && sameKeys(part, ["type", "text"]) && typeof part.text === "string") continue;
        if (part?.type === "video_url" && sameKeys(part, ["type", "video_url"])) {
          videos += 1;
          if (videos > video.max_per_request) throw new Refusal("unsupported_media");
          checkVideoRef(part.video_url, key);
          continue;
        }
        throw new Refusal(part?.type === "text" || part?.type === "video_url" ? "invalid_request" : "unsupported_media");
      }
    }
    return b;
  }

  function send(res: ServerResponse, call: Call, status: number, body?: unknown, headers: Record<string, string> = {}) {
    call.status = status;
    res.writeHead(status, {
      "Inference-Id": "4d4d4d4d-0000-4000-8000-000000000004",
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      ...headers,
    });
    res.end(body === undefined ? undefined : JSON.stringify(body));
  }

  function sse(res: ServerResponse, call: Call, frames: typeof SSE.frames) {
    call.status = 200;
    res.writeHead(200, { "Content-Type": "text/event-stream", "Inference-Id": "4d4d4d4d-0000-4000-8000-000000000004" });
    res.end(renderSse(frames));
  }

  function accept(res: ServerResponse, call: Call, key: string, body: Record<string, unknown>, idemKey: string | undefined, mode: string) {
    const hash = createHash("sha256").update(`${mode}\n${JSON.stringify(body)}`).digest("hex");
    const prior = idemKey === undefined ? undefined : idem.get(`${key}\n${idemKey}`);
    if (prior && prior.hash !== hash) throw new Refusal("idempotency_conflict");
    const handle = prior?.handle ?? newHandle("job");
    if (!prior) {
      jobs.set(handle, { key });
      if (idemKey !== undefined) idem.set(`${key}\n${idemKey}`, { hash, handle });
    }
    send(res, call, 202, { ...ACCEPTED, job_handle: handle, idempotency_replayed: Boolean(prior) }, {
      Location: `/v1/jobs/${handle}`,
      "Retry-After": "2",
      ...(prior ? { "Idempotency-Replayed": "true" } : {}),
    });
  }

  async function handle(req: IncomingMessage, res: ServerResponse, call: Call, raw: Buffer) {
    const url = new URL(req.url ?? "/", "http://fake");
    const path = url.pathname;
    const method = req.method ?? "GET";
    if (method === "GET" && path === "/v1/models") {
      call.route = "/v1/models";
      return send(res, call, 200, { object: "list", data: [published] });
    }
    const bearer = /^Bearer (.+)$/.exec(req.headers.authorization ?? "")?.[1];
    const key = bearer !== undefined && keys.includes(bearer) && !revoked.includes(bearer) ? bearer : null;
    const json = () => {
      if (raw.length === 0) return {};
      try {
        return JSON.parse(raw.toString("utf8"));
      } catch {
        throw new Refusal("invalid_request");
      }
    };
    const parts = path.split("/").filter(Boolean);
    const route =
      parts[1] === "jobs" && parts.length >= 3
        ? `/v1/jobs/{handle}${parts[3] ? `/${parts[3]}` : ""}`
        : parts[1] === "uploads" && parts.length >= 3
          ? `/v1/uploads/{handle}${parts[3] ? `/${parts[3]}` : ""}`
          : path;
    call.route = route;
    if (!["/v1/chat/completions", "/v1/jobs", "/v1/jobs/{handle}", "/v1/jobs/{handle}/result", "/v1/jobs/{handle}/events", "/v1/uploads", "/v1/uploads/{handle}", "/v1/uploads/{handle}/complete"].includes(route)) {
      throw new Refusal("not_found");
    }
    if (key === null) throw new Refusal("invalid_api_key");
    const handleParam = route.includes("{handle}") ? parts[2] : "";
    if (route.includes("{handle}") && !HANDLE.test(handleParam)) throw new Refusal("not_found");

    if (route === "/v1/chat/completions" && method === "POST") {
      const body = checkChat(json(), key);
      const prefer = String(req.headers.prefer ?? "").split(",").map((t) => t.trim().split("=")[0].toLowerCase());
      const idemKey = req.headers["idempotency-key"] as string | undefined;
      if (prefer.includes("respond-async")) {
        if (body.stream) throw new Refusal("invalid_request");
        return accept(res, call, key, body, idemKey, "async");
      }
      return body.stream ? sse(res, call, SSE.frames) : send(res, call, 200, NONSTREAM);
    }
    if (route === "/v1/jobs" && method === "POST") {
      const body = checkChat(json(), key);
      if (body.stream) throw new Refusal("invalid_request");
      return accept(res, call, key, body, req.headers["idempotency-key"] as string | undefined, "async");
    }
    if (route.startsWith("/v1/jobs/")) {
      const job = jobs.get(handleParam);
      if (!job || job.key !== key) throw new Refusal("not_found");
      if (method === "GET" && route === "/v1/jobs/{handle}") return send(res, call, 200, { ...STATUS, job_handle: handleParam });
      if (method === "GET" && route === "/v1/jobs/{handle}/result") return send(res, call, 200, { ...RESULT, job_handle: handleParam });
      if (method === "GET" && route === "/v1/jobs/{handle}/events") {
        const last = req.headers["last-event-id"] as string | undefined;
        const from = last ? SSE.frames.findIndex((f) => f.id === last) : -1;
        if (last && from === -1) throw new Refusal("invalid_cursor");
        return sse(res, call, SSE.frames.slice(from + 1));
      }
      if (method === "DELETE" && route === "/v1/jobs/{handle}") return send(res, call, 200, { ...STATUS, job_handle: handleParam });
      throw new Refusal("not_found");
    }
    if (route === "/v1/uploads" && method === "POST") {
      const body = json() as Record<string, unknown>;
      if (Object.keys(body).some((k) => !UPLOAD_FIELDS.has(k))) throw new Refusal("invalid_request");
      const max = body.max_bytes;
      const mime = body.accepted_mime;
      if (!Number.isInteger(max) || (max as number) < 1 || (max as number) > video.max_bytes) throw new Refusal("invalid_request");
      if (!Array.isArray(mime) || mime.length === 0 || mime.some((m) => !video.mime_types.includes(m))) {
        throw new Refusal("invalid_request");
      }
      if (body.bytes !== undefined && (!Number.isInteger(body.bytes) || (body.bytes as number) > (max as number))) {
        throw new Refusal("invalid_request");
      }
      if (body.digest !== undefined && !/^sha256:[0-9a-f]{64}$/.test(String(body.digest))) throw new Refusal("invalid_request");
      const handle = newHandle("upl");
      uploads.set(handle, { key, max: max as number, mime: mime as string[], bytes: body.bytes as number | undefined, digest: body.digest as string | undefined, done: false });
      return send(res, call, 201, { ...UPLOAD_CREATED, upload_handle: handle, destination_ref: `infrx-upload:${handle}`, max_bytes: max, accepted_mime: mime });
    }
    const upload = uploads.get(handleParam);
    if (!upload || upload.key !== key) throw new Refusal("not_found");
    if (route === "/v1/uploads/{handle}" && method === "PUT") {
      const type = (req.headers["content-type"] ?? "").split(";")[0].trim().toLowerCase();
      if (!upload.mime.includes(type)) throw new Refusal("unsupported_media");
      if (raw.length > upload.max || (upload.bytes !== undefined && raw.length !== upload.bytes)) throw new Refusal("invalid_request");
      if (upload.digest !== undefined && upload.digest !== `sha256:${createHash("sha256").update(raw).digest("hex")}`) {
        throw new Refusal("invalid_request");
      }
      upload.data = raw;
      return send(res, call, 204);
    }
    if (route === "/v1/uploads/{handle}/complete" && method === "POST") {
      if (raw.length > 0 && JSON.stringify(json()) !== "{}") throw new Refusal("invalid_request");
      if (!upload.data) throw new Refusal("state_conflict");
      upload.done = true;
      const digest = `sha256:${createHash("sha256").update(upload.data).digest("hex")}`;
      return send(res, call, 200, {
        ...UPLOAD_COMPLETED,
        upload_handle: handleParam,
        media: { ...UPLOAD_COMPLETED.media, handle: handleParam, bytes: upload.data.length, digest },
      });
    }
    throw new Refusal("not_found");
  }

  const server: Server = createServer((req, res) => {
    const chunks: Buffer[] = [];
    req.on("data", (c: Buffer) => chunks.push(c));
    req.on("end", () => {
      const call: Call = {
        method: req.method ?? "GET",
        route: "",
        headers: Object.keys(req.headers).sort(),
        body: null,
        status: 0,
      };
      calls.push(call);
      const raw = Buffer.concat(chunks);
      if ((req.headers["content-type"] ?? "").startsWith("application/json") && raw.length > 0) {
        try {
          call.body = JSON.parse(raw.toString("utf8"));
        } catch {
          // recorded as null; `handle` refuses it
        }
      }
      handle(req, res, call, raw).catch((error: unknown) => {
        const code = error instanceof Refusal ? error.code : "internal_error";
        const entry = ENVELOPES[code];
        send(res, call, entry.http_status, entry.envelope);
      });
    });
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address() as AddressInfo;
  return {
    baseUrl: `http://127.0.0.1:${port}`,
    calls,
    close: () => new Promise((resolve) => server.close(() => resolve())),
  };
}
