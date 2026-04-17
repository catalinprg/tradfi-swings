"""Per-stock earnings calendar from Finnhub.

Fetched once per pipeline run alongside the macro context. For each
watchlist instrument with `asset_class == "stock"` AND a `finnhub_symbol`,
we query Finnhub's /calendar/earnings over the next EARNINGS_HORIZON_DAYS
days. The first matching event per symbol ends up in the output (stocks
typically have one scheduled print per quarter).

Env vars:
  FINNHUB_API_KEY — required. Module returns [] silently when unset.

Return shape:

    [
      {
        "slug": "aapl",
        "symbol": "AAPL",
        "display": "Apple",
        "date": "2026-04-25",            # ISO date
        "hour": "bmo" | "amc" | "dmh" | None,   # before / after / during market
        "eps_estimate": float | None,
        "revenue_estimate": float | None,
        "days_until": int,               # relative to run time
      },
      ...
    ]
"""
import os
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import requests

FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "")
FINNHUB_BASE = "https://finnhub.io/api/v1"
EARNINGS_HORIZON_DAYS = 14
HTTP_TIMEOUT = 8


def _float_or_none(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def fetch_for_symbol(finnhub_symbol: str, today: Optional[date] = None) -> Optional[dict]:
    """Return the first scheduled earnings event within EARNINGS_HORIZON_DAYS
    for `finnhub_symbol`, or None when no event exists / API fails."""
    if not FINNHUB_API_KEY or not finnhub_symbol:
        return None
    today = today or datetime.now(timezone.utc).date()
    horizon = today + timedelta(days=EARNINGS_HORIZON_DAYS)
    url = (
        f"{FINNHUB_BASE}/calendar/earnings"
        f"?from={today.isoformat()}&to={horizon.isoformat()}"
        f"&symbol={finnhub_symbol}"
    )
    try:
        r = requests.get(
            url,
            headers={"X-Finnhub-Token": FINNHUB_API_KEY},
            timeout=HTTP_TIMEOUT,
        )
        if r.status_code != 200:
            return None
        data = r.json() or {}
        events = data.get("earningsCalendar") or []
        if not events:
            return None
        events.sort(key=lambda e: e.get("date") or "")
        ev = events[0]
        ev_date_str = ev.get("date")
        if not ev_date_str:
            return None
        try:
            ev_date = date.fromisoformat(ev_date_str)
        except ValueError:
            return None
        return {
            "date":             ev_date.isoformat(),
            "hour":             (ev.get("hour") or None),
            "eps_estimate":     _float_or_none(ev.get("epsEstimate")),
            "revenue_estimate": _float_or_none(ev.get("revenueEstimate")),
            "days_until":       (ev_date - today).days,
        }
    except Exception:
        return None


def fetch_for_watchlist(watchlist_instruments: dict) -> list[dict]:
    """Iterate watchlist rows, keep stock+finnhub_symbol rows, return a list
    of earnings events (one per symbol) within the horizon. Skips silently
    when no key is configured so CI / local runs without Finnhub still work."""
    if not FINNHUB_API_KEY:
        return []
    today = datetime.now(timezone.utc).date()
    out: list[dict] = []
    for slug, instr in watchlist_instruments.items():
        if instr.get("asset_class") != "stock":
            continue
        fh_sym = instr.get("finnhub_symbol")
        if not fh_sym:
            continue
        event = fetch_for_symbol(fh_sym, today=today)
        if event is None:
            continue
        out.append({
            "slug":    slug,
            "symbol":  fh_sym,
            "display": instr.get("display", slug.upper()),
            **event,
        })
    out.sort(key=lambda e: e["date"])
    return out
