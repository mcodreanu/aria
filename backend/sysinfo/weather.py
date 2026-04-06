"""
weather.py — Weather lookups via wttr.in (no API key required).

wttr.in is a free, open weather service. We use its JSON API for
structured data and format a clean response for ARIA.

Supports:
    - Current conditions
    - Today's forecast (morning / afternoon / evening)
    - 3-day forecast
    - Automatic location detection (omit city = use IP geolocation)

Usage:
    get_weather("Palma")          -> formatted string
    get_weather("London,UK")      -> formatted string
    get_weather("")               -> uses IP-based location
"""

import json
import logging
import urllib.parse
import urllib.request
from typing import Optional

logger = logging.getLogger("aria.weather")

WTTR_URL = "https://wttr.in/{location}?format=j1"

# Condition code → emoji (subset of WMO codes wttr.in uses)
_CONDITION_EMOJI = {
    "sunny": "☀️", "clear": "🌙", "partly cloudy": "⛅",
    "cloudy": "☁️", "overcast": "☁️", "mist": "🌫️", "fog": "🌫️",
    "drizzle": "🌦️", "rain": "🌧️", "heavy rain": "🌧️",
    "snow": "❄️", "sleet": "🌨️", "thunder": "⛈️", "blizzard": "🌨️",
}

_WIND_DIRS = {
    "N": "↑", "NE": "↗", "E": "→", "SE": "↘",
    "S": "↓", "SW": "↙", "W": "←", "NW": "↖",
}


def _emoji_for(desc: str) -> str:
    desc_l = desc.lower()
    for keyword, emoji in _CONDITION_EMOJI.items():
        if keyword in desc_l:
            return emoji
    return "🌡️"


def _wind_arrow(direction: str) -> str:
    for key, arrow in _WIND_DIRS.items():
        if key == direction.upper():
            return arrow
    return ""


def _format_temp(c_str: str, show_f: bool = True) -> str:
    try:
        c = int(c_str)
        f = round(c * 9 / 5 + 32)
        return f"{c}°C ({f}°F)" if show_f else f"{c}°C"
    except Exception:
        return c_str


def _format_hourly_slot(slot: dict, label: str) -> str:
    desc  = slot.get("weatherDesc", [{}])[0].get("value", "")
    temp  = slot.get("tempC", "?")
    feels = slot.get("FeelsLikeC", "?")
    rain  = slot.get("chanceofrain", "0")
    emoji = _emoji_for(desc)
    return (
        f"  **{label}:** {emoji} {desc} · {_format_temp(temp, False)} "
        f"(feels {feels}°C) · 🌧 {rain}% rain"
    )


def get_weather(location: str = "") -> str:
    """
    Fetch weather for location (city name, coordinates, or empty for auto-detect).
    Returns a formatted markdown string suitable for ARIA chat.
    """
    loc_enc = urllib.parse.quote(location.strip()) if location.strip() else ""
    url = WTTR_URL.format(location=loc_enc or "")

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "ARIA-Assistant/1.0 (local assistant)",
                "Accept": "application/json",
            }
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        logger.warning(f"[Weather] HTTP {e.code} for {location!r}")
        return f"Couldn't fetch weather for **{location}** (HTTP {e.code}). Try a more specific city name."
    except Exception as e:
        logger.error(f"[Weather] Request failed: {e}")
        return "Weather service unreachable. Check your internet connection."

    try:
        current    = data["current_condition"][0]
        area_info  = data.get("nearest_area", [{}])[0]
        city       = area_info.get("areaName", [{}])[0].get("value", location or "your location")
        country    = area_info.get("country", [{}])[0].get("value", "")
        place_name = f"{city}, {country}" if country else city

        desc       = current.get("weatherDesc", [{}])[0].get("value", "Unknown")
        temp_c     = current.get("temp_C", "?")
        feels_c    = current.get("FeelsLikeC", "?")
        humidity   = current.get("humidity", "?")
        wind_kph   = current.get("windspeedKmph", "?")
        wind_dir   = current.get("winddir16Point", "")
        visibility = current.get("visibility", "?")
        uv         = current.get("uvIndex", "?")
        emoji      = _emoji_for(desc)
        arrow      = _wind_arrow(wind_dir)

        lines = [
            f"## {emoji} Weather in **{place_name}**",
            f"",
            f"**{desc}** · {_format_temp(temp_c)} · Feels like {feels_c}°C",
            f"",
            f"💧 Humidity: **{humidity}%** · 💨 Wind: **{wind_kph} km/h {arrow}{wind_dir}**",
            f"👁 Visibility: **{visibility} km** · ☀️ UV Index: **{uv}**",
        ]

        # Today's forecast slots (wttr gives 8 3-hourly slots; pick morning/afternoon/evening)
        today = data.get("weather", [{}])[0]
        hourly = today.get("hourly", [])
        slot_map = {
            "Morning":   next((h for h in hourly if int(h.get("time","0"))//100 == 6),  None),
            "Afternoon": next((h for h in hourly if int(h.get("time","0"))//100 == 12), None),
            "Evening":   next((h for h in hourly if int(h.get("time","0"))//100 == 18), None),
        }
        today_max = today.get("maxtempC", "?")
        today_min = today.get("mintempC", "?")
        lines += [
            f"",
            f"**Today:** High {today_max}°C · Low {today_min}°C",
        ]
        for label, slot in slot_map.items():
            if slot:
                lines.append(_format_hourly_slot(slot, label))

        # 3-day forecast summary
        forecast_days = data.get("weather", [])
        if len(forecast_days) >= 2:
            lines += ["", "**3-day forecast:**"]
            import datetime
            today_dt = datetime.date.today()
            for i, day in enumerate(forecast_days[:3]):
                date_str = day.get("date", "")
                try:
                    day_dt  = datetime.date.fromisoformat(date_str)
                    day_lbl = "Today" if i == 0 else day_dt.strftime("%A")
                except Exception:
                    day_lbl = f"Day {i+1}"
                max_c = day.get("maxtempC", "?")
                min_c = day.get("mintempC", "?")
                desc_d = day.get("hourly", [{}])[4].get("weatherDesc", [{}])[0].get("value", "") if day.get("hourly") else ""
                em = _emoji_for(desc_d)
                lines.append(f"  **{day_lbl}:** {em} {desc_d} · {max_c}°C / {min_c}°C")

        return "\n".join(lines)

    except (KeyError, IndexError, TypeError) as e:
        logger.error(f"[Weather] Parse error: {e} | data keys: {list(data.keys())}")
        return f"Got weather data but couldn't parse it. Try again or use a more specific location."