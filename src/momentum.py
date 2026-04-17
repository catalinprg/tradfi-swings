"""Momentum indicators (RSI, MACD, ATR-percentile) per timeframe.

Formulas lifted from catalinprg/financial-briefing's fetch_market.py
(unchanged — same Wilder smoothing for RSI, same 12/26/9 MACD defaults,
same EWM-ATR). Wrapped with a per-TF driver so the tradfi analyst sees
the same indicator row for every TF in the payload.

ATR is already computed in src/swings.py as a list of rolling values
for the clustering radius. This module re-derives its own ATR series via
pandas for the percentile helper — keeps the two concerns decoupled.
"""
from typing import Optional

import pandas as pd

from src.types import OHLC, Timeframe


def _series_from_ohlc(bars: list[OHLC]) -> pd.DataFrame:
    return pd.DataFrame({
        "Open":  [b.open for b in bars],
        "High":  [b.high for b in bars],
        "Low":   [b.low for b in bars],
        "Close": [b.close for b in bars],
    })


def calc_rsi(series: pd.Series, length: int = 14) -> pd.Series:
    """Wilder-smoothed RSI (matches financial-briefing)."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def calc_macd(
    series: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def calc_atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    """EWM-smoothed ATR. `df` must have High, Low, Close columns."""
    high_low = df["High"] - df["Low"]
    high_close = (df["High"] - df["Close"].shift()).abs()
    low_close = (df["Low"] - df["Close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / length, adjust=False).mean()


def _atr_percentile(atr_series: pd.Series, window: int = 90) -> Optional[float]:
    """ATR's current value as a percentile of its last `window` values.
    Tells the analyst whether volatility is compressed or expanding for
    this TF. Returns None if not enough data."""
    s = atr_series.dropna()
    if len(s) < 10:
        return None
    recent = s.iloc[-min(window, len(s)):]
    latest = s.iloc[-1]
    rank = (recent <= latest).sum() / len(recent)
    return round(float(rank) * 100, 1)


def _macd_cross_state(
    macd_line: pd.Series, signal_line: pd.Series, lookback: int = 3,
) -> str:
    """Summarize MACD vs signal in one of 4 tokens:
      - `bullish`        : macd > signal, no cross in lookback window
      - `fresh_bullish`  : macd crossed above signal within last `lookback` bars
      - `bearish`        : macd < signal, no recent cross
      - `fresh_bearish`  : macd crossed below signal within last `lookback` bars
    """
    if len(macd_line) < lookback + 2:
        return "unknown"
    diff = macd_line - signal_line
    d = diff.dropna()
    if d.empty:
        return "unknown"
    now = d.iloc[-1]
    recent_signs = (d.iloc[-(lookback + 1):] > 0).tolist()
    # Find a sign change within the lookback window
    flipped = any(recent_signs[i] != recent_signs[i - 1] for i in range(1, len(recent_signs)))
    if now > 0:
        return "fresh_bullish" if flipped else "bullish"
    else:
        return "fresh_bearish" if flipped else "bearish"


def compute_tf(bars: list[OHLC]) -> Optional[dict]:
    """Compute RSI/MACD/ATR-percentile for a single TF's OHLC. Returns a
    dict summarizing the latest bar, or None when there isn't enough data
    for the 14-period indicators."""
    if len(bars) < 30:
        return None
    df = _series_from_ohlc(bars)
    close = df["Close"]

    rsi_series = calc_rsi(close, length=14)
    macd_line, signal_line, hist = calc_macd(close)
    atr_series = calc_atr(df, length=14)

    rsi_latest = rsi_series.dropna()
    if rsi_latest.empty:
        return None
    hist_latest = hist.dropna()
    atr_latest = atr_series.dropna()

    return {
        "rsi_14":       round(float(rsi_latest.iloc[-1]), 1),
        "macd_hist":    round(float(hist_latest.iloc[-1]), 6) if not hist_latest.empty else None,
        "macd_cross":   _macd_cross_state(macd_line, signal_line),
        "atr_14":       round(float(atr_latest.iloc[-1]), 6) if not atr_latest.empty else None,
        "atr_percentile_90d": _atr_percentile(atr_series, window=90),
    }


def compute_per_tf(ohlc: dict[Timeframe, list[OHLC]]) -> dict[Timeframe, dict]:
    """Run compute_tf on every TF present in the fetched OHLC map. TFs with
    insufficient data are silently omitted from the output — the analyst
    checks for presence."""
    out: dict[Timeframe, dict] = {}
    for tf, bars in ohlc.items():
        m = compute_tf(bars)
        if m is not None:
            out[tf] = m
    return out
