from abc import ABC, abstractmethod


class ModelAdapter(ABC):
    """Normalizes a raw Ollama chat completion into clean, model-agnostic
    content. Each local model has its own output quirks (reasoning blocks,
    special tokens, etc.) — swapping the model in `AI_SERVICE_OLLAMA_MODEL`
    means swapping the matching adapter in `AI_SERVICE_MODEL_ADAPTER` too.
    """

    @abstractmethod
    def normalize_response(self, content: str) -> str:
        ...
