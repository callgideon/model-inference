#!/usr/bin/env python3
"""W1 / API-STREAM: the reasoning filter is correct across arbitrary chunk boundaries.

    uv run --frozen pytest -q tests/w/test_reasoning.py

The property test is the point of this file: for a seeded corpus, **every** way of
splitting each text into pieces must produce the same filtered output as the
unsplit text. That is the invariant F1's per-chunk regex could not hold, and a
delimiter split as `<th` + `ink>` is not a rare case - it is what a token stream
looks like.
"""
from __future__ import annotations

import json
import re
from itertools import combinations

from infrx.worker.engine import _json_cost
from infrx.worker.reasoning import ReasoningFilter, filter_text


def oracle(text: str) -> str:
    """An **independent** implementation of the same rule: F1's leading-block regex plus
    the two rules the streaming filter adds (an unclosed block never becomes visible; the
    whitespace around the block goes with it).

    Independent on purpose - a property test whose oracle is the code under test only
    proves the code is deterministic. This one is a regex and a `find`, not a state
    machine, so a bug would have to occur twice in two shapes to hide here.
    """
    opened = re.match(r"^\s*<think>", text)
    if opened is None:
        return text
    rest = text[opened.end():]
    closed = rest.find("</think>")
    if closed < 0:
        return ""
    return rest[closed + len("</think>"):].lstrip()

# Nasty on purpose: delimiters at the start, in the middle, nested, unclosed, split
# into their own characters, and text that merely contains the word.
CORPUS = (
    "",
    "hello",
    "  ",
    "<think>r</think>a",
    "  <think>r</think>\n a",
    "<think>r</think>",
    "<think></think>a",
    "<think>a<think>b</think>c",
    "<think>unclosed",
    "<think>",
    "<thi",
    "a<think>b</think>c",
    "</think>x",
    "<think>the van is stationary</think>Two people unload boxes.",
    "\n\n<think>why</think>\n\nans",
    "no delimiter here at all",
    "< think>x</think>y",
    "<think>r</think>a<think>b</think>",
    "<THINK>x</THINK>y",
    "<think>r</think><think>b</think>c",
    "\t<think>r</think>\t\tanswer",
)
# All 2**(n-1) splits for texts up to `EXHAUSTIVE_MAX` characters; for the longer ones
# every split into at most four pieces (three cuts), which still puts a boundary inside
# both delimiters and on either side of them.
EXHAUSTIVE_MAX = 16
LONG_TEXT_CUTS = (0, 1, 2, 3)


def splits(text: str):
    """Every way of cutting `text` into consecutive pieces (including no cut)."""
    n = len(text)
    positions = range(1, n)
    cut_counts = range(0, n) if n <= EXHAUSTIVE_MAX else LONG_TEXT_CUTS
    for count in cut_counts:
        for cuts in combinations(positions, count):
            bounds = (0, *cuts, n)
            yield tuple(text[bounds[i]:bounds[i + 1]] for i in range(len(bounds) - 1))


def filtered(pieces) -> str:
    reasoning = ReasoningFilter()
    return "".join(reasoning.feed(piece) for piece in pieces) + reasoning.close()


def test_api_stream__every_chunk_split_filters_to_the_same_text():
    """API-STREAM: for every split of every corpus entry the filtered stream equals what
    an **independent** oracle makes of the unsplit text. Chunk boundaries are
    unobservable, and the rule itself is checked against a second implementation."""
    total = 0
    for text in CORPUS:
        expected = oracle(text)
        assert filter_text(text) == expected, text
        for pieces in splits(text):
            total += 1
            assert "".join(pieces) == text, pieces          # the corpus itself is intact
            assert filtered(pieces) == expected, (text, pieces, expected)
    print(f"\nreasoning property: {total} splits over {len(CORPUS)} corpus entries")
    assert total > 10_000, total


