"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Check, Copy, Loader2, Plus } from "lucide-react";
import { toast } from "sonner";
import { createConsumerKey } from "@/app/actions";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { MAX_KEY_NAME_CHARS } from "@/lib/contracts/types";
import { CREATE_LOST, ONE_TIME_COPY, createOutcome, settle } from "./view-model";

export function CreateKeyDialog() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The plaintext lives in this component's state only: never stored, logged or put in a URL.
  const [secret, setSecret] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  // One idempotency key per opened dialog: a double submit or a retry replays, never mints a second key.
  const [attempt, setAttempt] = useState(() => crypto.randomUUID());

  function reset(next: boolean) {
    setOpen(next);
    if (!next) {
      setName("");
      setSecret(null);
      setError(null);
      setCopied(false);
      setAttempt(crypto.randomUUID());
      router.refresh();
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (pending) return;
    setPending(true);
    setError(null);
    const outcome = createOutcome(await settle(() => createConsumerKey({ name, idempotency_key: attempt }), CREATE_LOST));
    setPending(false);
    if (outcome.kind === "secret") setSecret(outcome.secret);
    else setError(outcome.message);
  }

  async function copy() {
    if (!secret) return;
    try {
      await navigator.clipboard.writeText(secret);
      setCopied(true);
      toast.success("API key copied");
    } catch {
      toast.error("Copying was blocked; select the key and copy it by hand.");
    }
  }

  return (
    <Dialog open={open} onOpenChange={reset}>
      <DialogTrigger render={<Button />}>
        <Plus />
        Create key
      </DialogTrigger>
      <DialogContent>
        {secret ? (
          <>
            <DialogHeader>
              <DialogTitle>Copy your key now</DialogTitle>
              <DialogDescription>{ONE_TIME_COPY}</DialogDescription>
            </DialogHeader>
            <div className="flex items-center gap-2 rounded-lg border bg-muted/40 p-2">
              <code className="min-w-0 flex-1 overflow-x-auto font-mono text-xs select-all">{secret}</code>
              <Button size="icon-sm" variant="outline" onClick={copy} aria-label="Copy key">
                {copied ? <Check /> : <Copy />}
              </Button>
            </div>
            <Button onClick={() => reset(false)}>Done</Button>
          </>
        ) : (
          <form onSubmit={submit} className="contents">
            <DialogHeader>
              <DialogTitle>Create an API key</DialogTitle>
              <DialogDescription>Name it after where it will be used. The key is shown once.</DialogDescription>
            </DialogHeader>
            <div className="space-y-2">
              <Label htmlFor="key-name">Name</Label>
              <Input
                id="key-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="production"
                maxLength={MAX_KEY_NAME_CHARS}
                autoComplete="off"
                autoFocus
                required
                aria-invalid={error !== null}
                aria-describedby={error ? "key-name-error" : undefined}
                className="w-full"
              />
              {error ? (
                <p id="key-name-error" role="alert" className="text-xs text-destructive">
                  {error}
                </p>
              ) : null}
            </div>
            <Button type="submit" disabled={pending}>
              {pending ? <Loader2 className="animate-spin" /> : null}
              Create key
            </Button>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
