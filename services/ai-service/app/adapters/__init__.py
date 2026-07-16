from app.adapters.base import ModelAdapter
from app.adapters.qwen import QwenAdapter
from app.config import settings

_ADAPTERS: dict[str, type[ModelAdapter]] = {
    "qwen": QwenAdapter,
}

_adapter: ModelAdapter | None = None


def get_adapter() -> ModelAdapter:
    global _adapter
    if _adapter is None:
        try:
            adapter_cls = _ADAPTERS[settings.model_adapter]
        except KeyError:
            raise ValueError(
                f"Unknown AI_SERVICE_MODEL_ADAPTER '{settings.model_adapter}' — "
                f"available: {sorted(_ADAPTERS)}"
            ) from None
        _adapter = adapter_cls()
    return _adapter
