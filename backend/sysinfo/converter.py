"""
converter.py — Unit and currency conversion for ARIA.

Unit conversions: pure Python, no network, no API key.
Currency conversions: uses exchangerate-api.com free tier (no key needed
for the open endpoint) with a 1-hour in-process cache.

Supported unit categories:
    Length, Weight/Mass, Temperature, Volume, Area, Speed,
    Data/Storage, Time, Pressure, Energy

Usage:
    convert("250 EUR to USD")   -> "250 EUR = 271.25 USD  ..."
    convert("15 miles to km")   -> "15 miles = 24.14 km"
    convert("100 F to C")       -> "100°F = 37.78°C"
    convert_units(15, "miles", "km")  -> float
"""

import re
import time
import json
import logging
import urllib.request
from typing import Optional

logger = logging.getLogger("aria.converter")

# ── Unit tables ───────────────────────────────────────────────────────────────
# Each entry: canonical_name → factor_to_base_unit
# Base units: metre, kilogram, litre, second, m², m/s, byte, pascal, joule

LENGTH = {
    "mm": 0.001, "millimeter": 0.001, "millimetre": 0.001,
    "cm": 0.01,  "centimeter": 0.01,  "centimetre": 0.01,
    "m":  1.0,   "meter": 1.0,        "metre": 1.0,
    "km": 1000,  "kilometer": 1000,   "kilometre": 1000,
    "in": 0.0254,"inch": 0.0254,      "inches": 0.0254,
    "ft": 0.3048,"foot": 0.3048,      "feet": 0.3048,
    "yd": 0.9144,"yard": 0.9144,      "yards": 0.9144,
    "mi": 1609.344, "mile": 1609.344, "miles": 1609.344,
    "nmi": 1852, "nautical mile": 1852,
    "ly": 9.461e15, "light-year": 9.461e15,
}

WEIGHT = {
    "mg":  0.000001, "milligram": 0.000001,
    "g":   0.001,    "gram": 0.001,
    "kg":  1.0,      "kilogram": 1.0,
    "t":   1000,     "tonne": 1000, "metric ton": 1000,
    "oz":  0.0283495,"ounce": 0.0283495,
    "lb":  0.453592, "pound": 0.453592, "lbs": 0.453592,
    "st":  6.35029,  "stone": 6.35029,
    "ton": 907.185,  "short ton": 907.185,
}

VOLUME = {
    "ml":  0.001,    "milliliter": 0.001,  "millilitre": 0.001,
    "l":   1.0,      "liter": 1.0,         "litre": 1.0,
    "dl":  0.1,      "deciliter": 0.1,
    "cl":  0.01,     "centiliter": 0.01,
    "m3":  1000,     "cubic meter": 1000,
    "gal": 3.78541,  "gallon": 3.78541,    "us gallon": 3.78541,
    "qt":  0.946353, "quart": 0.946353,
    "pt":  0.473176, "pint": 0.473176,
    "cup": 0.236588,
    "fl oz": 0.0295735, "fluid ounce": 0.0295735,
    "tbsp": 0.0147868,  "tablespoon": 0.0147868,
    "tsp":  0.00492892, "teaspoon": 0.00492892,
    "imp gal": 4.54609, "imperial gallon": 4.54609,
}

AREA = {
    "mm2": 1e-6,    "cm2": 1e-4,   "m2": 1.0,    "km2": 1e6,
    "sqm": 1.0,     "sqkm": 1e6,   "hectare": 1e4, "ha": 1e4,
    "acre": 4046.86,"sqft": 0.0929, "sqyd": 0.836127,
    "sqmi": 2.59e6, "sqin": 0.000645,
}

SPEED = {
    "m/s": 1.0,  "mps": 1.0,
    "km/h": 1/3.6, "kph": 1/3.6, "kmh": 1/3.6,
    "mph":  0.44704,"mi/h": 0.44704,
    "knot": 0.514444, "kn": 0.514444,
    "ft/s": 0.3048,
}

