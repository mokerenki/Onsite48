"""
CurrentTimeTool - Returns the current date and time.

Maps region hints (USA, London, China, ...) to IANA zones and returns
human-readable prose so the executor can answer without a second LLM pass
inventing "I don't have the current time".

Resolution strategy, in order:
  1. Exact match against REGION_TIMEZONES (curated aliases: countries,
     demonyms, abbreviations, common cities, multi-zone countries).
  2. Hint already looks like an IANA zone path ("Asia/Shanghai") -> use as-is.
  3. Substring match against the full IANA tzdata city/region list
     (e.g. "shanghai" matches "Asia/Shanghai" even though it's not in our
     curated table).
  4. Fuzzy match (difflib) against the same list, to absorb typos/partial
     names ("mumbay" -> "Asia/Kolkata" via "Calcutta"/"Mumbai" if present).
  5. Give up -> UTC, but say so explicitly in the output instead of quietly
     answering as if UTC was what was asked for.
"""

from __future__ import annotations

import difflib
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

import structlog

from app.tools.registry import BaseTool, ToolSchema

logger = structlog.get_logger("aether.tools.current_time")

# Curated aliases: country names, demonyms, abbreviations, major cities.
# Countries that span multiple zones return a representative spread (capped
# at 4 by the caller) rather than every zone in the country.
REGION_TIMEZONES: dict[str, list[str]] = {
    # --- North America ---
    "usa": ["America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles"],
    "us": ["America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles"],
    "united states": ["America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles"],
    "america": ["America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles"],
    "new york": ["America/New_York"],
    "nyc": ["America/New_York"],
    "eastern": ["America/New_York"],
    "et": ["America/New_York"],
    "est": ["America/New_York"],
    "edt": ["America/New_York"],
    "chicago": ["America/Chicago"],
    "central": ["America/Chicago"],
    "ct": ["America/Chicago"],
    "cst": ["America/Chicago"],
    "cdt": ["America/Chicago"],
    "denver": ["America/Denver"],
    "mountain": ["America/Denver"],
    "mt": ["America/Denver"],
    "mst": ["America/Denver"],
    "mdt": ["America/Denver"],
    "los angeles": ["America/Los_Angeles"],
    "la": ["America/Los_Angeles"],
    "pacific": ["America/Los_Angeles"],
    "pt": ["America/Los_Angeles"],
    "pst": ["America/Los_Angeles"],
    "pdt": ["America/Los_Angeles"],
    "canada": ["America/Toronto", "America/Winnipeg", "America/Edmonton", "America/Vancouver"],
    "toronto": ["America/Toronto"],
    "vancouver": ["America/Vancouver"],
    "mexico": ["America/Mexico_City"],
    "mexico city": ["America/Mexico_City"],

    # --- South America ---
    "brazil": ["America/Sao_Paulo", "America/Manaus"],
    "sao paulo": ["America/Sao_Paulo"],
    "argentina": ["America/Argentina/Buenos_Aires"],
    "buenos aires": ["America/Argentina/Buenos_Aires"],
    "chile": ["America/Santiago"],
    "colombia": ["America/Bogota"],
    "peru": ["America/Lima"],

    # --- Europe ---
    "uk": ["Europe/London"],
    "britain": ["Europe/London"],
    "england": ["Europe/London"],
    "london": ["Europe/London"],
    "gmt": ["Etc/GMT"],
    "utc": ["UTC"],
    "germany": ["Europe/Berlin"],
    "berlin": ["Europe/Berlin"],
    "france": ["Europe/Paris"],
    "paris": ["Europe/Paris"],
    "spain": ["Europe/Madrid"],
    "madrid": ["Europe/Madrid"],
    "italy": ["Europe/Rome"],
    "rome": ["Europe/Rome"],
    "netherlands": ["Europe/Amsterdam"],
    "amsterdam": ["Europe/Amsterdam"],
    "switzerland": ["Europe/Zurich"],
    "zurich": ["Europe/Zurich"],
    "sweden": ["Europe/Stockholm"],
    "norway": ["Europe/Oslo"],
    "denmark": ["Europe/Copenhagen"],
    "finland": ["Europe/Helsinki"],
    "poland": ["Europe/Warsaw"],
    "greece": ["Europe/Athens"],
    "portugal": ["Europe/Lisbon"],
    "ireland": ["Europe/Dublin"],
    "austria": ["Europe/Vienna"],
    "belgium": ["Europe/Brussels"],
    "hungary": ["Europe/Budapest"],
    "ukraine": ["Europe/Kyiv"],
    "russia": ["Europe/Moscow", "Asia/Yekaterinburg", "Asia/Novosibirsk", "Asia/Vladivostok"],
    "moscow": ["Europe/Moscow"],

    # --- Africa ---
    "south africa": ["Africa/Johannesburg"],
    "johannesburg": ["Africa/Johannesburg"],
    "sa": ["Africa/Johannesburg"],
    "egypt": ["Africa/Cairo"],
    "cairo": ["Africa/Cairo"],
    "nigeria": ["Africa/Lagos"],
    "lagos": ["Africa/Lagos"],
    "kenya": ["Africa/Nairobi"],
    "nairobi": ["Africa/Nairobi"],
    "morocco": ["Africa/Casablanca"],

    # --- Middle East ---
    "uae": ["Asia/Dubai"],
    "dubai": ["Asia/Dubai"],
    "saudi arabia": ["Asia/Riyadh"],
    "riyadh": ["Asia/Riyadh"],
    "israel": ["Asia/Jerusalem"],
    "jerusalem": ["Asia/Jerusalem"],
    "tel aviv": ["Asia/Jerusalem"],
    "turkey": ["Europe/Istanbul"],
    "istanbul": ["Europe/Istanbul"],
    "iran": ["Asia/Tehran"],
    "iraq": ["Asia/Baghdad"],

    # --- South / Central Asia ---
    "india": ["Asia/Kolkata"],
    "ist": ["Asia/Kolkata"],
    "mumbai": ["Asia/Kolkata"],
    "delhi": ["Asia/Kolkata"],
    "pakistan": ["Asia/Karachi"],
    "karachi": ["Asia/Karachi"],
    "bangladesh": ["Asia/Dhaka"],
    "dhaka": ["Asia/Dhaka"],

    # --- East / Southeast Asia ---
    "china": ["Asia/Shanghai"],
    "beijing": ["Asia/Shanghai"],
    "shanghai": ["Asia/Shanghai"],
    "hong kong": ["Asia/Hong_Kong"],
    "taiwan": ["Asia/Taipei"],
    "taipei": ["Asia/Taipei"],
    "tokyo": ["Asia/Tokyo"],
    "japan": ["Asia/Tokyo"],
    "korea": ["Asia/Seoul"],
    "south korea": ["Asia/Seoul"],
    "seoul": ["Asia/Seoul"],
    "singapore": ["Asia/Singapore"],
    "vietnam": ["Asia/Ho_Chi_Minh"],
    "thailand": ["Asia/Bangkok"],
    "bangkok": ["Asia/Bangkok"],
    "indonesia": ["Asia/Jakarta"],
    "jakarta": ["Asia/Jakarta"],
    "philippines": ["Asia/Manila"],
    "manila": ["Asia/Manila"],
    "malaysia": ["Asia/Kuala_Lumpur"],

    # --- Oceania ---
    "australia": ["Australia/Sydney", "Australia/Perth", "Australia/Adelaide"],
    "sydney": ["Australia/Sydney"],
    "melbourne": ["Australia/Melbourne"],
    "perth": ["Australia/Perth"],
    "new zealand": ["Pacific/Auckland"],
    "auckland": ["Pacific/Auckland"],
}


