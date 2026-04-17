"""Momentum indicators (RSI, MACD, ATR-percentile) per timeframe.

Standard formulas: Wilder-smoothed RSI(14), MACD(12, 26, 9), EWM ATR(14).
Wrapped with a per-TF driver so the tradfi analyst sees one indicator
row for every TF in the payload.

ATR is already computed in src/swings.py as a list of rolling values
for the clustering radius. This module re-derives its own ATR series via
pandas for the percentile helper — keeps the two concerns decoupled.
"""
from typing import Optional

import pandas as pd

from src.types import OHLC, Timeframe

# ATR-percentile windows picked so "compressed / expanding" means comparable
# things across TFs. 5m ≈ 1 trading day; 1h ≈ 1 trading week; 1d ≈ 3 months;
# 1w ≈ 1 year. Quoted in bars of the TF itself.
ATR_PCT_WINDOW_BY_TF: dict[Timeframe, int] = {
    "5m": 288,
    "1h": 168,
    "1d":  90,
    "1w":  52,
}
ATR_PCT_WINDOW_DEFAULT = 90


def _series_from_ohlc(bars: list[OHLC]) -> pd.DataFrame:
    return pd.DataFrame({
        "Open":  [b.open for b in bars],
        "High":  [b.high for b in bars],
        "Low":   [b.low for b in bars],
        "Close": [b.close for b in bars],
    })


def calc_rsi(series: pd.Series, length: int = 14) -> pd.Series:
    """Wilder-smoothed RSI."""
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


def _atr_percentile(atr_series: pd.Series, window: int = ATR_PCT_WINDOW_DEFAULT) -> Optional[float]:
    """ATR's current value as a percentile of its last `window` values.
    Tells the analyst whether volatility is compressed or expanding for
    this TF. Returns None if not enough data. Window is calibrated per-TF
    via ATR_PCT_WINDOW_BY_TF so the label means comparable things across
    1w and 5m."""
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


def compute_tf(bars: list[OHLC], atr_window: int = ATR_PCT_WINDOW_DEFAULT) -> Optional[dict]:
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
        "rsi_14":           round(float(rsi_latest.iloc[-1]), 1),
        "macd_hist":        round(float(hist_latest.iloc[-1]), 6) if not hist_latest.empty else None,
        "macd_cross":       _macd_cross_state(macd_line, signal_line),
        "atr_14":           round(float(atr_latest.iloc[-1]), 6) if not atr_latest.empty else None,
        "atr_percentile":   _atr_percentile(atr_series, window=atr_window),
        "atr_pct_window":   atr_window,
    }


def compute_per_tf(ohlc: dict[Timeframe, list[OHLC]]) -> dict[Timeframe, dict]:
    """Run compute_tf on every TF present in the fetched OHLC map. TFs with
    insufficient data are silently omitted from the output — the analyst
    checks for presence. ATR-percentile window is per-TF so the reading is
    comparable across 5m / 1h / 1d / 1w."""
    out: dict[Timeframe, dict] = {}
    for tf, bars in ohlc.items():
        window = ATR_PCT_WINDOW_BY_TF.get(tf, ATR_PCT_WINDOW_DEFAULT)
        m = compute_tf(bars, atr_window=window)
        if m is not None:
            out[tf] = m
    return out
