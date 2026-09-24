#!/usr/bin/env python3
"""Resumable large-dataset client for the infrx gateway: one logical result per item.

    export INFRX_API_KEY=...                         # never on argv
    python models/marlin2b/dataset.py run --manifest items.jsonl --state run.sqlite \
        --dataset-version ds-2026-09-24 --base-url https://<gateway>/v1 --model <pin> \
        --retain-output text [--form upload] [--concurrency 4] [--only failures.jsonl]
    python models/marlin2b/dataset.py export --state run.sqlite \
        --results results.jsonl --failures failures.jsonl

The manifest is JSONL, read line by line (never loaded whole): one item per line,
`{"id": "<stable id>", "video": "<local path, relative to the manifest>" | null,
"prompt": "...", "max_tokens": 512, "start_s": 0, "end_s": 8}`.

Identity is the SOP recipe (bench.item_key, marlin-sop.md §3.1): the item id, a digest of
its media, its span, and a digest of prompt + output budget + model make the item key, and
`Idempotency-Key = sop1.<item_key>`. The key is written to the SQLite journal BEFORE the
request leaves (state `sent`), so an interruption at any point is resumed under the same
key and the gateway replays the committed answer instead of billing a second inference.
An item whose content changed after its key was used is refused, never re-keyed.

Journal states: staged (upload finalized, never sent) -> sent -> done | failed (retryable,
same key) | quarantined (terminal refusal or a cancelled replay, R106) | expired (the
gateway said result_expired: re-running would be a new billed inference, so it is exported
as a failure and never re-sent automatically). A staged handle past its expiry is re-staged
only if the item was never sent; a sent item always keeps its handle (same payload).

Output retention is declared, not defaulted: `--retain-output text` keeps the answer in the
journal for export; `digest` keeps only its sha256 and length.
"""
import argparse, asyncio, hashlib, json, os, re, sqlite3, sys, time
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx

import bench

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS items(
  item_id TEXT PRIMARY KEY, idem_key TEXT NOT NULL DEFAULT '', state TEXT NOT NULL,
  upload_handle TEXT, upload_expires_at TEXT,
  sends INTEGER NOT NULL DEFAULT 0, fresh INTEGER NOT NULL DEFAULT 0,
  replayed INTEGER NOT NULL DEFAULT 0, transport_retries INTEGER NOT NULL DEFAULT 0,
  http_status INTEGER, error_class TEXT, error_code TEXT, inference_id TEXT,
  prompt_tokens INTEGER, completion_tokens INTEGER,
  output TEXT, output_sha256 TEXT, output_chars INTEGER, updated_at TEXT NOT NULL);
"""
TERMINAL = frozenset({"done", "quarantined", "expired"})
ID_OK = re.compile(r"[A-Za-z0-9._:-]{1,128}")
# The knobs that decide every key and payload: resuming across any of them is refused.
FINGERPRINT = ("dataset_version", "profile_version", "model", "base_url", "form")


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def file_digest(path):
    """sha256 in 1 MiB chunks: bounded memory whatever the clip size."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def iter_manifest(path, only=None, progress=None):
    """Yield items one line at a time. `progress["read"]` counts lines read, so a caller
    (and the test) can see that the producer never runs ahead of the bounded queue."""
    base = os.path.dirname(os.path.abspath(path))
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            if progress is not None:
                progress["read"] += 1
            item = json.loads(line)
            if not isinstance(item, dict) or not ID_OK.fullmatch(str(item.get("id", ""))):
                raise SystemExit(f"manifest line {n}: an item needs an id matching {ID_OK.pattern}")
            if only is not None and item["id"] not in only:
                continue
            video = item.get("video")
            yield {"id": item["id"], "prompt": str(item.get("prompt") or ""),
                   "max_tokens": int(item.get("max_tokens") or 512),
                   "start_s": item.get("start_s", 0), "end_s": item.get("end_s", 0),
                   "video": os.path.join(base, video) if video else None}


