from abc import ABC, abstractmethod
from typing import Literal, Sequence

StreamChunkKind = Literal["thinking", "answer"]


class StreamFilter(ABC):
    """Per-request, stateful classifier for a model's streamed output.
    Reasoning content (e.g. a `<think>...</think>` block) and real answer
    content can arrive split across arbitrary delta boundaries, so this
    can't be done with a single end-of-string regex the way
    `ModelAdapter.normalize_response` does it — each `feed()` call may need
    to hold text back until enough of it has arrived to classify.
    """

    @abstractmethod
    def feed(self, delta: str) -> list[tuple[StreamChunkKind, str]]:
        """Feed the next raw content delta from the model. Returns zero,
        one, or two (kind, text) segments now safe to emit — two when a
        single delta straddles the boundary between reasoning and answer.
        """
        ...

    @abstractmethod
    def flush(self) -> list[tuple[StreamChunkKind, str]]:
        """Call once after the model signals completion. Returns any text
        still held back (e.g. a think block that never closed) so it's
        surfaced rather than silently dropped.
        """
        ...


class ModelAdapter(ABC):
    """Normalizes a raw Ollama chat completion into clean, model-agnostic
    content. Each local model has its own output quirks (reasoning blocks,
    special tokens, etc.) — swapping the model in `AI_SERVICE_OLLAMA_MODEL`
    means swapping the matching adapter in `AI_SERVICE_MODEL_ADAPTER` too.
    """

    @abstractmethod
    def normalize_response(self, content: str) -> str:
        ...

    @abstractmethod
    def create_stream_filter(self) -> StreamFilter:
        """Return a fresh StreamFilter instance. Filters are stateful and
        scoped to a single chat request — never share one across requests.
        """
        ...

    @abstractmethod
    def parse_choice(self, content: str, choices: Sequence[str]) -> str | None:
        """Extract which of `choices` the model's free-form reply names,
        tolerating per-model prose quirks (extra commentary, casing, hedge
        words). Returns whichever choice is named *earliest* in the reply,
        or `None` if none can be confidently identified. Model-specific
        parsing workarounds (e.g. avoiding native structured tool-calling
        because of a documented bug for this model) belong here, not in
        agent business logic (caught in code review — a Qwen3-specific
        workaround had been hand-rolled directly in `app/agents/nodes.py`,
        bypassing this adapter seam entirely).
        """
        ...

    @abstractmethod
    def parse_tool_decision(self, content: str) -> dict:
        """Parse a `{"tool": ..., "query": ...}`-shaped tool-call decision
        out of the model's free-form reply, tolerating per-model formatting
        quirks (e.g. a wrapping markdown code fence). Returns `{"tool":
        None}` if no valid decision can be parsed.
        """
        ...
