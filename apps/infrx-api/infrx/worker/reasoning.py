"""The reasoning-delimiter filter (API-STREAM).

One leading `<think>…</think>` block is the model's reasoning and is not the
customer's answer. F1 stripped it with a regex per chunk
(`gateway/routes/chat.py`, "the streaming strip heuristic"), which is only correct
when the whole delimiter arrives inside one delta. A real engine splits tokens
wherever it likes: `<th` + `ink>the van is` is the same stream.

So the filter is a small state machine over the concatenation, fed piece by piece:

* `feed(piece)` returns the visible part *of that piece*, holding back only what might
  still turn out to be a delimiter: the leading whitespace plus at most six characters of
  a possible `<think>`, or at most seven of a possible `</think>` (one short of the
  delimiter, since a complete one is recognised immediately);
* `close()` returns whatever was held back and turned out not to be one, and r1 R58 makes
  that tail part of the customer's text: the adapter emits it as a final delta, so a
  streaming consumer sees the whole answer and not only `EngineStream.visible_text`;
* the raw text is never modified - the caller keeps it for trace capture.

The property that matters is that the chunk boundaries cannot be observed:
`"".join(feed(p) for p in parts) + close()` is the same string for every way of
splitting one text. `tests/w/test_reasoning.py` proves it over every split of a
seeded corpus.

Deliberate semantics, each one a case in that test:

* **Only a leading block is a delimiter.** `"a <think>b</think>"` is answer text
  that happens to contain the word; it passes through untouched. Leading
  whitespace before `<think>` goes with the block (F1's `^\\s*`), and so does the
  whitespace right after `</think>` (F1's trailing `\\s*`).
* **The first `</think>` closes it**, so a nested `<think>` inside the block is
  just more reasoning (F1's non-greedy `.*?</think>`).
* **The delimiter is case sensitive**, exactly as F1's regex was: `<THINK>` is answer
  text, because it is not the token the model was trained to emit.
* **An unclosed block never becomes visible.** If the stream ends inside the
  reasoning the answer is empty - honest, and usually paired with
  `finish_reason=length`. F1's non-stream regex leaks the remainder instead; that
  path is G's to retire.
"""
from __future__ import annotations

OPEN = "<think>"
CLOSE = "</think>"


class ReasoningFilter:
    """Stateful, single request, not thread safe: one per generation."""

    __slots__ = ("_held", "_tail", "_decided", "_reasoning", "_eat_ws")

    def __init__(self) -> None:
        self._held = ""          # a possible `<think>`, plus the whitespace before it
        self._tail = ""          # the last characters of dropped reasoning, for a split `</think>`
        self._decided = False    # is the leading-block question settled?
        self._reasoning = False
        self._eat_ws = False     # whitespace right after `</think>` belongs to the delimiter

    def feed(self, piece: str) -> str:
        """The visible part of `piece`, which may be empty."""
        if not piece:
            return ""
        if self._reasoning:
            return self._drop(piece)
        if self._decided:
            return self._visible(piece)
        buf = self._held + piece
        stripped = buf.lstrip()
        if not stripped:                       # whitespace only: it may still precede `<think>`
            self._held = buf
            return ""
        if stripped.startswith(OPEN):
            self._held, self._decided, self._reasoning = "", True, True
            return self._drop(stripped[len(OPEN):])
        if OPEN.startswith(stripped):          # a proper prefix: it may still complete
            self._held = buf
            return ""
        self._held, self._decided = "", True   # it was never a delimiter: emit it whole
        return buf

    def close(self) -> str:
        """Whatever was held back and is not a delimiter after all."""
        held, self._held, self._tail = self._held, "", ""
        if self._reasoning:
            return ""                          # unclosed reasoning stays reasoning
        self._decided = True
        return held

    # --- states ---------------------------------------------------------------
    def _drop(self, text: str) -> str:
        """Inside the block: drop everything up to the first `</think>`."""
        buf = self._tail + text
        found = buf.find(CLOSE)
        if found < 0:
            # Only enough to recognise a delimiter straddling this boundary, so a long
            # reasoning block costs O(1) memory rather than being buffered whole.
            self._tail = buf[-(len(CLOSE) - 1):]
            return ""
        self._tail, self._reasoning, self._eat_ws = "", False, True
        return self._visible(buf[found + len(CLOSE):])

    def _visible(self, text: str) -> str:
        if self._eat_ws:
            text = text.lstrip()
            if not text:
                return ""
            self._eat_ws = False
        return text


def filter_text(text: str) -> str:
    """The whole answer at once: the oracle the streaming case is compared against."""
    reasoning = ReasoningFilter()
    return reasoning.feed(text) + reasoning.close()
