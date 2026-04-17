import pandas as pd

from src.fetch import (
    MIN_BARS,
    TF_TO_YF,
    _df_to_ohlc,
    _resample_1h_to_4h,
)
from src.types import OHLC


def test_tf_to_yf_has_expected_five_timeframes():
    assert set(TF_TO_YF.keys()) == {"5m", "1h", "4h", "1d", "1w"}
    for tf, (interval, period) in TF_TO_YF.items():
        assert isinstance(interval, str) and interval
        assert isinstance(period, str) and period


def test_min_bars_covers_every_tf():
    assert set(MIN_BARS.keys()) == set(TF_TO_YF.keys())
    for v in MIN_BARS.values():
        assert v >= 1


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


def _bar(ts_ms, o=100.0, h=101.0, l=99.0, c=100.5, v=1.0):
    return OHLC(ts=ts_ms, open=o, high=h, low=l, close=c, volume=v)


def test_resample_1h_to_4h_groups_by_4_and_aggregates():
    # 8 consecutive 1h bars aligned to 00:00 UTC on 2026-01-01
    base = int(pd.Timestamp("2026-01-01 00:00", tz="UTC").timestamp() * 1000)
    HOUR = 3600 * 1000
    bars = [
        _bar(base + 0 * HOUR, o=100, h=110, l=95,  c=105, v=1),
        _bar(base + 1 * HOUR, o=105, h=108, l=100, c=106, v=1),
        _bar(base + 2 * HOUR, o=106, h=115, l=104, c=112, v=1),
        _bar(base + 3 * HOUR, o=112, h=116, l=110, c=114, v=1),
        # Second bucket
        _bar(base + 4 * HOUR, o=114, h=118, l=112, c=115, v=1),
        _bar(base + 5 * HOUR, o=115, h=120, l=114, c=119, v=1),
        _bar(base + 6 * HOUR, o=119, h=122, l=117, c=120, v=1),
        _bar(base + 7 * HOUR, o=120, h=121, l=118, c=119, v=1),
    ]
    resampled = _resample_1h_to_4h(bars)
    assert len(resampled) == 2
    assert resampled[0].open == 100
    assert resampled[0].high == 116
    assert resampled[0].low == 95
    assert resampled[0].close == 114
    assert resampled[0].volume == 4
    assert resampled[1].open == 114
    assert resampled[1].high == 122
    assert resampled[1].low == 112
    assert resampled[1].close == 119


def test_resample_1h_to_4h_drops_incomplete_buckets():
    # Only 3 bars of a 4-bar bucket → bucket dropped entirely
    base = int(pd.Timestamp("2026-01-01 00:00", tz="UTC").timestamp() * 1000)
    HOUR = 3600 * 1000
    bars = [_bar(base + i * HOUR) for i in range(3)]
    assert _resample_1h_to_4h(bars) == []


def test_resample_1h_to_4h_empty_input():
    assert _resample_1h_to_4h([]) == []
