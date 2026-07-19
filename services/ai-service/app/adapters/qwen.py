import json
import re
from typing import Sequence

from app.adapters.base import ModelAdapter, StreamChunkKind, StreamFilter

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)

_OPEN_TAG = "<think>"
_CLOSE_TAG = "</think>"

# Tolerates a single leading/trailing markdown code fence (with an optional
# "json" language tag) around a reply that's supposed to be a bare JSON
# object — a common instruction-tuned-model habit even when explicitly told
# "respond with ONLY a JSON object, no other text." Without this, a fenced
# reply silently parses as "no tool needed" — indistinguishable from a
# deliberate no-search decision.
_CODE_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


class QwenAdapter(ModelAdapter):
    """Qwen3 prefixes its reply with a <think>...</think> reasoning block —
    strip it, the UI only wants the final answer. Falls back to the raw
    content if stripping leaves nothing (e.g. the response was cut off
    mid-think), rather than returning an empty message.
    """

    def normalize_response(self, content: str) -> str:
        if _THINK_BLOCK.search(content):
            return _THINK_BLOCK.sub("", content).strip()
        return content.strip()

    def create_stream_filter(self) -> StreamFilter:
        return _QwenStreamFilter()

    def parse_choice(self, content: str, choices: Sequence[str]) -> str | None:
        """Picks whichever `choices` entry appears *earliest* in the
        model's own answer, not the first one enumerated in `choices` — a
        fixed iteration order would silently prefer an earlier-enumerated
        choice over one the model actually led with (e.g. a reply like
        "this is a vehicle maintenance question" naming both "maintenance"
        and "vehicle" — a realistic case here, since the supervisor
        prompt's own "maintenance" category explicitly mentions "vehicle"
        as one of the things it covers). Matches on a word boundary, not a
        raw substring — a choice name embedded in a longer word (e.g.
        "general" inside "Generally, I'd say...") used to false-positive
        ahead of the model's actual intended choice (caught in code
        review; `entity_grounding._word_boundary_match` already solves
        this same class of problem elsewhere in this codebase).
        """
        normalized = self.normalize_response(content).strip().lower()
        matches = []
        for choice in choices:
            pattern = re.compile(r"(?<!\w)" + re.escape(choice.lower()) + r"(?!\w)")
            match = pattern.search(normalized)
            if match:
                matches.append((match.start(), choice))
        return min(matches)[1] if matches else None

    def parse_tool_decision(self, content: str) -> dict:
        normalized = self.normalize_response(content).strip()
        normalized = _CODE_FENCE.sub("", normalized).strip()
        try:
            parsed = json.loads(normalized)
        except json.JSONDecodeError:
            return {"tool": None}
        return parsed if isinstance(parsed, dict) else {"tool": None}


class _QwenStreamFilter(StreamFilter):
    """Classifies streamed deltas as "thinking" (inside <think>...</think>)
    or "answer" (everything else) so the caller can show reasoning live in
    a temporary preview and discard it once the real answer starts.

    Three states, in order:
    - "detecting_open": buffering to decide whether the reply opens with
      "<think>" at all.
    - "thinking": inside the reasoning block. Text is released as
      "thinking" as soon as it's no longer possibly part of a split
      "</think>" tag — only the ambiguous tail (at most len("</think>") - 1
      characters) is ever held back.
    - "answering": passthrough — every delta is real answer content,
      returned unbuffered, aside from suppressing leading whitespace right
      after the think block (see `_emit_answer`).
    """

    def __init__(self) -> None:
        self._state = "detecting_open"
        self._buffer = ""
        # Qwen typically leaves a blank line between "</think>" and the
        # real answer, and Ollama streams roughly one token per delta — so
        # that whitespace usually arrives as its own separate chunk, not
        # bundled with "</think>". This flag suppresses leading whitespace
        # across as many "answering"-state feed() calls as it takes to see
        # real content, not just the one call where the tag was found.
        self._suppress_leading_whitespace = False

    def feed(self, delta: str) -> list[tuple[StreamChunkKind, str]]:
        if self._state == "answering":
            return self._emit_answer(delta)
        if self._state == "detecting_open":
            return self._feed_detecting_open(delta)
        return self._feed_thinking(delta)

    def _emit_answer(self, text: str) -> list[tuple[StreamChunkKind, str]]:
        if self._suppress_leading_whitespace:
            text = text.lstrip()
            if not text:
                return []
            self._suppress_leading_whitespace = False
        return [("answer", text)] if text else []

    def _feed_detecting_open(self, delta: str) -> list[tuple[StreamChunkKind, str]]:
        self._buffer += delta
        if self._buffer.startswith(_OPEN_TAG):
            remainder = self._buffer[len(_OPEN_TAG) :]
            self._state = "thinking"
            self._buffer = ""
            return self._feed_thinking(remainder)
        if _OPEN_TAG.startswith(self._buffer):
            # Still an unresolved prefix of "<think>" — need more input
            # before we know whether this reply opens with a think block.
            return []
        # Diverged: this reply doesn't open with a think block at all.
        self._state = "answering"
        emitted, self._buffer = self._buffer, ""
        return [("answer", emitted)] if emitted else []

    def _feed_thinking(self, delta: str) -> list[tuple[StreamChunkKind, str]]:
        self._buffer += delta
        close_idx = self._buffer.find(_CLOSE_TAG)
        if close_idx != -1:
            thinking_part = self._buffer[:close_idx]
            remainder = self._buffer[close_idx + len(_CLOSE_TAG) :]
            self._state = "answering"
            self._suppress_leading_whitespace = True
            self._buffer = ""
            segments: list[tuple[StreamChunkKind, str]] = []
            if thinking_part:
                segments.append(("thinking", thinking_part))
            segments.extend(self._emit_answer(remainder))
            return segments

        # No close tag yet — hold back only the suffix that could still
        # turn into "</think>" as more input arrives, release the rest.
        risky_len = 0
        for k in range(min(len(_CLOSE_TAG) - 1, len(self._buffer)), 0, -1):
            if self._buffer[-k:] == _CLOSE_TAG[:k]:
                risky_len = k
                break
        safe_len = len(self._buffer) - risky_len
        safe_part = self._buffer[:safe_len]
        self._buffer = self._buffer[safe_len:]
        return [("thinking", safe_part)] if safe_part else []

    def flush(self) -> list[tuple[StreamChunkKind, str]]:
        leftover, self._buffer = self._buffer, ""
        if not leftover:
            return []
        if self._state == "thinking":
            # Genuine reasoning text that just never got confirmation it
            # wasn't the start of "</think>" — still reasoning either way.
            return [("thinking", leftover)]
        # "detecting_open": never confirmed this was a think block at all.
        return [("answer", leftover)]
