"""Q2: the same scheduling index, in Valkey, through atomic Lua.

`MemoryScheduler` is the reference implementation; this adapter is a **port**, not a
second design. Everything the Q1 evidence's fourteen-point list pins is reproduced
here, and the differential harness (`tests/q/differential.py`) runs random operation
streams against both and compares `tags()`, `virtual_times()`, `kind_tags()`,
`top_virtual_time()` and `stats()` after every operation. Where the two could drift,
they do not, for three reasons worth stating because each one was a design choice:

* **Doubles, in the same order.** Fairness state lives in ZSET scores, which are IEEE
  doubles exactly as Python's floats are, and every score is written with
  `string.format('%.17g', …)` - 17 significant digits round-trip a double exactly,
  while Lua 5.1's *default* number-to-string conversion is `%.14g` and would quietly
  round every tag. The arithmetic is spelled in the order the memory adapter spells it
  (`start = max(tag, V)` → `V = start` → `tag = start + cost / weight`), so the drift
  the Q1 evidence documents (10^6 unit dispatches land on 111110.99999952753) is
  reproduced bit for bit rather than corrected in one adapter and not the other.
* **Integer microseconds, never epoch floats.** Every instant crosses into Lua as an
  integer microsecond count (exact up to 2^53, i.e. year 287396). `now.timestamp()`
  is not exact, so a `available_at <= now` or `now >= claimed_at + TTL` boundary
  compared in epoch floats would disagree with `datetime` comparison in the last bits -
  and both boundaries are pinned *at equality* by the conformance suite.
* **One script per operation.** Enqueue, claim, acknowledge, remove, rebuild and the
  observability snapshot are each a single `EVALSHA`, so each one is atomic: a
  connection that dies mid-call leaves the index either wholly before or wholly after
  that operation, never half-indexed (`tests/q/test_valkey_scheduler.py`'s
  fault-injecting client proves it by killing the socket between send and reply).

Keys are namespaced per index, all nine declared in `KEYS`, and the namespace carries a
`{…}` hash tag so the per-flow keys derived from it (`<ns>:pending:<kind>|<org>`) hash
to the same slot as the fixed ones - the adapter targets a single Valkey (Q's `03`
gotcha: asynchronous replication is not a durable queue), and the hash tag is what
keeps a future cluster from splitting one index across slots.

What is deliberately **not** stored: the claiming worker's id. The memory adapter keeps
it and nothing reads it; the index never authorizes execution, so the worker's right to
run comes from `JobStore.claim`'s lease and generation, and storing a second copy of the
owner here would invite someone to trust it.

Persistence expectations (`03` §2.5, and E2's compose file): the index is a cache. It is
run with `--save '' --appendonly no`, because losing it must be a throughput event -
`rebuild` from PostgreSQL's non-terminal snapshot is the recovery path (Q3 owns the
reconciler), and a half-persisted index would only make the loss harder to reason about.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence

from ..contracts import errors
from ..contracts.codec import compact_bytes
from ..contracts.limits import DEFAULTS, PilotSettings
from ..contracts.records import DISPATCH_KINDS, IndexEvent, OutboxKind
from .memory import MAX_INDEX_BYTES, MAX_INDEX_ITEMS, _one_second

# F2R/F2P own the config names. `PilotSettings` already carries `valkey_url`; the two
# caps are still module constants in `memory.py` (Q1's coordinator request), so they are
# read through the settings object *if it grows the fields* and fall back to Q1's
# constants otherwise - one `getattr` instead of a second source of truth.
_ITEMS_FIELD, _BYTES_FIELD = "max_index_items", "max_index_bytes"

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_MICROSECOND = timedelta(microseconds=1)
# A claim is select-then-commit (see `claim_candidate`); a concurrent claimer can change
# the winner between the two calls, and each retry prices the new winner. The bound turns
# a pathological livelock into a typed error instead of an unbounded loop.
_CLAIM_ATTEMPTS = 16


def _us(moment: datetime) -> int:
    """An instant as exact integer microseconds since the epoch.

    `datetime` resolution *is* the microsecond, so this loses nothing, and it keeps every
    time comparison inside Lua on integers - `moment.timestamp()` would be a double near
    1.7e9 whose ulp is ~2.4e-7 s, which is enough to disagree with `datetime` comparison
    at exactly the TTL boundary the conformance suite pins.
    """
    return (moment - _EPOCH) // _MICROSECOND


def _seconds_us(seconds: float) -> int:
    """`timedelta(seconds=…)` in microseconds: the memory adapter's own rounding."""
    return timedelta(seconds=seconds) // _MICROSECOND