def test_api_stream__one_code_point_costs_what_json_says_it_costs():
    """B6's arithmetic, against `json.dumps` itself over every branch: the per-code-point
    cost used to be a process-global memo table bounded only by Unicode (a million entries
    after enough varied text), and the four cases below are what the encoder really does
    under `ensure_ascii=False`, which is how the store's `compact_bytes` serializes."""
    points = [*range(0x00, 0x100), 0x2028, 0x2029, 0x7FF, 0x800, 0xFFFF, 0x1F600, 0x10FFFF,
              ord("日"), ord('"'), ord("\\")]
    for code in points:
        char = chr(code)
        expected = len(json.dumps(char, ensure_ascii=False).encode()) - 2   # minus the quotes
        assert _json_cost(char) == expected, (hex(code), _json_cost(char), expected)
    # and the whole of the BMP agrees, not only the interesting corners
    for code in range(0x20, 0x10000, 97):
        char = chr(code)
        if 0xD800 <= code <= 0xDFFF:                 # lone surrogates are not encodable
            continue
        assert _json_cost(char) == len(json.dumps(char, ensure_ascii=False).encode()) - 2


def test_api_stream__the_raw_text_is_never_modified():
    """API-STREAM / DEC-05: trace capture records the model's own text, so the filter
    must be a projection of the stream, not a rewrite of it."""
    text = "<think>reasoning</think>answer"
    for pieces in splits(text):
        reasoning = ReasoningFilter()
        raw = ""
        for piece in pieces:
            reasoning.feed(piece)
            raw += piece
        assert raw == text


def test_api_stream__only_a_leading_block_is_a_delimiter():
    """API-STREAM: a `<think>` after visible text is answer text (F1's `^`), the first
    `</think>` closes the block (F1's non-greedy group), and the whitespace around the
    block goes with it."""
    assert filter_text("<think>r</think>a") == "a"
    assert filter_text("  <think>r</think>\n\ta") == "a"
    assert filter_text("<think>a<think>b</think>c") == "c"
    assert filter_text("a<think>b</think>c") == "a<think>b</think>c"
    assert filter_text("</think>x") == "</think>x"
    assert filter_text("< think>x</think>y") == "< think>x</think>y"
    assert filter_text("hello") == "hello"
    # the delimiter is case sensitive, exactly as F1's regex was
    assert filter_text("<THINK>x</THINK>y") == "<THINK>x</THINK>y"
    assert filter_text("  ") == "  "
    assert filter_text("") == ""


def test_api_stream__an_unclosed_block_never_becomes_visible():
    """API-STREAM: reasoning that was never closed is still reasoning. Emitting it
    would show the customer the model's scratchpad, and the answer is honestly empty."""
    assert filter_text("<think>unclosed reasoning") == ""
    assert filter_text("<think>") == ""
    reasoning = ReasoningFilter()
    assert reasoning.feed("<thi") == ""
    assert reasoning.feed("nk>secret ") == ""
    assert reasoning.feed("plans") == ""
    assert reasoning.close() == ""


def test_api_stream__a_partial_delimiter_at_the_end_is_released():
    """API-STREAM: `<thi` that never completes is answer text, so `close` must give it
    back rather than swallow it."""
    reasoning = ReasoningFilter()
    assert reasoning.feed("<thi") == ""
    assert reasoning.close() == "<thi"
    reasoning = ReasoningFilter()
    assert reasoning.feed("  ") == ""
    assert reasoning.close() == "  "


def test_api_stream__the_close_delimiter_may_straddle_any_boundary():
    """API-STREAM: the state machine keeps only the few characters needed to recognise
    a split `</think>`, so a long block costs no memory and is still recognised."""
    reasoning = ReasoningFilter()
    assert reasoning.feed("<think>" + "x" * 100_000) == ""
    assert reasoning.feed("</thi") == ""
    assert reasoning.feed("nk>ans") == "ans"
    assert reasoning.close() == ""