DATA = {
    "bit": 0.125,   "b": 0.125,
    "byte": 1.0,    "B": 1.0,
    "kb":   1e3,    "kilobyte": 1e3,
    "mb":   1e6,    "megabyte": 1e6,
    "gb":   1e9,    "gigabyte": 1e9,
    "tb":   1e12,   "terabyte": 1e12,
    "pb":   1e15,   "petabyte": 1e15,
    "kib":  1024,   "mib": 1024**2,  "gib": 1024**3, "tib": 1024**4,
}

TIME_UNITS = {
    "ms": 0.001,        "millisecond": 0.001,
    "s":  1.0,          "second": 1.0,      "sec": 1.0,
    "min": 60,          "minute": 60,
    "h":  3600,         "hour": 3600,
    "d":  86400,        "day": 86400,
    "wk": 604800,       "week": 604800,
    "mo": 2629800,      "month": 2629800,
    "yr": 31557600,     "year": 31557600,
}

PRESSURE = {
    "pa": 1.0,    "pascal": 1.0,
    "kpa": 1000,  "kilopascal": 1000,
    "bar": 1e5,   "mbar": 100,
    "psi": 6894.76, "atm": 101325,
    "mmhg": 133.322, "torr": 133.322,
}

ENERGY = {
    "j": 1.0,       "joule": 1.0,
    "kj": 1000,     "kilojoule": 1000,
    "mj": 1e6,      "megajoule": 1e6,
    "cal": 4.184,   "calorie": 4.184,
    "kcal": 4184,   "kilocalorie": 4184,
    "wh": 3600,     "kwh": 3.6e6,
    "btu": 1055.06, "ev": 1.602e-19,
}

_ALL_UNIT_TABLES = [LENGTH, WEIGHT, VOLUME, AREA, SPEED, DATA, TIME_UNITS, PRESSURE, ENERGY]

# ── Currency cache ────────────────────────────────────────────────────────────
_fx_cache: dict = {}
_fx_fetched_at: float = 0
_FX_TTL = 3600   # seconds


