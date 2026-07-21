import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date

import httpx

from app.lazy_singleton import LazySingleton

logger = logging.getLogger(__name__)

# Open-Meteo's own ceiling on `forecast_days` for the models backing this
# endpoint — requesting more just gets silently clamped server-side, so
# clamping here too keeps `days` meaningful to a caller inspecting it.
MAX_FORECAST_DAYS = 16


@dataclass
class WeatherResult:
    location_label: str
    temperature_c: float
    condition: str
    wind_kph: float


@dataclass
class DailyForecast:
    day: date
    precipitation_mm: float
    condition: str


@dataclass
class ForecastResult:
    location_label: str
    days: list[DailyForecast]


class WeatherProvider(ABC):
    """Read-only current-weather/forecast lookup, used by the Research
    Assistant's tool-choice loop (`agents/nodes.py::research_node`).
    Swappable via `AI_SERVICE_WEATHER_PROVIDER`, mirroring
    `SearchProvider`'s seam.
    """

    @abstractmethod
    async def get_weather(self, location: str) -> WeatherResult | None:
        """Degrades to `None` on any failure (geocoding miss, HTTP error,
        malformed response) — same never-load-bearing contract as
        `SearchProvider.search`.
        """
        ...

    @abstractmethod
    async def get_forecast(self, location: str, days: int = MAX_FORECAST_DAYS) -> ForecastResult | None:
        """Day-by-day precipitation/condition outlook, up to
        `MAX_FORECAST_DAYS` days ahead — for questions `get_weather`'s
        current-conditions-only shape can't answer at all (e.g. "when's
        the next dry stretch", "will it rain Thursday"). Hands back raw
        per-day data rather than answering the question itself: finding a
        window (e.g. "5 consecutive dry days") is the model's job once it
        has the data, the same division of labor every other tool in this
        loop already follows. Degrades to `None` on any failure, same
        contract as `get_weather`.
        """
        ...


# WMO weather codes (the shape Open-Meteo's `weather_code` field uses) —
# collapsed to the subset of human-readable conditions relevant to a
# household chat answer, not the full spec. An unmapped code (rare —
# WMO's table is otherwise exhaustive) degrades to a generic label rather
# than dropping the whole result.
_WMO_CONDITIONS: dict[int, str] = {
    0: "clear sky",
    1: "mainly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "depositing rime fog",
    51: "light drizzle",
    53: "moderate drizzle",
    55: "dense drizzle",
    56: "light freezing drizzle",
    57: "dense freezing drizzle",
    61: "slight rain",
    63: "moderate rain",
    65: "heavy rain",
    66: "light freezing rain",
    67: "heavy freezing rain",
    71: "slight snow fall",
    73: "moderate snow fall",
    75: "heavy snow fall",
    77: "snow grains",
    80: "slight rain showers",
    81: "moderate rain showers",
    82: "violent rain showers",
    85: "slight snow showers",
    86: "heavy snow showers",
    95: "thunderstorm",
    96: "thunderstorm with slight hail",
    99: "thunderstorm with heavy hail",
}


def _describe_condition(code: int | None) -> str:
    if code is None:
        return "unknown conditions"
    return _WMO_CONDITIONS.get(code, "unknown conditions")


_client = LazySingleton(lambda: httpx.AsyncClient(timeout=10.0))


def _get_client() -> httpx.AsyncClient:
    return _client.get()


class OpenMeteoAdapter(WeatherProvider):
    """Open-Meteo — free, no API key, includes its own free geocoding
    endpoint, so a bare place name (city, "city, state") resolves to
    coordinates in the same call chain with no separate geocoding key to
    manage.
    """

    async def _geocode(self, client: httpx.AsyncClient, name: str) -> dict | None:
        resp = await client.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": name, "count": 1},
        )
        resp.raise_for_status()
        results = resp.json().get("results")
        return results[0] if results else None

    async def _resolve_place(self, client: httpx.AsyncClient, location: str) -> dict | None:
        """Shared by `get_weather`/`get_forecast` — Open-Meteo's geocoder
        only matches a bare place name — it reliably returns nothing for
        "City, ST"/"City, State" (confirmed live: "Lizella, GA" empty,
        "Lizella" matches), even though that's the natural way a household
        member (and the roadmap's own example query) would name a place.
        Since the full "City, ST" form is the one confirmed to fail, try
        the bare name first when a comma is present — avoids paying for a
        doomed geocode round trip on every such lookup before falling back
        to the full string (caught in code review).
        """
        candidates = [location]
        if "," in location:
            candidates = [location.split(",", 1)[0].strip(), location]
        for candidate in candidates:
            place = await self._geocode(client, candidate)
            if place is not None:
                return place
        return None

    @staticmethod
    def _location_label(place: dict, fallback: str) -> str:
        label_parts = [p for p in (place.get("name"), place.get("country")) if p]
        return ", ".join(label_parts) or fallback

    async def get_weather(self, location: str) -> WeatherResult | None:
        client = _get_client()
        try:
            place = await self._resolve_place(client, location)
            if place is None:
                return None
            latitude, longitude = place["latitude"], place["longitude"]

            forecast_resp = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": latitude,
                    "longitude": longitude,
                    "current": "temperature_2m,weather_code,wind_speed_10m",
                    "temperature_unit": "celsius",
                    "wind_speed_unit": "kmh",
                },
            )
            forecast_resp.raise_for_status()
            current = forecast_resp.json()["current"]

            return WeatherResult(
                location_label=self._location_label(place, location),
                temperature_c=current["temperature_2m"],
                condition=_describe_condition(current.get("weather_code")),
                wind_kph=current["wind_speed_10m"],
            )
        except Exception:
            logger.warning("open-meteo lookup failed, degrading to no weather result", exc_info=True)
            return None

    async def get_forecast(self, location: str, days: int = MAX_FORECAST_DAYS) -> ForecastResult | None:
        client = _get_client()
        days = max(1, min(days, MAX_FORECAST_DAYS))
        try:
            place = await self._resolve_place(client, location)
            if place is None:
                return None
            latitude, longitude = place["latitude"], place["longitude"]

            forecast_resp = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": latitude,
                    "longitude": longitude,
                    "daily": "precipitation_sum,weather_code",
                    "timezone": "auto",
                    "forecast_days": days,
                },
            )
            forecast_resp.raise_for_status()
            daily = forecast_resp.json()["daily"]

            return ForecastResult(
                location_label=self._location_label(place, location),
                days=[
                    DailyForecast(
                        day=date.fromisoformat(day_str),
                        precipitation_mm=precipitation_mm,
                        condition=_describe_condition(weather_code),
                    )
                    for day_str, precipitation_mm, weather_code in zip(
                        daily["time"], daily["precipitation_sum"], daily["weather_code"]
                    )
                ],
            )
        except Exception:
            logger.warning("open-meteo forecast lookup failed, degrading to no forecast result", exc_info=True)
            return None
