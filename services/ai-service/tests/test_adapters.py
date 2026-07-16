import pytest

import app.adapters as adapters_module
from app.adapters import get_adapter
from app.adapters.qwen import QwenAdapter
from app.config import settings


@pytest.fixture(autouse=True)
def reset_adapter_singleton():
    yield
    adapters_module._adapter = None


def test_qwen_adapter_strips_think_block():
    adapter = QwenAdapter()
    raw = "<think>\nreasoning about the answer\n</think>\n\nHello, ARIA!"
    assert adapter.normalize_response(raw) == "Hello, ARIA!"


def test_qwen_adapter_passes_through_content_without_think_block():
    adapter = QwenAdapter()
    assert adapter.normalize_response("just a plain reply") == "just a plain reply"


def test_qwen_adapter_falls_back_to_raw_content_if_stripping_leaves_nothing():
    adapter = QwenAdapter()
    raw = "<think>\ncut off mid-thought, no closing tag or reply followed"
    assert adapter.normalize_response(raw) == raw.strip()


def test_get_adapter_returns_qwen_by_default():
    assert isinstance(get_adapter(), QwenAdapter)


def test_get_adapter_raises_on_unknown_adapter_name(monkeypatch):
    monkeypatch.setattr(settings, "model_adapter", "not-a-real-adapter")
    with pytest.raises(ValueError, match="Unknown AI_SERVICE_MODEL_ADAPTER"):
        get_adapter()