def read_ids(path):
    """Item ids from a failure export (JSON lines with item_id/id) or a plain id list."""
    ids = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                row = json.loads(line) if line.startswith("{") else {"id": line}
                ids.add(str(row.get("item_id") or row.get("id")))
    return ids


def identity(item, media_digest, a):
    """(item_key, Idempotency-Key): bench.item_key over this item's content."""
    question = hashlib.sha256("\x1f".join((item["prompt"], str(item["max_tokens"]), a.model))
                              .encode("utf-8")).hexdigest()[:16]
    key = bench.item_key(a.dataset_version, item["id"], (media_digest or "nomedia")[:16], 0,
                         item["start_s"], item["end_s"], question, a.profile_version)
    return key, bench.IDEMPOTENCY_PREFIX + key


class Journal:
    """SQLite, one row per item, committed at every transition.
    ponytail: synchronous writes on the event loop (a few ms each); move to a thread if
    they ever show up in driver lag."""

    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

    def check_meta(self, current):
        stored = dict(self.db.execute("SELECT k, v FROM meta").fetchall())
        if not stored:
            self.db.executemany("INSERT INTO meta VALUES (?, ?)", sorted(current.items()))
            self.db.commit()
            return
        differing = [k for k in FINGERPRINT if stored.get(k) != current[k]]
        if differing:
            raise SystemExit("refusing to resume: " + "; ".join(
                f"{k} was {stored.get(k)!r}, now {current[k]!r}" for k in differing)
                + ". These decide every key and payload.")

    def get(self, item_id):
        return self.db.execute("SELECT * FROM items WHERE item_id = ?", (item_id,)).fetchone()

    def put(self, item_id, **fields):
        fields["updated_at"] = now()
        if self.get(item_id) is None:
            names = ["item_id", *fields]
            self.db.execute(f"INSERT INTO items ({', '.join(names)}) VALUES "
                            f"({', '.join('?' * len(names))})", [item_id, *fields.values()])
        else:
            self.db.execute(f"UPDATE items SET {', '.join(f'{k} = ?' for k in fields)} "
                            f"WHERE item_id = ?", [*fields.values(), item_id])
        self.db.commit()

    def rows(self):
        return self.db.execute("SELECT * FROM items ORDER BY item_id")


def classify(row):
    """The journal state a final attempt row leaves its item in."""
    if row["outcome"] == "accepted":
        return "done"
    if row["outcome"] == bench.CANCELLED_REPLAY:
        return "quarantined"
    if row["http_status"] == 410 and row["error_code"] == "result_expired":
        return "expired"
    return "quarantined" if bench.is_terminal(row) else "failed"


def expired(stamp):
    try:
        return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc) <= datetime.now(timezone.utc)
    except (TypeError, ValueError):
        return True                 # an unreadable expiry is treated as past, never trusted


