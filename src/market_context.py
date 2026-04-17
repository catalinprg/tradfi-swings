"""Market-wide context snapshot — VIX and DXY.

Fetched once per pipeline run and attached to every instrument's payload as
`market_context`. The analyst uses VIX for risk-on/off framing (equities,
stocks) and DXY for USD strength framing (forex pairs, commodities, and
USD-correlated equity flows).

Two data paths for resilience (yfinance is not SLA-backed — empty responses
and silent rate limits are common):
  1. Primary: yfinance with the native Yahoo symbols (`^VIX`, `DX-Y.NYB`).
  2. Fallback: Alpha Vantage `TIME_SERIES_DAILY` on configurable proxies
     (defaults: `VIXY` ETF for VIX, `UUP` ETF for DXY). Override via
     ALPHAVANTAGE_VIX_SYMBOL / ALPHAVANTAGE_DXY_SYMBOL. Fallback only
     activates when yfinance returned nothing AND an ALPHAVANTAGE_API_KEY
     is set. Each payload entry carries a `source` field so the analyst
     can flag when the number came from the proxy.

Contract — the returned dict mirrors the crypto pipeline's per-section null
pattern so that a single upstream failure degrades gracefully:

    {
      "vix": {"value": float, "change_24h_pct": float, "source": "yfinance" | "alphavantage"} | None,
      "dxy": {"value": float, "change_24h_pct": float, "source": "yfinance" | "alphavantage"} | None,
      "partial": bool,
      "missing": ["vix" | "dxy", ...],
    }
"""
import os
import time

import requests
import yfinance as yf

VIX_SYMBOL = "^VIX"
DXY_SYMBOL = "DX-Y.NYB"

ALPHAVANTAGE_API_KEY = os.environ.get("ALPHAVANTAGE_API_KEY", "")
# ETF proxies that mirror the underlying index. Direct `VIX` / `DXY` symbols
# are unreliable on Alpha Vantage's free tier — the ETFs are always quoted.
# Directionally and qualitatively equivalent for risk-on/off and USD-strength
# reads, which is all the agent uses.
AV_VIX_SYMBOL = os.environ.get("ALPHAVANTAGE_VIX_SYMBOL", "VIXY")
AV_DXY_SYMBOL = os.environ.get("ALPHAVANTAGE_DXY_SYMBOL", "UUP")
AV_BASE = "https://www.alphavantage.co/query"
AV_TIMEOUT = 10


def _yfinance_latest_and_change(symbol: str) -> dict | None:
    for attempt in range(2):
        try:
            df = yf.Ticker(symbol).history(
                interval="1d", period="5d",
                auto_adjust=False, raise_errors=False,
            )
            if df is None or df.empty or len(df) < 2:
                return None
            closes = df["Close"].dropna().tolist()
            if len(closes) < 2:
                return None
            latest = float(closes[-1])
            prev = float(closes[-2])
            if prev == 0:
                return None
            return {
                "value": round(latest, 2),
                "change_24h_pct": round((latest - prev) / prev * 100, 2),
                "source": "yfinance",
            }
        except Exception:
            if attempt == 1:
                return None
            time.sleep(2)
    return None


def _alphavantage_latest_and_change(symbol: str) -> dict | None:
    """Alpha Vantage TIME_SERIES_DAILY on `symbol`. Returns the same shape
    as the yfinance path with `source="alphavantage"`. None on any failure
    or when the response doesn't carry a Time Series block (free-tier rate
    limit returns a 200 with a `Note` or `Information` field instead)."""
    if not ALPHAVANTAGE_API_KEY:
        return None
    try:
        r = requests.get(
            AV_BASE,
            params={
                "function": "TIME_SERIES_DAILY",
                "symbol":   symbol,
                "apikey":   ALPHAVANTAGE_API_KEY,
                "outputsize": "compact",
            },
            timeout=AV_TIMEOUT,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        series = data.get("Time Series (Daily)")
        if not series or not isinstance(series, dict):
            return None
        dates = sorted(series.keys(), reverse=True)
        if len(dates) < 2:
            return None
        latest = float(series[dates[0]]["4. close"])
        prev = float(series[dates[1]]["4. close"])
        if prev == 0:
            return None
        return {
            "value": round(latest, 2),
            "change_24h_pct": round((latest - prev) / prev * 100, 2),
            "source": "alphavantage",
        }
    except Exception:
        return None


def _latest_and_change(yf_symbol: str, av_symbol: str) -> dict | None:
    """Primary path: yfinance. Fallback: Alpha Vantage proxy ETF. Only falls
    through when yfinance returns nothing AND an AV key is configured."""
    primary = _yfinance_latest_and_change(yf_symbol)
    if primary is not None:
        return primary
    return _alphavantage_latest_and_change(av_symbol)


def fetch() -> dict:
    """Fetch VIX + DXY snapshots. Always returns a dict; missing sections
    are nulled out and listed in `missing` / `partial` flags."""
    vix = _latest_and_change(VIX_SYMBOL, AV_VIX_SYMBOL)
    dxy = _latest_and_change(DXY_SYMBOL, AV_DXY_SYMBOL)
    missing = [name for name, val in (("vix", vix), ("dxy", dxy)) if val is None]
    return {
        "vix": vix,
        "dxy": dxy,
        "partial": bool(missing),
        "missing": missing,
    }