def _text(value: Any) -> str:
    """The reply as text, whether or not the caller's client decodes responses."""
    return value.decode() if isinstance(value, (bytes, bytearray)) else str(value)


def connect(limits: PilotSettings = DEFAULTS, **kw: Any):
    """An async client for `limits.valkey_url`, or a typed refusal if it is unset.

    The caller may pass its own client instead; this exists so "which URL" has one
    answer (`VALKEY_URL`, `08` §6) rather than one per call site.
    """
    if not limits.valkey_url:
        raise errors.InvalidRequest("VALKEY_URL is not configured")
    from valkey.asyncio import Valkey                # imported here: `08` §4 keeps the
    return Valkey.from_url(limits.valkey_url, **kw)  # request path free of heavy deps


# =========================================================================
# Lua
# =========================================================================
# Shared by every script: the nine declared keys, the packed-entry format and the two
# operations ("forget this entry", "which candidate would this kind hand out") that more
# than one script needs. Keeping them in one string keeps the scripts honest with each
# other - a selection rule that existed twice would drift once.
_PRELUDE = """
local META, PAYLOAD, ACKED, INFLIGHT, TAGS, CLOCK, REFS, COUNTERS, NS =
  KEYS[1], KEYS[2], KEYS[3], KEYS[4], KEYS[5], KEYS[6], KEYS[7], KEYS[8], KEYS[9]

-- Every score is written through this: Lua 5.1 stringifies numbers as %.14g, which
-- would round every fairness tag on its way to the ZSET. %.17g round-trips a double.
local function score_text(x) return string.format('%.17g', x) end
local function int_text(x) return string.format('%d', x) end

local function pending_key(flow) return NS .. ':pending:' .. flow end

-- entry = seq \\t available_at_us \\t visibility_us \\t kind \\t org \\t job_id \\t bytes
local function split(packed)
  local out, start = {}, 1
  while true do
    local cut = string.find(packed, '\\t', start, true)
    if not cut then out[#out + 1] = string.sub(packed, start); return out end
    out[#out + 1] = string.sub(packed, start, cut - 1)
    start = cut + 1
  end
end

local function entry(id)
  local packed = redis.call('HGET', META, id)
  if not packed then return nil end
  local f = split(packed)
  return {seq = tonumber(f[1]), avail = tonumber(f[2]), ttl = tonumber(f[3]),
          kind = f[4], org = f[5], job = f[6], size = tonumber(f[7]),
          flow = f[4] .. '|' .. f[5]}
end

local function score(key, member)
  local raw = redis.call('ZSCORE', key, member)
  if raw == false or raw == nil then return 0 end
  return tonumber(raw)
end

-- Drop one candidate and, with it, any fairness state it was the last reason for: the
-- flow disappears exactly when its pending AND in-flight count reaches zero.
local function forget(id)
  local e = entry(id)
  if not e then return 0 end
  redis.call('ZREM', pending_key(e.flow), id)
  redis.call('ZREM', INFLIGHT, id)
  redis.call('HDEL', META, id)
  redis.call('HDEL', PAYLOAD, id)
  redis.call('HINCRBY', COUNTERS, 'bytes', int_text(-e.size))
  if redis.call('HINCRBY', REFS, e.flow, -1) <= 0 then
    redis.call('HDEL', REFS, e.flow)
    redis.call('ZREM', TAGS, e.flow)
  end
  return 1
end

-- Level 2: the fair choice inside one kind - the smallest (tag, arrival sequence) over
-- that kind's flows with an available candidate. A flow's head may be a candidate that
-- is not available yet, so the scan walks the flow in arrival order until it finds one.
local function pick_in_kind(kind, now)
  local best = nil
  local flows = redis.call('ZRANGE', TAGS, 0, -1, 'WITHSCORES')
  for i = 1, #flows, 2 do
    local flow, tag = flows[i], tonumber(flows[i + 1])
    if string.sub(flow, 1, #kind + 1) == kind .. '|' then
      local queued = redis.call('ZRANGE', pending_key(flow), 0, -1)
      for j = 1, #queued do
        local e = entry(queued[j])
        if e and e.avail <= now then
          if best == nil or tag < best.tag
             or (tag == best.tag and e.seq < best.seq) then
            best = {id = queued[j], flow = flow, tag = tag, seq = e.seq, kind = kind}
          end
          break
        end
      end
    end
  end
  return best
end
"""

