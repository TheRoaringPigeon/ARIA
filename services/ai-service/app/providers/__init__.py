from app.config import settings
from app.providers.search import BraveSearchAdapter, SearchProvider, SearchResult
from app.providers.weather import DailyForecast, ForecastResult, OpenMeteoAdapter, WeatherProvider, WeatherResult

_SEARCH_PROVIDERS: dict[str, type[SearchProvider]] = {
    "brave": BraveSearchAdapter,
}
_WEATHER_PROVIDERS: dict[str, type[WeatherProvider]] = {
    "open_meteo": OpenMeteoAdapter,
}

_search_provider: SearchProvider | None = None
_weather_provider: WeatherProvider | None = None


def _instantiate(registry: dict[str, type], key: str, setting_name: str):
    """Shared by `get_search_provider`/`get_weather_provider` — same
    dict-lookup + KeyError-to-ValueError shape both used to hand-roll
    independently (caught in code review).
    """
    try:
        provider_cls = registry[key]
    except KeyError:
        raise ValueError(f"Unknown {setting_name} '{key}' — available: {sorted(registry)}") from None
    return provider_cls()


def get_search_provider() -> SearchProvider:
    global _search_provider
    if _search_provider is None:
        _search_provider = _instantiate(
            _SEARCH_PROVIDERS, settings.search_provider, "AI_SERVICE_SEARCH_PROVIDER"
        )
    return _search_provider


def get_weather_provider() -> WeatherProvider:
    global _weather_provider
    if _weather_provider is None:
        _weather_provider = _instantiate(
            _WEATHER_PROVIDERS, settings.weather_provider, "AI_SERVICE_WEATHER_PROVIDER"
        )
    return _weather_provider


__all__ = [
    "DailyForecast",
    "ForecastResult",
    "SearchProvider",
    "SearchResult",
    "WeatherProvider",
    "WeatherResult",
    "get_search_provider",
    "get_weather_provider",
]
