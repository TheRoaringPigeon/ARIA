from abc import ABC, abstractmethod
from typing import Literal

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

    def feed_thinking(self, thinking: str) -> list[tuple[StreamChunkKind, str]]:
        """Feed the next delta of a model/server's *native* reasoning field
        (Ollama's `message.thinking`, as opposed to a `<think>...</think>`
        block inlined in `content` and detected by `feed()`'s tag-sniffing)
        — kept in this seam, not the router, since which mechanism a given
        model/server combination uses is exactly the kind of per-model
        output quirk `ModelAdapter` exists to isolate. Default: pass
        through verbatim as a single "thinking" segment; override only if a
        combination needs different handling (e.g. multiple named
        reasoning segments).
        """
        return [("thinking", thinking)] if thinking else []


class ModelAdapter(ABC):
    """Normalizes a raw Ollama chat completion into clean, model-agnostic
    content. Each local model has its own output quirks (reasoning blocks,
    special tokens, etc.) — swapping the model in `AI_SERVICE_OLLAMA_MODEL`
    means swapping the matching adapter in `AI_SERVICE_MODEL_ADAPTER` too.

    This used to also own `parse_choice()`/`parse_tool_decision()` —
    per-model workarounds for extracting a decision out of a free-form
    reply, needed because Ollama's native `tools` param has a documented
    bug against Qwen3 (ollama/ollama#14601). Both were removed once every
    internal routing/tool-choice call site moved to
    `ollama.complete_structured()` (scaling-debt.md #15) — Ollama's
    `format` field grammar-constrains decoding to a JSON Schema
    model-agnostically, making a per-model free-text parsing seam
    unnecessary for that use case entirely. What's left here
    (`normalize_response`/`create_stream_filter`) is genuinely still
    model-specific: the user-facing streamed answer is real freeform prose,
    not schema-constrained.
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
