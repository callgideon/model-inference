// N4 test double: a DatasetsPort that records every call and answers from a script.
import type { DatasetsPort, Failure, Result } from "../../lib/services/datasets/port.ts";

export type Call = { method: keyof DatasetsPort; args: unknown[] };

export function recordingPort(answer: (method: keyof DatasetsPort, args: unknown[]) => Result<unknown> = () => ({ ok: true, value: {} })) {
  const calls: Call[] = [];
  const port = new Proxy({} as DatasetsPort, {
    get: (_, method: string) => async (...args: unknown[]) => {
      calls.push({ method: method as keyof DatasetsPort, args });
      return answer(method as keyof DatasetsPort, args);
    },
  });
  return { port, calls };
}

export const fail = (error: Failure["error"], extra: Partial<Failure> = {}): Failure => ({ ok: false, error, detail: "x", ...extra });
