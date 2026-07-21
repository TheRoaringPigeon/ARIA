import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime

import httpx

from app.config import settings
from app.lazy_singleton import LazySingleton

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    published_at: str | None = None


class SearchProvider(ABC):
    """Read-only web search, used by the Research Assistant's tool-choice
    loop (`agents/nodes.py::research_node`) alongside its existing document
    search. Swappable via `AI_SERVICE_SEARCH_PROVIDER`, mirroring
    `adapters/base.py::ModelAdapter`'s seam — no caller should ever
    depend on a concrete provider's shape.
    """

    @abstractmethod
    async def search(self, query: str, since: date | None = None) -> list[SearchResult]:
        """Degrades to `[]` on any failure (missing key, HTTP error,
        malformed response) — web search is additive to chat, never
        load-bearing, same contract as every other grounding path in this
        codebase.
        """
        ...


_client = LazySingleton(lambda: httpx.AsyncClient(base_url="https://api.search.brave.com", timeout=10.0))


def _get_client() -> httpx.AsyncClient:
    return _client.get()


class BraveSearchAdapter(SearchProvider):
    """Brave Web Search API — free tier, no local-dev friction beyond an
    API key. `since` is enforced as a post-filter, not a query param: Brave
    has no native date-range filter, but each result carries a `page_age`
    (ISO 8601) when the underlying page exposes one. A result with no
    `page_age` at all is kept rather than dropped — silently discarding
    every undated result (common for e.g. Wikipedia) would make `since`
    behave more like "only very recently published pages" than the
    intended "not stale/outdated" filter.
    """

    async def search(self, query: str, since: date | None = None) -> list[SearchResult]:
        if not settings.brave_search_api_key:
            return []
        try:
            resp = await _get_client().get(
                "/res/v1/web/search",
                params={"q": query, "count": settings.web_search_result_limit},
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": settings.brave_search_api_key,
                },
            )
            resp.raise_for_status()
            body = resp.json()
        except Exception:
            logger.warning("brave search failed, degrading to no web results", exc_info=True)
            return []

        results = []
        for item in (body.get("web") or {}).get("results", []):
            title = item.get("title")
            url = item.get("url")
            if not title or not url:
                continue
            published_at = item.get("page_age")
            if since is not None and published_at is not None:
                try:
                    if datetime.fromisoformat(published_at).date() < since:
                        continue
                except ValueError:
                    pass
            results.append(
                SearchResult(
                    title=title,
                    url=url,
                    snippet=item.get("description", ""),
                    published_at=published_at,
                )
            )
        return results