# --- enqueue -------------------------------------------------------------
# Checks in the pinned order: duplicate (pending, in flight or acknowledged) -> item cap
# -> byte cap, both caps BEFORE any write, so a refusal leaves no half-indexed candidate
# holding a cap slot for ever.
_ENQUEUE = _PRELUDE + """
local id, payload, avail, ttl, kind, org, job = ARGV[1], ARGV[2], ARGV[3], ARGV[4],
                                                ARGV[5], ARGV[6], ARGV[7]
local max_items, max_bytes = tonumber(ARGV[8]), tonumber(ARGV[9])
if redis.call('HEXISTS', META, id) == 1 then return 0 end
if redis.call('SISMEMBER', ACKED, id) == 1 then return 0 end
local size = #payload
if redis.call('HLEN', META) + 1 > max_items then return -1 end
local bytes = tonumber(redis.call('HGET', COUNTERS, 'bytes') or '0')
if bytes + size > max_bytes then return -2 end
local seq = redis.call('HINCRBY', COUNTERS, 'seq', 1)
redis.call('HSET', META, id, table.concat({int_text(seq), avail, ttl, kind, org, job,
                                           int_text(size)}, '\\t'))
redis.call('HSET', PAYLOAD, id, payload)
redis.call('HINCRBY', COUNTERS, 'bytes', int_text(size))
local flow = kind .. '|' .. org
if redis.call('HINCRBY', REFS, flow, 1) == 1 then
  -- An arriving flow's tag is ITS OWN kind's virtual time (never the highest of any
  -- kind, never zero): a tenant that was absent starts level with the others instead of
  -- with a credit for every dispatch it missed. The score is copied as the string Valkey
  -- stored, so not even a parse can move it.
  local vt = redis.call('ZSCORE', CLOCK, 'vt|' .. kind)
  if vt == false or vt == nil then vt = '0' end
  redis.call('ZADD', TAGS, vt, flow)
end
redis.call('ZADD', pending_key(flow), int_text(seq), id)
return 1
"""