async def process(item, cfg, journal, a, limits, stats):
    """One item from its journal row to a terminal or retryable state."""
    row = journal.get(item["id"])
    if row is not None and row["state"] in TERMINAL:
        stats["skipped_terminal"] += 1          # before hashing: a resume is cheap
        return
    try:
        media = await asyncio.to_thread(file_digest, item["video"]) if item["video"] else None
    except OSError as e:
        stats["failed"] += 1
        journal.put(item["id"], state="failed", error_class=type(e).__name__)
        return
    item_key, idem = identity(item, media, a)
    if row is not None and row["idem_key"] != idem:
        if row["sends"]:
            stats["changed"] += 1           # its key was used: re-keying would bill it twice
            journal.put(item["id"], error_class="item_changed_after_send")
            return
        journal.put(item["id"], idem_key=idem, upload_handle=None, upload_expires_at=None)
        row = journal.get(item["id"])
    if row is None:
        journal.put(item["id"], idem_key=idem, state="new")
        row = journal.get(item["id"])
    form = "text" if not item["video"] else a.form
    handle = row["upload_handle"]
    if form == "upload" and (handle is None or (not row["sends"]
                                                and expired(row["upload_expires_at"]))):
        staging = {}
        async with limits["upload"]:
            try:
                handle = await bench.upload(cfg["client"], cfg, item["video"], staging)
            except (bench.UploadFailed, httpx.HTTPError, OSError) as e:
                stats["failed"] += 1
                journal.put(item["id"], state="failed", error_class=type(e).__name__,
                            http_status=staging.get("upload_status"))
                return
        journal.put(item["id"], state="staged", upload_handle=handle,
                    upload_expires_at=staging.get("upload_expires_at"))
    # Durable intent BEFORE the request leaves: from here a resume can only replay.
    journal.put(item["id"], state="sent", sends=row["sends"] + 1)
    req = {"seq": stats["sent"], "clip_id": item["id"], "form": form, "prompt_kind": None,
           "cold": None, "duration_s": None, "item_key": item_key, "idempotency_key": idem,
           "segment_index": 0, "tenant": 0, "max_tokens": item["max_tokens"],
           "upload_handle": handle, "cancel_at_s": None, "arrival_s": 0.0,
           "clip": {"path": item["video"], "file": item["video"]} if item["video"] else None,
           "prompt": item["prompt"]}
    stats["sent"] += 1
    rows = []
    await bench.run_one(cfg["client"], cfg, req, bench.CLOCK(), rows)
    final, text = rows[-1], "".join(cfg["outputs"].pop(item_key, []))
    state = classify(final)
    replay = bool(final["idempotency_replayed"])
    stats[state] += 1
    stats["replayed"] += replay
    fields = dict(state=state, http_status=final["http_status"],
                  error_class=final["error_class"], error_code=final["error_code"],
                  inference_id=final["inference_id"], replayed=row["replayed"] + replay,
                  transport_retries=row["transport_retries"] + len(rows) - 1)
    if state == "done":
        fields.update(fresh=int(not replay), prompt_tokens=final["prompt_tokens"],
                      completion_tokens=final["completion_tokens"],
                      output=text if a.retain_output == "text" else None,
                      output_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                      output_chars=len(text))
    journal.put(item["id"], **fields)


async def run(a, journal, stats, transport=None):
    keys = (bench.api_key(),)
    headers = {"content-type": "application/json", "accept": "text/event-stream",
               "authorization": f"Bearer {keys[0]}"}
    only = read_ids(a.only) if a.only else None
    limits = {"upload": asyncio.Semaphore(a.upload_concurrency)}
    queue = asyncio.Queue(maxsize=a.queue)
    async with httpx.AsyncClient(timeout=a.timeout, transport=transport) as client:
        # bench's request path, unchanged: same allowlists, same replay classification.
        cfg = {"args": SimpleNamespace(target="gateway", mm_kwargs=""), "key": keys,
               "headers_for": lambda tenant: dict(headers), "base": a.base_url.rstrip("/"),
               "model": a.model, "retries": a.retries, "open_loop": False,
               "media_base_url": "", "mm_fixed": None, "video": None, "outputs": {},
               "client": client}

        async def worker():
            while (item := await queue.get()) is not None:
                try:
                    await process(item, cfg, journal, a, limits, stats)
                except Exception as e:          # one item's fault never ends the run
                    stats["failed"] += 1
                    journal.put(item["id"], state="failed", error_class=type(e).__name__)
                stats["finished"] += 1

        workers = [asyncio.create_task(worker()) for _ in range(a.concurrency)]
        for item in iter_manifest(a.manifest, only, stats):
            await queue.put(item)                        # blocks: the queue is the bound
            stats["read_ahead_max"] = max(stats["read_ahead_max"],
                                          stats["read"] - stats["finished"])
        for _ in workers:
            await queue.put(None)
        await asyncio.gather(*workers)