def _get_fx_rates(base: str = "USD") -> Optional[dict]:
    """Fetch exchange rates from open.er-api.com (free, no key)."""
    global _fx_cache, _fx_fetched_at

    now = time.time()
    if _fx_cache.get("base") == base and now - _fx_fetched_at < _FX_TTL:
        return _fx_cache.get("rates")

    try:
        url = f"https://open.er-api.com/v6/latest/{base.upper()}"
        req = urllib.request.Request(url, headers={"User-Agent": "ARIA/1.0"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode())
        if data.get("result") == "success":
            _fx_cache = {"base": base, "rates": data["rates"]}
            _fx_fetched_at = now
            return data["rates"]
    except Exception as e:
        logger.warning(f"[Converter] FX fetch failed: {e}")
    return None


# ── Unit conversion ───────────────────────────────────────────────────────────

def _find_unit(name: str) -> Optional[tuple[dict, float]]:
    """Return (table, factor) for a unit name, or None."""
    n = name.lower().rstrip("s").strip()   # simple depluralization
    for table in _ALL_UNIT_TABLES:
        if name.lower() in table:
            return table, table[name.lower()]
        if n in table:
            return table, table[n]
    return None


def convert_units(value: float, from_unit: str, to_unit: str) -> Optional[float]:
    """Convert value from from_unit to to_unit. Returns None if units unknown."""
    from_res = _find_unit(from_unit)
    to_res   = _find_unit(to_unit)
    if not from_res or not to_res:
        return None
    from_table, from_factor = from_res
    to_table,   to_factor   = to_res
    if from_table is not to_table:
        return None   # different physical quantities
    return value * from_factor / to_factor


def _format_number(n: float) -> str:
    """Format cleanly: no trailing zeros, reasonable precision."""
    if abs(n) >= 1000:
        return f"{n:,.2f}".rstrip("0").rstrip(".")
    if abs(n) >= 1:
        return f"{n:.4f}".rstrip("0").rstrip(".")
    return f"{n:.6f}".rstrip("0").rstrip(".")


# ── Temperature (special case — not a ratio scale) ────────────────────────────

def convert_temperature(value: float, from_unit: str, to_unit: str) -> Optional[float]:
    f = from_unit.lower().rstrip("°").strip()
    t = to_unit.lower().rstrip("°").strip()
    conversions = {
        ("c","f"): lambda v: v * 9/5 + 32,
        ("f","c"): lambda v: (v - 32) * 5/9,
        ("c","k"): lambda v: v + 273.15,
        ("k","c"): lambda v: v - 273.15,
        ("f","k"): lambda v: (v - 32) * 5/9 + 273.15,
        ("k","f"): lambda v: (v - 273.15) * 9/5 + 32,
        ("c","c"): lambda v: v,
        ("f","f"): lambda v: v,
        ("k","k"): lambda v: v,
    }
    # Accept "celsius", "fahrenheit", "kelvin"
    aliases = {"celsius":"c","fahrenheit":"f","kelvin":"k","°c":"c","°f":"f","°k":"k"}
    f = aliases.get(f, f[0] if f else f)
    t = aliases.get(t, t[0] if t else t)
    fn = conversions.get((f, t))
    return fn(value) if fn else None


# ── Main parse + convert entry point ─────────────────────────────────────────

_CONVERT_RE = re.compile(
    r"(?:convert\s+)?(-?\d+(?:[.,]\d+)?(?:e[+-]?\d+)?)\s*"
    r"([a-zA-Z°/²³\s]+?)\s+"
    r"(?:to|in|into|as|→)\s+"
    r"([a-zA-Z°/²³\s]+?)(?:\s*$|[?.!])",
    re.IGNORECASE
)


def convert(text: str) -> Optional[str]:
    """
    Parse and execute a conversion request.
    Returns a formatted string or None if the pattern doesn't match.
    """
    text = text.strip()
    m = _CONVERT_RE.search(text)
    if not m:
        return None

    raw_val  = m.group(1).replace(",", "")
    from_str = m.group(2).strip().lower()
    to_str   = m.group(3).strip().lower()

    try:
        value = float(raw_val)
    except ValueError:
        return None

    # ── Temperature ──
    temp_keywords = {"c","f","k","celsius","fahrenheit","kelvin","°c","°f","°k"}
    if from_str.rstrip("s") in temp_keywords or to_str.rstrip("s") in temp_keywords:
        result = convert_temperature(value, from_str, to_str)
        if result is not None:
            fu = from_str.upper().replace("CELSIUS","°C").replace("FAHRENHEIT","°F").replace("KELVIN","K")
            tu = to_str.upper().replace("CELSIUS","°C").replace("FAHRENHEIT","°F").replace("KELVIN","K")
            return f"**{value} {fu} = {_format_number(result)} {tu}**"

    # ── Standard units ──
    result = convert_units(value, from_str, to_str)
    if result is not None:
        # Friendly unit labels
        fu = from_str
        tu = to_str
        return f"**{value} {fu} = {_format_number(result)} {tu}**"

    # ── Currency ──
    from_upper = from_str.upper()
    to_upper   = to_str.upper()
    if len(from_upper) == 3 and len(to_upper) == 3:
        rates = _get_fx_rates(from_upper)
        if rates and to_upper in rates:
            result = value * rates[to_upper]
            return (
                f"**{value:,.2f} {from_upper} = {result:,.2f} {to_upper}**\n"
                f"*Rate: 1 {from_upper} = {rates[to_upper]:.4f} {to_upper} "
                f"(via open.er-api.com)*"
            )
        elif rates is None:
            return (
                f"Currency conversion service unreachable. "
                f"Check your internet connection and try again."
            )
        else:
            return f"Unknown currency code **{to_upper}**. Use ISO 4217 codes (USD, EUR, GBP…)"

    return None   # couldn't parse — let LLM handle it