# --- claim ---------------------------------------------------------------
# One call does three things atomically: lazy visibility expiry across BOTH kinds, the
# two-level R60 selection, and - only when the caller has already priced this exact
# candidate - the fairness writes plus the move into flight.
_CLAIM = _PRELUDE + """
local now = tonumber(ARGV[1])
local kind_filter, expected, cost = ARGV[2], ARGV[3], tonumber(ARGV[4])
local kinds = {}
for k in string.gmatch(ARGV[5], '[^,]+') do kinds[#kinds + 1] = k end
local weights = {}
for i = 6, #ARGV, 2 do weights[ARGV[i]] = tonumber(ARGV[i + 1]) end

-- Visibility is a timeout measured from the claim (30 s preparation, 120 s inference),
-- evaluated lazily here and back at exactly the TTL: the deadline score is <= now.
local due = redis.call('ZRANGEBYSCORE', INFLIGHT, '-inf', int_text(now))
for i = 1, #due do
  local e = entry(due[i])
  if e then
    -- back at its ORIGINAL arrival sequence, so FIFO inside the flow survives a lost
    -- worker without a re-sort
    redis.call('ZADD', pending_key(e.flow), int_text(e.seq), due[i])
  end
  redis.call('ZREM', INFLIGHT, due[i])
end

local pick, top_level = nil, (kind_filter == '')
if not top_level then
  pick = pick_in_kind(kind_filter, now)
else
  -- Level 1 (R60): the kinds are flows in their own right, weight 1, ranked by
  -- (kind tag, arrival sequence of the candidate their OWN level-2 rule would hand
  -- out). A kind with no eligible pick is skipped and left untouched.
  local best = nil
  for i = 1, #kinds do
    local candidate = pick_in_kind(kinds[i], now)
    if candidate then
      local ktag = score(CLOCK, 'kt|' .. kinds[i])
      if best == nil or ktag < best.ktag
         or (ktag == best.ktag and candidate.seq < best.seq) then
        candidate.ktag = ktag
        best = candidate
      end
    end
  end
  pick = best
end
if pick == nil then return {0, '', ''} end
local payload = redis.call('HGET', PAYLOAD, pick.id)
-- The caller prices the candidate between the probe and the commit, because the service
-- estimator is an injected Python collaborator; a bad answer must be a typed refusal
-- with the index untouched, so nothing below this line runs until the cost is validated.
if expected == '' or expected ~= pick.id then return {1, pick.id, payload} end

if top_level then
  -- level 1 writes, BEFORE the level-2 writes, no weight
  local top_start = pick.ktag
  local top = score(CLOCK, 'top')
  if top > top_start then top_start = top end
  redis.call('ZADD', CLOCK, score_text(top_start), 'top')
  redis.call('ZADD', CLOCK, score_text(top_start + cost), 'kt|' .. pick.kind)
end
-- level 2: start-time fair queuing inside the kind
local vt = score(CLOCK, 'vt|' .. pick.kind)
local start = pick.tag
if vt > start then start = vt end
redis.call('ZADD', CLOCK, score_text(start), 'vt|' .. pick.kind)
local org = string.sub(pick.flow, #pick.kind + 2)
local weight = weights[org]
if weight == nil then weight = 1 end
redis.call('ZADD', TAGS, score_text(start + cost / weight), pick.flow)
local e = entry(pick.id)
redis.call('ZREM', pending_key(pick.flow), pick.id)
redis.call('ZADD', INFLIGHT, int_text(now + e.ttl), pick.id)
return {2, pick.id, payload}
"""

# --- acknowledge ---------------------------------------------------------
_ACK = _PRELUDE + """
forget(ARGV[1])
redis.call('SADD', ACKED, ARGV[1])
return 1
"""

# --- remove (cancellation) ----------------------------------------------
# Every candidate of the job, pending or in flight, and deliberately NOT remembered: a
# replayed dispatch event may be re-indexed, and `JobStore.claim` refuses it. Remembering
# it would make the index authoritative about cancellation.
_REMOVE = _PRELUDE + """
local job, dropped = ARGV[1], 0
local all = redis.call('HGETALL', META)
for i = 1, #all, 2 do
  local f = split(all[i + 1])
  if f[6] == job then dropped = dropped + forget(all[i]) end
end
return dropped
"""

# --- rebuild -------------------------------------------------------------
# PostgreSQL's truth replaces the index: the de-duplicated snapshot in snapshot order,
# caps NOT applied (a rebuild is recovery), in flight dropped, acknowledged cleared, both
# fairness levels and the arrival sequence restarted.
_REBUILD = _PRELUDE + """
local flows = redis.call('ZRANGE', TAGS, 0, -1)
for i = 1, #flows do redis.call('DEL', pending_key(flows[i])) end
redis.call('DEL', META, PAYLOAD, ACKED, INFLIGHT, TAGS, CLOCK, REFS, COUNTERS)
for i = 1, #ARGV, 7 do
  local id, payload = ARGV[i], ARGV[i + 1]
  if redis.call('HEXISTS', META, id) == 0 then
    local kind, org = ARGV[i + 4], ARGV[i + 5]
    local size = #payload
    local seq = redis.call('HINCRBY', COUNTERS, 'seq', 1)
    redis.call('HSET', META, id, table.concat({int_text(seq), ARGV[i + 2], ARGV[i + 3],
                                               kind, org, ARGV[i + 6],
                                               int_text(size)}, '\\t'))
    redis.call('HSET', PAYLOAD, id, payload)
    redis.call('HINCRBY', COUNTERS, 'bytes', int_text(size))
    local flow = kind .. '|' .. org
    if redis.call('HINCRBY', REFS, flow, 1) == 1 then
      redis.call('ZADD', TAGS, '0', flow)
    end
    redis.call('ZADD', pending_key(flow), int_text(seq), id)
  end
end
return redis.call('HLEN', META)
"""

