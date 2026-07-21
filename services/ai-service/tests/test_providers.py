from datetime import date

import httpx
import pytest

import app.providers as providers_module
import app.providers.search as search_module
import app.providers.weather as weather_module
from app.config import settings
from app.providers.search import BraveSearchAdapter
from app.providers.weather import OpenMeteoAdapter


class FakeResponse:
    def __init__(self, body, status_error=None):
        self._body = body
        self._status_error = status_error

    def raise_for_status(self):
        if self._status_error:
            raise self._status_error

    def json(self):
        return self._body


class FakeGetClient:
    """Single-endpoint fake — `BraveSearchAdapter` makes exactly one call."""

    def __init__(self, response):
        self._response = response
        self.calls = []

    async def get(self, path, params=None, headers=None):
        self.calls.append({"path": path, "params": params, "headers": headers})
        return self._response


class FakeSequentialClient:
    """Returns one queued response per call, in order — for
    `OpenMeteoAdapter`, which calls geocoding then forecast.
    """

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def get(self, url, params=None):
        self.calls.append({"url": url, "params": params})
        return self._responses.pop(0)


# --- BraveSearchAdapter -------------------------------------------------


async def test_brave_search_returns_empty_without_api_key(monkeypatch):
    monkeypatch.setattr(settings, "brave_search_api_key", "")
    fake_client = FakeGetClient(FakeResponse({}))
    monkeypatch.setattr(search_module, "_get_client", lambda: fake_client)

    result = await BraveSearchAdapter().search("Acme Corp news")

    assert result == []
    assert fake_client.calls == []


async def test_brave_search_parses_results(monkeypatch):
    monkeypatch.setattr(settings, "brave_search_api_key", "test-key")
    body = {
        "web": {
            "results": [
                {
                    "title": "Acme Corp raises Series B",
                    "url": "https://example.com/acme",
                    "description": "Acme Corp announced funding.",
                    "page_age": "2026-07-18T00:00:00",
                }
            ]
        }
    }
    fake_client = FakeGetClient(FakeResponse(body))
    monkeypatch.setattr(search_module, "_get_client", lambda: fake_client)

    result = await BraveSearchAdapter().search("Acme Corp")

    assert len(result) == 1
    assert result[0].title == "Acme Corp raises Series B"
    assert result[0].url == "https://example.com/acme"
    assert result[0].snippet == "Acme Corp announced funding."
    assert fake_client.calls[0]["headers"]["X-Subscription-Token"] == "test-key"


async def test_brave_search_drops_results_older_than_since(monkeypatch):
    monkeypatch.setattr(settings, "brave_search_api_key", "test-key")
    body = {
        "web": {
            "results": [
                {"title": "Old news", "url": "https://example.com/old", "page_age": "2020-01-01"},
                {"title": "Recent news", "url": "https://example.com/new", "page_age": "2026-07-19"},
                {"title": "Undated", "url": "https://example.com/undated"},
            ]
        }
    }
    fake_client = FakeGetClient(FakeResponse(body))
    monkeypatch.setattr(search_module, "_get_client", lambda: fake_client)

    result = await BraveSearchAdapter().search("Acme Corp", since=date(2026, 7, 1))

    titles = {r.title for r in result}
    assert titles == {"Recent news", "Undated"}


async def test_brave_search_degrades_to_empty_on_http_error(monkeypatch):
    monkeypatch.setattr(settings, "brave_search_api_key", "test-key")
    fake_client = FakeGetClient(FakeResponse({}, status_error=httpx.HTTPError("boom")))
    monkeypatch.setattr(search_module, "_get_client", lambda: fake_client)

    assert await BraveSearchAdapter().search("anything") == []


async def test_brave_search_skips_results_missing_title_or_url(monkeypatch):
    monkeypatch.setattr(settings, "brave_search_api_key", "test-key")
    body = {"web": {"results": [{"title": "No URL"}, {"url": "https://example.com/no-title"}]}}
    fake_client = FakeGetClient(FakeResponse(body))
    monkeypatch.setattr(search_module, "_get_client", lambda: fake_client)

    assert await BraveSearchAdapter().search("anything") == []


# --- OpenMeteoAdapter -----------------------------------------------------


