import math

import pandas as pd

from src import momentum
from src.types import OHLC


def _bars(closes: list[float]) -> list[OHLC]:
    """Minimal OHLC list from a close-price series for indicator tests.
    High/Low derived as ±0.1% so ATR has non-zero TR."""
    out = []
    for i, c in enumerate(closes):
        out.append(OHLC(
            ts=i * 60_000,
            open=c,
            high=c * 1.001,
            low=c * 0.999,
            close=c,
            volume=1,
        ))
    return out


def test_calc_rsi_trending_up_approaches_high_reading():
    # Steady gains → RSI should climb toward ~85+
    series = pd.Series([100 + i for i in range(60)])
    rsi = momentum.calc_rsi(series, length=14).dropna()
    assert rsi.iloc[-1] > 80


def test_calc_rsi_trending_down_approaches_low_reading():
    series = pd.Series([100 - i for i in range(60)])
    rsi = momentum.calc_rsi(series, length=14).dropna()
    assert rsi.iloc[-1] < 20


def test_calc_macd_returns_three_series_of_equal_length():
    series = pd.Series([100 + math.sin(i / 3) * 5 for i in range(100)])
    line, signal, hist = momentum.calc_macd(series)
    assert len(line) == len(signal) == len(hist) == 100
    # Histogram = line - signal
    assert math.isclose(hist.iloc[-1], line.iloc[-1] - signal.iloc[-1], rel_tol=1e-9)


def test_compute_tf_returns_none_when_insufficient_bars():
    assert momentum.compute_tf(_bars([100.0] * 10)) is None


def test_compute_tf_happy_path_trending_up():
    bars = _bars([100 + i * 0.5 for i in range(60)])
    out = momentum.compute_tf(bars)
    assert out is not None
    assert 0 <= out["rsi_14"] <= 100
    assert out["rsi_14"] > 70  # steady uptrend
    assert out["macd_cross"] in {"bullish", "fresh_bullish"}
    assert out["atr_14"] > 0
    assert out["atr_percentile_90d"] is None or 0 <= out["atr_percentile_90d"] <= 100


def test_compute_per_tf_runs_for_every_tf_with_enough_bars():
    bars = _bars([100 + i * 0.2 for i in range(60)])
    ohlc = {"1w": bars, "1d": bars, "4h": _bars([100.0] * 5)}  # 4h too short
    out = momentum.compute_per_tf(ohlc)
    assert set(out.keys()) == {"1w", "1d"}  # 4h omitted


def test_macd_cross_state_detects_fresh_flip():
    # Line below signal for 3 bars, then crosses above in the last 2
    line = pd.Series([-1.0, -0.8, -0.5, -0.2, 0.1, 0.3])   # flip at bar 4
    signal = pd.Series([0.0] * 6)
    state = momentum._macd_cross_state(line, signal, lookback=3)
    assert state == "fresh_bullish"


def test_macd_cross_state_detects_stable_bullish():
    # Line consistently above signal for 6 bars — no flip
    line = pd.Series([0.3, 0.4, 0.5, 0.6, 0.7, 0.8])
    signal = pd.Series([0.1] * 6)
    state = momentum._macd_cross_state(line, signal, lookback=3)
    assert state == "bullish"
