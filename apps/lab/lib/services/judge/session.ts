// The user's own Lab session as an RPC caller (server only: it reads the request's cookies). Shared by
// the C3L actions and the J3 per-request read; the Lab holds no service-role key.
import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";
import type { JudgePort } from "../../../components/traces/judge/port.ts";
import { sessionRpc, type Rpc } from "./core.ts";
import { rpcJudgePort } from "./runs.ts";

export async function session(): Promise<Rpc> {
  return sessionRpc(process.env, await cookies(), (url, key, options) => {
    const client = createServerClient(url, key, options);
    return { rpc: (name, args) => client.rpc(name, args) };
  });
}

/** WR-V3-1: what `judgePort()` (components/traces/judge/port.ts) returns once wired. */
export function sessionJudgePort(): JudgePort {
  return rpcJudgePort(session);
}
