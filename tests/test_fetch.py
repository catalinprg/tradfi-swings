import pandas as pd

from src.fetch import (
    MIN_BARS,
    TF_TO_YF,
    SESSION_LIMITED_CLASSES,
    _df_to_ohlc,
)
from src.types import OHLC


def test_tf_to_yf_has_expected_timeframes():
    assert set(TF_TO_YF.keys()) == {"5m", "1h", "1d", "1w"}
    for tf, (interval, period) in TF_TO_YF.items():
        assert isinstance(interval, str) and interval
        assert isinstance(period, str) and period


def test_min_bars_covers_every_tf():
    assert set(MIN_BARS.keys()) == set(TF_TO_YF.keys())
    for v in MIN_BARS.values():
        assert v >= 1


def test_session_limited_classes_are_equities():
    # RTH filter only applies to asset classes with extended hours — keep
    # forex and commodities on the full 24h session.
    assert SESSION_LIMITED_CLASSES == frozenset({"index", "stock"})


def test_df_to_ohlc_converts_tz_naive_dataframe_to_utc_ms():
    idx = pd.DatetimeIndex(["2026-01-01 00:00", "2026-01-01 01:00"])
    df = pd.DataFrame(
        {"Open":[100.0, 101.0], "High":[102.0, 103.0],
         "Low":[99.0, 100.0], "Close":[101.0, 102.0], "Volume":[10, 20]},
        index=idx,
    )
    bars = _df_to_ohlc(df)
    assert len(bars) == 2
    assert bars[0].ts == int(pd.Timestamp("2026-01-01 00:00", tz="UTC").timestamp() * 1000)
    assert bars[0].open == 100.0
    assert bars[1].close == 102.0


def test_df_to_ohlc_drops_nan_rows():
    idx = pd.DatetimeIndex(["2026-01-01 00:00", "2026-01-01 01:00"])
    df = pd.DataFrame(
        {"Open":[100.0, float("nan")], "High":[102.0, 103.0],
         "Low":[99.0, 100.0], "Close":[101.0, 102.0], "Volume":[10, 20]},
        index=idx,
    )
    bars = _df_to_ohlc(df)
    assert len(bars) == 1
    assert bars[0].open == 100.0


def test_df_to_ohlc_handles_empty_and_none():
    assert _df_to_ohlc(None) == []
    assert _df_to_ohlc(pd.DataFrame()) == []
