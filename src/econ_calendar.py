"""Economic calendar loader.

Reads from this repo's own GitHub-hosted mirror
(`data-mirror/ff_calendar_thisweek.json`), refreshed every 4h by a GHA
workflow pulling Forex Factory's public JSON. The repo is public — same
pattern as catalinprg/financial-briefing, originally adopted because
`nfs.faireconomy.media` is not consistently reachable from Claude Code
cloud sessions whereas `raw.githubusercontent.com` is.

Returned events include only high/medium impact in the next 48h; agent
filters further by `country` / `title` against a watchlist row's
relevance_terms. Degrades to an empty list on any error.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

MIRROR_URL = (
    "https://raw.githubusercontent.com/catalinprg/tradfi-swings/main/"
    "data-mirror/ff_calendar_thisweek.json"
)
TIMEOUT = 8
MAX_RETRIES = 2
KEEP_IMPACTS = {"high", "medium"}
WINDOW_HOURS = 48


def _try_parse(ts: str) -> Optional[datetime]:
    """Forex Factory dates come in several formats across the week's JSON.
    Try the most common; return None on fail."""
    if not ts:
        return None
    ts = ts.strip().replace("Z", "+00:00")
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
    ):
        try:
            return datetime.strptime(ts, fmt).replace(
                tzinfo=datetime.strptime(ts, fmt).tzinfo or timezone.utc
            )
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(ts)
    except Exception:
        return None


def fetch() -> list[dict]:
    """Load + normalize calendar events. Returns a list of:
        {
          "title": str,
          "country": str,
          "currency": str | None,
          "date_utc": str (ISO),
          "impact": "high" | "medium",
          "forecast": str | None,
          "previous": str | None,
        }
    Filtered to events in the next 48h with high/medium impact."""
    raw = None
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.get(MIRROR_URL, timeout=TIMEOUT)
            if r.status_code == 200:
                raw = r.json()
                break
        except Exception:
            pass
    if raw is None or not isinstance(raw, list):
        return []

    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=WINDOW_HOURS)

    events: list[dict] = []
    for ev in raw:
        impact = str(ev.get("impact", "")).lower()
        if impact not in KEEP_IMPACTS:
            continue
        pub = _try_parse(ev.get("date", ""))
        if pub is None:
            continue
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=timezone.utc)
        if not (now - timedelta(hours=2) <= pub <= horizon):
            # include events from the last 2h in case the analyst wants
            # to reference something that just dropped
            continue
        events.append({
            "title":    str(ev.get("title", "")).strip(),
            "country":  str(ev.get("country", "")).strip(),
            "currency": (str(ev.get("currency", "")).strip() or None),
            "date_utc": pub.astimezone(timezone.utc).isoformat(),
            "impact":   impact,
            "forecast": ev.get("forecast") or None,
            "previous": ev.get("previous") or None,
        })
    # Sort chronologically so the agent sees events in order
    events.sort(key=lambda e: e["date_utc"])
    return events
