"""Market-wide context snapshot — VIX and DXY.

Fetched once per pipeline run and attached to every instrument's payload as
`market_context`. The analyst uses VIX for risk-on/off framing (equities,
stocks) and DXY for USD strength framing (forex pairs, commodities, and
USD-correlated equity flows).

Contract — the returned dict mirrors the crypto pipeline's per-section null
pattern so that a single upstream failure degrades gracefully:

    {
      "vix": {"value": float, "change_24h_pct": float} | None,
      "dxy": {"value": float, "change_24h_pct": float} | None,
      "partial": bool,
      "missing": ["vix" | "dxy", ...],
    }
"""
import time

import yfinance as yf

VIX_SYMBOL = "^VIX"
DXY_SYMBOL = "DX-Y.NYB"


def _latest_and_change(symbol: str) -> dict | None:
    """Fetch the last ~5 daily closes of `symbol`. Return latest close and
    24h % change (today's close vs prior close). Returns None on any failure
    or when insufficient data came back."""
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
            }
        except Exception:
            if attempt == 1:
                return None
            time.sleep(2)
    return None


def fetch() -> dict:
    """Fetch VIX + DXY snapshots. Always returns a dict; missing sections
    are nulled out and listed in `missing` / `partial` flags."""
    vix = _latest_and_change(VIX_SYMBOL)
    dxy = _latest_and_change(DXY_SYMBOL)
    missing = [name for name, val in (("vix", vix), ("dxy", dxy)) if val is None]
    return {
        "vix": vix,
        "dxy": dxy,
        "partial": bool(missing),
        "missing": missing,
    }