@lru_cache(maxsize=1)
def _all_zones() -> list[str]:
    """All IANA zone names, cached once per process."""
    from zoneinfo import available_timezones

    return sorted(available_timezones())


def _substring_match(key: str) -> str | None:
    """Find an IANA zone whose city/region component contains the hint."""
    needle = key.replace(" ", "_")
    for zone in _all_zones():
        # Compare against the last path segment (city) and the whole path,
        # both lowercased, since e.g. "buenos_aires" is nested under
        # "America/Argentina/Buenos_Aires".
        tail = zone.rsplit("/", 1)[-1].lower()
        if needle == tail or needle in zone.lower():
            return zone
    return None


def _fuzzy_match(key: str) -> str | None:
    """Best-effort fuzzy match against IANA city names for typos/partials."""
    zones = _all_zones()
    tails = {zone.rsplit("/", 1)[-1].lower().replace("_", " "): zone for zone in zones}
    matches = difflib.get_close_matches(key, tails.keys(), n=1, cutoff=0.75)
    if matches:
        return tails[matches[0]]
    return None


def resolve_timezones(hint: str | None) -> tuple[list[str], bool]:
    """Resolve a free-text hint to IANA zones.

    Returns (zones, matched) where matched=False means we fell back to UTC
    without understanding the hint, so the caller can be honest about it.
    """
    if not hint:
        return ["UTC"], True

    key = hint.strip().lower().replace("_", " ")

    if key in REGION_TIMEZONES:
        return list(REGION_TIMEZONES[key]), True

    # Looks like an explicit IANA path already, e.g. "Asia/Shanghai".
    if "/" in hint:
        return [hint.strip()], True

    if key == "utc":
        return ["UTC"], True

    substring_hit = _substring_match(key)
    if substring_hit:
        return [substring_hit], True

    fuzzy_hit = _fuzzy_match(key)
    if fuzzy_hit:
        return [fuzzy_hit], True

    return ["UTC"], False


