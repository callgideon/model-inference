"use client";
// AP-09 09d: the Add model wizard's client islands. Each form keeps the person's values after a
// refusal (never a secret reference), shows its pending state (no double submit) and carries the
// operation key its page rendered. The browser upload hashes each chosen file, asks the API for a
// scoped PUT per file and then for server verification; it holds no storage credential.
import { useActionState, useEffect, useRef, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/field";
import type { FileEntry } from "./api";
import { completeUpload, createProject, createRevision, createUpload, grantPart, startImport } from "./actions";
import type { Problem } from "./wizard";
import s from "../../operate.module.css";

function Refusal({ problem }: { problem: Problem | null }) {
  if (problem === null) return null;
  return (
    <div role="alert" className="lab-stack">
      <p>{problem.message}</p>
      {problem.fields.length > 0 && <ul>{problem.fields.map((f) => <li key={f}>{f}</li>)}</ul>}
    </div>
  );
}

export function ProjectForm({ idempotencyKey }: { idempotencyKey: string }) {
  const [state, action, pending] = useActionState(createProject, null);
  const v = state?.values ?? {};
  return (
    <form action={action} className={s.form}>
      <input type="hidden" name="idempotencyKey" value={idempotencyKey} />
      <Refusal problem={state} />
      <Field label="Name" description="How the Lab shows this model to your workspace.">
        <Input name="name" defaultValue={v.name} required maxLength={200} />
      </Field>
      <Field label="Identifier" description="Stable: lowercase letters, digits and hyphens, up to 63 characters. It names the model in its public identifier and cannot change.">
        <Input name="slug" defaultValue={v.slug} required pattern="[a-z0-9][a-z0-9\-]{0,62}" autoComplete="off" spellCheck={false} />
      </Field>
      <Field label="Task" description="Optional. What the model is for, for example SOP analysis of recorded robot video. Shown as description only.">
        <Input name="description" defaultValue={v.description} maxLength={4000} render={<textarea rows={3} />} />
      </Field>
      <div className={s.actions}>
        <Button type="submit" variant="primary" pending={pending}>Create project</Button>
      </div>
    </form>
  );
}

export function ImportForm({ projectId, idempotencyKey }: { projectId: string; idempotencyKey: string }) {
  const [state, action, pending] = useActionState(startImport, null);
  const v = state?.values ?? {};
  return (
    <form action={action} className={s.form}>
      <input type="hidden" name="idempotencyKey" value={idempotencyKey} />
      <input type="hidden" name="projectId" value={projectId} />
      <Refusal problem={state} />
      <Field label="Source">
        <Input name="host" defaultValue={v.host ?? "huggingface.co"} render={<select />}>
          <option value="huggingface.co">Hugging Face (huggingface.co)</option>
        </Input>
      </Field>
      <Field label="Repository" description="owner/name, for example an approved public model repository.">
        <Input name="repo" defaultValue={v.repo} required autoComplete="off" spellCheck={false} />
      </Field>
      <Field label="Commit" description="The full 40-character commit. A branch or tag can change, so it is refused.">
        <Input name="commit" defaultValue={v.commit} required pattern="[0-9a-f]{40}" autoComplete="off" spellCheck={false} />
      </Field>
      <Field label="Credential reference" description="Optional, for a gated repository: a reference such as ssm:/path or env:NAME that the import worker resolves. Never paste a token here.">
        <Input name="secretRef" autoComplete="off" spellCheck={false} />
      </Field>
      <Field label="Manifest" description="JSON: every file to import, each with relative_path, bytes, sha256 (sha256:<64 hex>) and media_type. The server fetches exactly these files and re-hashes each one.">
        <Input name="manifest" defaultValue={v.manifest} required spellCheck={false} render={<textarea rows={8} />} />
      </Field>
      <div className={s.actions}>
        <Button type="submit" variant="primary" pending={pending}>Start import</Button>
      </div>
    </form>
  );
}

// ponytail: whole-file hashing in browser memory (crypto.subtle has no incremental digest), so files up
// to 2 GiB; larger shards use the repository import until an incremental hash is worth adding.
const MAX_HASH_BYTES = 2 * 1024 ** 3;
const mediaType = (name: string) => (name.endsWith(".json") ? "application/json" : name.endsWith(".safetensors") ? "application/octet-stream" : "text/plain");
async function sha256(file: File): Promise<string> {
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", await file.arrayBuffer()));
  return `sha256:${Array.from(digest, (b) => b.toString(16).padStart(2, "0")).join("")}`;
}

export function UploadForm({ projectId, idempotencyKey }: { projectId: string; idempotencyKey: string }) {
  const router = useRouter();
  const [progress, setProgress] = useState<string | null>(null);
  const [refusal, setRefusal] = useState<Problem | null>(null);
  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const files = Array.from((event.currentTarget.elements.namedItem("files") as HTMLInputElement).files ?? []);
    const big = files.find((f) => f.size > MAX_HASH_BYTES);
    if (files.length === 0 || big) {
      setRefusal({ message: big ? `${big.name} is too large to hash in this browser. Use a repository import for it.` : "Choose the model's files.", fields: [] });
      return;
    }
    setRefusal(null);
    const stop = (p: Problem) => (setRefusal(p), setProgress(null));
    try {
      const manifest: FileEntry[] = [];
      for (const [i, f] of files.entries()) {
        setProgress(`Hashing ${f.name} (${i + 1} of ${files.length})`);
        manifest.push({ relative_path: f.name, bytes: f.size, sha256: await sha256(f), media_type: mediaType(f.name) });
      }
      setProgress("Opening the upload session");
      const session = await createUpload(projectId, manifest, idempotencyKey);
      if (!session.ok) return stop(session);
      for (const [i, f] of files.entries()) {
        setProgress(`Uploading ${f.name} (${i + 1} of ${files.length})`);
        const grant = await grantPart(session.uploadId, f.name);
        if (!grant.ok) return stop(grant);
        const put = await fetch(grant.url, { method: "PUT", body: f }).catch(() => null);
        if (!put?.ok) return stop({ message: `${f.name} was not stored. Choose the same files and upload again; the session keeps what it already received.`, fields: [] });
      }
      setProgress("Asking the server to verify the files");
      const done = await completeUpload(session.uploadId, session.manifestSha, idempotencyKey);
      if (!done.ok) return stop(done);
      router.push(`/models/new?${new URLSearchParams({ project: projectId, operation: done.operationId })}`);
    } catch {
      stop({ message: "The upload stopped before the server confirmed it. Choose the same files and upload again; nothing is duplicated.", fields: [] });
    }
  }
  return (
    <form onSubmit={upload} className={s.form}>
      <Refusal problem={refusal} />
      <Field label="Files" description="Weights as .safetensors plus config, tokenizer, processor and template files. Code files (.py, .bin, .pt) are refused.">
        <Input name="files" type="file" multiple required />
      </Field>
      {progress && <p role="status">{progress}</p>}
      <div className={s.actions}>
        <Button type="submit" pending={progress !== null}>Upload and verify</Button>
      </div>
    </form>
  );
}

export function SetupForm({ projectId, artifactId, idempotencyKey }: { projectId: string; artifactId: string; idempotencyKey: string }) {
  const [state, action, pending] = useActionState(createRevision, null);
  return (
    <form action={action} className={s.form}>
      <input type="hidden" name="idempotencyKey" value={idempotencyKey} />
      <input type="hidden" name="projectId" value={projectId} />
      <input type="hidden" name="artifactId" value={artifactId} />
      <Refusal problem={state} />
      <div className={s.actions}>
        <Button type="submit" variant="primary" pending={pending}>Create serving revision</Button>
      </div>
    </form>
  );
}

// ponytail: at most 20 automatic re-reads at the operation's retry hint; "Check again" stays for later.
const MAX_REFRESHES = 20;
/** Re-reads the page (the operation) after `seconds`, a bounded number of times. */
export function AutoRefresh({ seconds }: { seconds: number }) {
  const router = useRouter();
  const done = useRef(0);
  useEffect(() => {
    if (done.current >= MAX_REFRESHES) return;
    const timer = setTimeout(() => {
      done.current += 1;
      router.refresh();
    }, Math.max(seconds, 2) * 1000);
    return () => clearTimeout(timer);
  });
  return null;
}