# --- observability -------------------------------------------------------
# One read-only script for the whole snapshot, so `stats()` and the fairness state are
# taken at one instant. It performs NO expiry: `inflight` still counts entries whose
# visibility has expired until the next claim, exactly as the memory adapter reports it.
_STATE = _PRELUDE + """
local now = tonumber(ARGV[1])
local inflight = redis.call('ZRANGE', INFLIGHT, 0, -1)
local held = {}
for i = 1, #inflight do held[inflight[i]] = true end
local depth, oldest, items = {}, nil, 0
local all = redis.call('HGETALL', META)
for i = 1, #all, 2 do
  local f = split(all[i + 1])
  items = items + 1
  depth[f[4]] = (depth[f[4]] or 0) + 1
  if not held[all[i]] then
    local avail = tonumber(f[2])
    if avail <= now and (oldest == nil or avail < oldest) then oldest = avail end
  end
end
local depth_flat = {}
for kind, count in pairs(depth) do
  depth_flat[#depth_flat + 1] = kind
  depth_flat[#depth_flat + 1] = int_text(count)
end
local wait = 0
if oldest ~= nil then wait = (now - oldest) / 1000000 end
return {redis.call('ZRANGE', TAGS, 0, -1, 'WITHSCORES'),
        redis.call('ZRANGE', CLOCK, 0, -1, 'WITHSCORES'),
        depth_flat,
        {int_text(items), int_text(#inflight),
         int_text(redis.call('SCARD', ACKED)), int_text(redis.call('ZCARD', TAGS)),
         redis.call('HGET', COUNTERS, 'bytes') or '0', score_text(wait)}}
"""


