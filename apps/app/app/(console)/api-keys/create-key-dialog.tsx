"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Check, Copy, Loader2, Plus } from "lucide-react";
import { toast } from "sonner";
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
import { rememberKey } from "@/lib/keys";
import { createApiKey } from "./actions";

export function CreateKeyDialog() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [secret, setSecret] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  function reset(next: boolean) {
    setOpen(next);
    if (!next) {
      setName("");
      setSecret(null);
      setError(null);
      setCopied(false);
      router.refresh();
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setPending(true);
    setError(null);
    const result = await createApiKey(name);
    setPending(false);
    if (result.ok) {
      // Kept in this tab only, so the Models snippets are runnable without a re-paste.
      rememberKey(result.id, result.key);
      setSecret(result.key);
    } else setError(result.error);
  }

  async function copy() {
    if (!secret) return;
    await navigator.clipboard.writeText(secret);
    setCopied(true);
    toast.success("API key copied");
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
              <DialogDescription>
                This is the only time we can show it — we store the hash, not the key.
              </DialogDescription>
            </DialogHeader>
            <div className="flex items-center gap-2 rounded-lg border bg-muted/40 p-2">
              <code className="min-w-0 flex-1 overflow-x-auto font-mono text-xs">{secret}</code>
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
              <DialogDescription>Name it after where it will be used.</DialogDescription>
            </DialogHeader>
            <div className="space-y-2">
              <Label htmlFor="key-name">Name</Label>
              <Input
                id="key-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="production"
                autoFocus
                required
                className="w-full"
              />
              {error ? <p className="text-xs text-destructive">{error}</p> : null}
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