def format_local(now: datetime) -> str:
    return now.strftime("%A, %d %B %Y at %H:%M:%S %Z (UTC%z)")


class CurrentTimeTool(BaseTool):
    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="current_time",
            description=(
                "Get the current date and time. Optionally specify a timezone, "
                "country, or city (e.g. 'America/New_York', 'USA', 'London', "
                "'China', 'Nairobi'). Use when the user asks what time or date "
                "it is anywhere in the world."
            ),
            parameters={
                "timezone": {
                    "type": "string",
                    "description": (
                        "IANA timezone, country, or city name "
                        "(e.g. 'America/New_York', 'USA', 'China', 'Nairobi'). "
                        "Defaults to UTC."
                    ),
                },
                "format": {
                    "type": "string",
                    "description": "Optional Python strftime format. Defaults to human-readable.",
                },
            },
            required=[],
        )

    async def execute(self, **kwargs: Any) -> dict:
        tz_name = kwargs.get("timezone")
        fmt = kwargs.get("format")

        try:
            from zoneinfo import ZoneInfo

            zones, matched = resolve_timezones(tz_name if isinstance(tz_name, str) else None)
            readings: list[dict[str, str]] = []
            for zone in zones[:4]:
                try:
                    tz = timezone.utc if zone.upper() == "UTC" else ZoneInfo(zone)
                except Exception:
                    tz = timezone.utc
                    zone = "UTC"
                now = datetime.now(tz)
                local_str = now.strftime(fmt) if fmt else format_local(now)
                readings.append(
                    {
                        "timezone": zone,
                        "iso": now.isoformat(),
                        "human": local_str,
                    }
                )

            utc_now = datetime.now(timezone.utc)
            primary = readings[0]

            if not matched:
                answer = (
                    f"I couldn't match '{tz_name}' to a specific timezone, so here is "
                    f"UTC instead: {primary['human']}. Try a country, city, or an IANA "
                    "zone like 'Asia/Shanghai'."
                )
            elif len(readings) == 1:
                answer = f"It is currently {primary['human']}."
            else:
                lines = ["Current time across the requested region:"]
                for item in readings:
                    lines.append(f"- {item['timezone']}: {item['human']}")
                answer = "\n".join(lines)

            return {
                "output": answer,
                "iso": primary["iso"],
                "timezone": primary["timezone"],
                "human": primary["human"],
                "utc_iso": utc_now.isoformat(),
                "readings": readings,
                "matched": matched,
            }
        except Exception as e:
            logger.error("current_time.failed", error=str(e))
            fallback = datetime.now(timezone.utc)
            human = format_local(fallback)
            return {
                "output": f"It is currently {human} (UTC). Could not resolve timezone '{tz_name}': {e}",
                "iso": fallback.isoformat(),
                "timezone": "UTC",
                "human": human,
                "utc_iso": fallback.isoformat(),
            }