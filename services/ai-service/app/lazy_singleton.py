import asyncio
from collections.abc import Awaitable, Callable
from typing import Generic, TypeVar

T = TypeVar("T")


class AsyncLazySingleton(Generic[T]):
    """Builds a value once, on first `get()`, and reuses it across every
    later call — the double-checked-locking shape `chroma.py` and
    `agents/graph.py` used to each hand-roll independently (caught in code
    review). `build` runs at most once even under concurrent first callers,
    guarded by a lock scoped to this instance.
    """

    def __init__(self, build: Callable[[], Awaitable[T]]) -> None:
        self._build = build
        self._value: T | None = None
        self._lock = asyncio.Lock()

    async def get(self) -> T:
        if self._value is None:
            async with self._lock:
                if self._value is None:
                    self._value = await self._build()
        return self._value