async def test_open_meteo_returns_weather_for_geocoded_location(monkeypatch):
    fake_client = FakeSequentialClient(
        [
            FakeResponse(
                {"results": [{"latitude": 32.78, "longitude": -83.85, "name": "Lizella", "country": "United States"}]}
            ),
            FakeResponse(
                {"current": {"temperature_2m": 29.5, "weather_code": 1, "wind_speed_10m": 8.2}}
            ),
        ]
    )
    monkeypatch.setattr(weather_module, "_get_client", lambda: fake_client)

    result = await OpenMeteoAdapter().get_weather("Lizella, GA")

    assert result.location_label == "Lizella, United States"
    assert result.temperature_c == 29.5
    assert result.condition == "mainly clear"
    assert result.wind_kph == 8.2


async def test_open_meteo_returns_none_on_geocoding_miss(monkeypatch):
    fake_client = FakeSequentialClient([FakeResponse({"results": []})])
    monkeypatch.setattr(weather_module, "_get_client", lambda: fake_client)

    assert await OpenMeteoAdapter().get_weather("Nowhereville") is None
    assert len(fake_client.calls) == 1


async def test_open_meteo_tries_bare_city_before_full_city_state_form(monkeypatch):
    """Regression test (found via live verification): Open-Meteo's
    geocoder returns no results for "City, ST" (e.g. "Lizella, GA" — the
    roadmap's own example query), only for the bare city name. Confirmed
    live against the real API before writing this fix. The bare name is
    tried *first* since it's the confirmed-common case — avoids paying
    for a doomed geocode round trip on every such lookup (caught in code
    review) — so a successful bare-name match needs only one geocode call.
    """
    fake_client = FakeSequentialClient(
        [
            FakeResponse(
                {"results": [{"latitude": 32.8, "longitude": -83.8, "name": "Lizella", "country": "United States"}]}
            ),
            FakeResponse({"current": {"temperature_2m": 30.0, "weather_code": 0, "wind_speed_10m": 5.0}}),
        ]
    )
    monkeypatch.setattr(weather_module, "_get_client", lambda: fake_client)

    result = await OpenMeteoAdapter().get_weather("Lizella, GA")

    assert result is not None
    assert result.location_label == "Lizella, United States"
    assert fake_client.calls[0]["params"]["name"] == "Lizella"
    assert len(fake_client.calls) == 2


async def test_open_meteo_falls_back_to_full_string_when_bare_city_misses(monkeypatch):
    """Fallback path: if the bare-name geocode attempt misses, retry with
    the full original "City, ST" string before giving up.
    """
    fake_client = FakeSequentialClient(
        [
            FakeResponse({"results": []}),
            FakeResponse(
                {"results": [{"latitude": 1.0, "longitude": 2.0, "name": "Full Match", "country": "Testland"}]}
            ),
            FakeResponse({"current": {"temperature_2m": 20.0, "weather_code": 2, "wind_speed_10m": 3.0}}),
        ]
    )
    monkeypatch.setattr(weather_module, "_get_client", lambda: fake_client)

    result = await OpenMeteoAdapter().get_weather("Ambiguous, Place")

    assert result is not None
    assert result.location_label == "Full Match, Testland"
    assert fake_client.calls[0]["params"]["name"] == "Ambiguous"
    assert fake_client.calls[1]["params"]["name"] == "Ambiguous, Place"


async def test_open_meteo_returns_none_when_both_full_and_bare_city_miss(monkeypatch):
    fake_client = FakeSequentialClient([FakeResponse({"results": []}), FakeResponse({"results": []})])
    monkeypatch.setattr(weather_module, "_get_client", lambda: fake_client)

    assert await OpenMeteoAdapter().get_weather("Nowhereville, ZZ") is None
    assert len(fake_client.calls) == 2


async def test_open_meteo_returns_none_on_http_error(monkeypatch):
    fake_client = FakeSequentialClient([FakeResponse({}, status_error=httpx.HTTPError("boom"))])
    monkeypatch.setattr(weather_module, "_get_client", lambda: fake_client)

    assert await OpenMeteoAdapter().get_weather("Lizella, GA") is None


async def test_open_meteo_unmapped_weather_code_degrades_to_unknown(monkeypatch):
    fake_client = FakeSequentialClient(
        [
            FakeResponse({"results": [{"latitude": 1, "longitude": 1, "name": "X", "country": "Y"}]}),
            FakeResponse({"current": {"temperature_2m": 10.0, "weather_code": 999, "wind_speed_10m": 1.0}}),
        ]
    )
    monkeypatch.setattr(weather_module, "_get_client", lambda: fake_client)

    result = await OpenMeteoAdapter().get_weather("X")

    assert result.condition == "unknown conditions"


