import re

from app.adapters.base import ModelAdapter

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)


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