class ValkeyScheduler:
    """`ports.Scheduler` in Valkey: the memory adapter's semantics, atomically.

    Collaborators are injected exactly as they are for `MemoryScheduler` - `now` is the
    clock, `weights` the per-org WFQ weight, `cost` the predicted service time - so the
    two adapters can be driven by one differential script. `namespace` isolates one
    index from another inside one server (a test, a second gateway, a drain-and-switch
    migration); it carries a `{…}` hash tag so every derived key shares its slot.

    The observability accessors are `async` here because they are server reads; the
    memory adapter's are synchronous. Nothing in `ports.Scheduler` covers them.
    """

    def __init__(self, client: Any, now: Callable[[], datetime], *,
                 namespace: str = "infrx:sched:{pilot}",
                 limits: PilotSettings = DEFAULTS,
                 weights: Mapping[str, float] | None = None,
                 cost: Callable[[IndexEvent], float] = _one_second,
                 max_items: int | None = None,
                 max_bytes: int | None = None) -> None:
        self.client = client
        self._now = now
        self.namespace = namespace
        self.limits = limits
        self._weights = dict(weights or {})
        for org_id, weight in self._weights.items():
            # A non-positive or non-finite weight is a division by zero (or a NaN that
            # poisons every comparison) in the middle of dispatch. Refuse it here, where
            # it is configured, not in Lua where the index is already moving.
            if not (weight > 0) or weight == float("inf"):
                raise ValueError(f"weight for {org_id} must be finite and positive: {weight!r}")
        self._cost = cost
        self._max_items = int(max_items if max_items is not None
                              else getattr(limits, _ITEMS_FIELD, MAX_INDEX_ITEMS))
        self._max_bytes = int(max_bytes if max_bytes is not None
                              else getattr(limits, _BYTES_FIELD, MAX_INDEX_BYTES))
        self._keys = [f"{namespace}:{name}" for name in
                      ("meta", "payload", "acked", "inflight", "tags", "clock", "refs",
                       "counters")] + [namespace]
        self._enqueue = client.register_script(_ENQUEUE)
        self._claim = client.register_script(_CLAIM)
        self._ack = client.register_script(_ACK)
        self._remove = client.register_script(_REMOVE)
        self._rebuild = client.register_script(_REBUILD)
        self._state = client.register_script(_STATE)
        self._kinds = ",".join(kind.value for kind in DISPATCH_KINDS)

    # --- port ---------------------------------------------------------------
    async def enqueue(self, event: IndexEvent) -> bool:
        """Replay safe: the same stable event id indexes exactly one candidate.

        A full index raises `capacity_exhausted` (with `Retry-After`) rather than
        dropping the candidate, and the cap is evaluated *inside* the script against the
        server's own count and byte total - two gateway processes sharing one index
        therefore share one bound, which a per-process check would not give.
        """
        code = int(await self._call(self._enqueue, *self._event_args(event),
                                   self._max_items, self._max_bytes))
        if code == -1:
            raise errors.CapacityExhausted(
                f"the scheduling index holds its maximum of {self._max_items} candidates")
        if code == -2:
            raise errors.CapacityExhausted(
                f"the scheduling index holds its maximum of {self._max_bytes} queued bytes")
        return code == 1

    async def claim_candidate(self, worker_id: str, *,
                              kind: OutboxKind | None = None) -> IndexEvent | None:
        """The next candidate under the two-level fair order, or `None`.

        Select-then-commit: the first call expires visibilities, selects under R60 and
        returns the candidate *without* moving any fairness state; the injected estimator
        then prices that candidate (a bad answer is a typed refusal with nothing moved);
        the second call commits only if the same candidate is still the fair choice, and
        hands back the new winner to be priced if it is not. Each call is one atomic
        script, so no half-claim exists at any instant.

        ponytail: two round trips per claim, and under concurrent claimers a retry loop
        bounded by `_CLAIM_ATTEMPTS`. One round trip needs the cost inside the script,
        which means storing a per-candidate cost at enqueue - worth doing when Q3's
        estimator is real and a profile says the second hop matters, not before.
        """
        if kind is not None and kind not in DISPATCH_KINDS:
            # R55: an unknown kind is a caller bug. Answering `None` would report it as
            # "the index is empty", and a pool with a misspelled kind would idle for ever
            # against a full queue and look healthy doing it.
            raise errors.InvalidRequest(
                f"{kind!r} is not a dispatch kind; the index carries "
                f"{', '.join(k.value for k in DISPATCH_KINDS)}")
        now = _us(self._now())
        filter_arg = "" if kind is None else kind.value
        weights = [part for org_id, weight in self._weights.items()
                   for part in (org_id, repr(weight))]
        expected, cost = "", 1.0
        for _attempt in range(_CLAIM_ATTEMPTS):
            state, event_id, payload = await self._call(
                self._claim, now, filter_arg, expected, repr(cost), self._kinds, *weights)
            if int(state) == 0:
                return None
            event = IndexEvent.model_validate_json(payload)
            if int(state) == 2:
                return event
            cost = self._service_cost(event)         # validated before anything moves
            expected = _text(event_id)
        raise errors.InternalError(                  # pragma: no cover - needs a livelock
            f"the fair choice changed under {_CLAIM_ATTEMPTS} priced claims")

    async def acknowledge(self, event: IndexEvent) -> None:
        """Done with it: the candidate is gone for good, and its id is remembered so a
        replayed outbox event cannot re-index it. The job's truth is in PostgreSQL, so
        the acknowledgment erases no durable fact."""
        await self._call(self._ack, event.event_id)

    async def remove(self, job_id: str) -> None:
        """Cancellation: every candidate of this job, pending or in flight."""
        await self._call(self._remove, job_id)

    async def rebuild(self, snapshot: tuple[IndexEvent, ...]) -> int:
        """Rebuild from PostgreSQL truth; returns the number of candidates indexed.

        Same semantics the memory adapter pins: `pending` becomes the de-duplicated
        snapshot in snapshot order, the caps are not applied, in-flight entries are
        dropped, acknowledged ids are cleared, and both fairness levels restart - so no
        tenant and no *kind* inherits a penalty from an index that no longer exists.

        ponytail: the whole snapshot crosses in one command's arguments, so a very large
        recovery is bounded by the server's query buffer rather than by anything here.
        Chunking it would cost the single-call atomicity that makes a rebuild
        all-or-nothing, so the chunk-and-fence version belongs to Q3's reconciler - which
        knows how many non-terminal jobs there are - if it ever needs one.
        """
        args: list[Any] = []
        for event in snapshot:
            args.extend(self._event_args(event))
        return int(await self._call(self._rebuild, *args))

    # --- observability ------------------------------------------------------
    async def snapshot(self) -> dict[str, Any]:
        """Every published number at one instant, in one round trip.

        The differential harness compares this whole dict against the memory adapter
        after every operation; the five accessors below are the same reply, sliced.
        """
        flows, clock, depth, totals = await self._call(self._state, _us(self._now()))
        tags = {tuple(_text(flows[i]).split("|", 1)): float(flows[i + 1])
                for i in range(0, len(flows), 2)}
        levels = {_text(clock[i]): float(clock[i + 1]) for i in range(0, len(clock), 2)}
        by_kind = {_text(depth[i]): int(depth[i + 1]) for i in range(0, len(depth), 2)}
        items, inflight, acked, flow_count, queued_bytes, wait = (_text(v) for v in totals)
        return {
            "tags": tags,
            "virtual_times": {kind.value: levels.get(f"vt|{kind.value}", 0.0)
                              for kind in DISPATCH_KINDS},
            "kind_tags": {kind.value: levels.get(f"kt|{kind.value}", 0.0)
                          for kind in DISPATCH_KINDS},
            "top_virtual_time": levels.get("top", 0.0),
            "stats": {
                "items": int(items),
                "bytes": int(queued_bytes),
                "pending": int(items) - int(inflight),
                "inflight": int(inflight),
                "flows": int(flow_count),
                "acknowledged": int(acked),
                "depth_by_kind": {kind.value: by_kind.get(kind.value, 0)
                                  for kind in DISPATCH_KINDS},
                "oldest_wait_s": float(wait),
            },
        }

    async def depth(self) -> int:
        """Pending plus in flight, as the fake reports it."""
        return int((await self.snapshot())["stats"]["items"])

    async def stats(self) -> dict[str, object]:
        """Index depth, bytes and waiting age - never admission capacity."""
        return (await self.snapshot())["stats"]

    async def tags(self) -> dict[tuple[str, str], float]:
        """The live fairness state, keyed `(kind, org_id)` as Q1 reports it."""
        return (await self.snapshot())["tags"]

    async def virtual_times(self) -> dict[str, float]:
        """One virtual time per dispatch kind (r2 B2)."""
        return (await self.snapshot())["virtual_times"]

    async def kind_tags(self) -> dict[str, float]:
        """R60 level-1 state: one tag per kind, moved only by unfiltered claims."""
        return (await self.snapshot())["kind_tags"]

    async def top_virtual_time(self) -> float:
        """R60 level-1 virtual time, moved only by unfiltered claims."""
        return (await self.snapshot())["top_virtual_time"]

    # --- internals ----------------------------------------------------------
    async def _call(self, script: Any, *args: Any) -> Any:
        return await script(keys=self._keys, args=[str(arg) for arg in args])

    def _event_args(self, event: IndexEvent) -> Sequence[Any]:
        """One candidate as the scripts take it: identity, canonical bytes (whose length
        is what the byte cap charges), and the two instants pre-resolved to integer
        microseconds so no comparison inside Lua touches a float."""
        visibility = (self.limits.preparation_lease_ttl_s if event.is_preparation
                      else self.limits.lease_ttl_s)   # R52: the pool's own lease
        return (event.event_id, compact_bytes(event).decode(), _us(event.available_at),
                _seconds_us(visibility), event.kind.value, event.org_id, event.job_id)

    def _service_cost(self, event: IndexEvent) -> float:
        """The injected estimator's answer, validated before anything moves.

        Deliberately a copy of `MemoryScheduler._service_cost` rather than a shared
        helper: the two adapters must refuse identically, and `memory.py` is Q1's frozen
        reference (its mutation list anchors on its exact lines).
        """
        try:
            cost = self._cost(event)
        except errors.DomainError:
            raise
        except Exception as exc:                  # an estimator bug is ours, not a caller's
            raise errors.InternalError("the service-cost estimator raised") from exc
        if not (cost > 0) or cost == float("inf"):
            raise errors.InternalError(
                f"service cost must be finite and positive: {cost!r}")
        return cost