def export(journal, results=None, failures=None):
    """Results: one line per done item. Failures: every other item, with whether a plain
    rerun (same key) retries it; feed the file back with `run --only` to retry that subset."""
    counts = {}
    with open(results or os.devnull, "w", encoding="utf-8") as res, \
            open(failures or os.devnull, "w", encoding="utf-8") as fail:
        for r in journal.rows():
            counts[r["state"]] = counts.get(r["state"], 0) + 1
            if r["state"] == "done":
                res.write(json.dumps({
                    "item_id": r["item_id"], "idempotency_key": r["idem_key"],
                    "inference_id": r["inference_id"],
                    "served": "fresh" if r["fresh"] else "replay",
                    "sends": r["sends"], "prompt_tokens": r["prompt_tokens"],
                    "completion_tokens": r["completion_tokens"], "output": r["output"],
                    "output_sha256": r["output_sha256"], "output_chars": r["output_chars"]})
                    + "\n")
            else:
                fail.write(json.dumps({
                    "item_id": r["item_id"], "state": r["state"],
                    "retryable": r["state"] in ("failed", "sent", "staged", "new")
                    and r["error_class"] != "item_changed_after_send",
                    "http_status": r["http_status"], "error_class": r["error_class"],
                    "error_code": r["error_code"], "idempotency_key": r["idem_key"]}) + "\n")
    return counts


def parse_args(argv):
    bench.refuse_embedded_key(argv)
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--manifest", required=True)
    r.add_argument("--state", required=True, help="SQLite journal; created if absent")
    r.add_argument("--dataset-version", required=True)
    r.add_argument("--profile-version", default="v1")
    r.add_argument("--base-url", required=True)
    r.add_argument("--model", required=True)
    r.add_argument("--retain-output", choices=["text", "digest"], required=True,
                   help="declared local retention: the answer text, or its digest only")
    r.add_argument("--form", choices=["upload", "video_b64"], default="upload")
    r.add_argument("--concurrency", type=int, default=4)
    r.add_argument("--upload-concurrency", type=int, default=2)
    r.add_argument("--queue", type=int, default=8, help="bounded producer queue")
    r.add_argument("--only", default=None, help="a failure export or id list: retry just these")
    r.add_argument("--retries", type=int, default=0, help="429/503 retries within this run")
    r.add_argument("--timeout", type=float, default=600.0)
    r.add_argument("--dry-run-transport", default=None)
    e = sub.add_parser("export")
    e.add_argument("--state", required=True)
    e.add_argument("--results", default=None)
    e.add_argument("--failures", default=None)
    a = ap.parse_args(argv)
    if a.cmd == "run" and min(a.concurrency, a.upload_concurrency, a.queue) < 1:
        ap.error("--concurrency, --upload-concurrency and --queue are at least 1")
    return a


def main(argv=None):
    bench.mute_library_logging()
    a = parse_args(list(sys.argv[1:] if argv is None else argv))
    journal = Journal(a.state)
    if a.cmd == "export":
        print(json.dumps(export(journal, a.results, a.failures), sort_keys=True))
        return 0
    bench.refuse_key_in_args(a, (bench.api_key(),))
    if not bench.api_key() and not a.dry_run_transport:
        raise SystemExit(f"export {bench.KEY_ENV[0]} or {bench.KEY_ENV[1]}")
    journal.check_meta({k: str(getattr(a, k)) for k in FINGERPRINT})
    stats = dict.fromkeys(("read", "finished", "read_ahead_max", "sent", "skipped_terminal",
                           "changed", "done", "failed", "quarantined", "expired",
                           "replayed"), 0)
    transport = bench.load_transport(a.dry_run_transport) if a.dry_run_transport else None
    t0, code = time.monotonic(), 0
    try:
        asyncio.run(run(a, journal, stats, transport))
    except KeyboardInterrupt:
        code = 130                  # every transition so far is committed; rerun to resume
    stats["wall_s"] = round(time.monotonic() - t0, 3)
    stats["interrupted"] = code == 130
    print(bench.dump_line(stats, (bench.api_key(),), sort_keys=True))
    return code or (1 if stats["failed"] or stats["changed"] else 0)


if __name__ == "__main__":
    sys.exit(main())