# --- OpenMeteoAdapter.get_forecast -----------------------------------------


async def test_open_meteo_get_forecast_returns_daily_data(monkeypatch):
    fake_client = FakeSequentialClient(
        [
            FakeResponse(
                {"results": [{"latitude": 32.78, "longitude": -83.85, "name": "Lizella", "country": "United States"}]}
            ),
            FakeResponse(
                {
                    "daily": {
                        "time": ["2026-07-21", "2026-07-22", "2026-07-23"],
                        "precipitation_sum": [0.0, 5.2, 0.0],
                        "weather_code": [0, 61, 1],
                    }
                }
            ),
        ]
    )
    monkeypatch.setattr(weather_module, "_get_client", lambda: fake_client)

    result = await OpenMeteoAdapter().get_forecast("Lizella, GA", days=3)

    assert result is not None
    assert result.location_label == "Lizella, United States"
    assert len(result.days) == 3
    assert result.days[0].day == date(2026, 7, 21)
    assert result.days[0].precipitation_mm == 0.0
    assert result.days[1].condition == "slight rain"
    assert fake_client.calls[1]["params"]["forecast_days"] == 3
    # Same bare-name-first geocode ordering as `get_weather`.
    assert fake_client.calls[0]["params"]["name"] == "Lizella"


async def test_open_meteo_get_forecast_clamps_days_to_max(monkeypatch):
    fake_client = FakeSequentialClient(
        [
            FakeResponse({"results": [{"latitude": 1, "longitude": 1, "name": "X", "country": "Y"}]}),
            FakeResponse({"daily": {"time": [], "precipitation_sum": [], "weather_code": []}}),
        ]
    )
    monkeypatch.setattr(weather_module, "_get_client", lambda: fake_client)

    await OpenMeteoAdapter().get_forecast("X", days=999)

    assert fake_client.calls[1]["params"]["forecast_days"] == weather_module.MAX_FORECAST_DAYS


async def test_open_meteo_get_forecast_returns_none_on_geocoding_miss(monkeypatch):
    fake_client = FakeSequentialClient([FakeResponse({"results": []})])
    monkeypatch.setattr(weather_module, "_get_client", lambda: fake_client)

    assert await OpenMeteoAdapter().get_forecast("Nowhereville") is None


async def test_open_meteo_get_forecast_returns_none_on_http_error(monkeypatch):
    fake_client = FakeSequentialClient([FakeResponse({}, status_error=httpx.HTTPError("boom"))])
    monkeypatch.setattr(weather_module, "_get_client", lambda: fake_client)

    assert await OpenMeteoAdapter().get_forecast("Lizella, GA") is None


# --- get_search_provider / get_weather_provider ---------------------------


def test_get_search_provider_unknown_raises(monkeypatch):
    monkeypatch.setattr(providers_module, "_search_provider", None)
    monkeypatch.setattr(settings, "search_provider", "not-a-real-provider")

    with pytest.raises(ValueError, match="Unknown AI_SERVICE_SEARCH_PROVIDER"):
        providers_module.get_search_provider()


def test_get_weather_provider_unknown_raises(monkeypatch):
    monkeypatch.setattr(providers_module, "_weather_provider", None)
    monkeypatch.setattr(settings, "weather_provider", "not-a-real-provider")

    with pytest.raises(ValueError, match="Unknown AI_SERVICE_WEATHER_PROVIDER"):
        providers_module.get_weather_provider()


def test_get_search_provider_returns_brave_by_default(monkeypatch):
    monkeypatch.setattr(providers_module, "_search_provider", None)
    monkeypatch.setattr(settings, "search_provider", "brave")

    assert isinstance(providers_module.get_search_provider(), BraveSearchAdapter)


def test_get_weather_provider_returns_open_meteo_by_default(monkeypatch):
    monkeypatch.setattr(providers_module, "_weather_provider", None)
    monkeypatch.setattr(settings, "weather_provider", "open_meteo")

    assert isinstance(providers_module.get_weather_provider(), OpenMeteoAdapter)
