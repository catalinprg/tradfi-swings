from src.fvg import detect_fvgs, expected_bar_ms_for
from src.types import OHLC


def _b(ts, h, l, c=None):
    c = c if c is not None else (h + l) / 2
    return OHLC(ts=ts, open=c, high=h, low=l, close=c, volume=1.0)


def test_bullish_fvg_detected_within_continuous_session():
    # 1h bars, contiguous
    bars = [
        _b(0,            100, 98),
        _b(3_600_000,    103, 99, c=102.5),
        _b(7_200_000,    105, 102),
    ]
    fvgs = detect_fvgs(bars, tf="1h", atr_14=1.0, stale_after=100, expected_bar_ms=3_600_000)
    bulls = [f for f in fvgs if f.type == "FVG_BULL"]
    assert len(bulls) == 1


def test_fvg_skipped_when_gap_spans_session_close_to_open():
    # Third bar is ~16h after the middle bar — session break for equities
    bars = [
        _b(0,               100, 98),
        _b(3_600_000,       103, 99, c=102.5),
        _b(3_600_000 + 16*3_600_000, 105, 102),
    ]
    fvgs = detect_fvgs(bars, tf="1h", atr_14=1.0, stale_after=100, expected_bar_ms=3_600_000)
    assert fvgs == []


def test_fvg_not_skipped_when_expected_bar_ms_is_none():
    # Same bars as above, but for 24×5 assets the filter is disabled
    bars = [
        _b(0,               100, 98),
        _b(3_600_000,       103, 99, c=102.5),
        _b(3_600_000 + 16*3_600_000, 105, 102),
    ]
    fvgs = detect_fvgs(bars, tf="1h", atr_14=1.0, stale_after=100, expected_bar_ms=None)
    assert len([f for f in fvgs if f.type == "FVG_BULL"]) == 1


def test_fvg_mitigated_flag():
    bars = [
        _b(0,            100, 98),
        _b(3_600_000,    103, 99, c=102.5),
        _b(7_200_000,    105, 102),
        _b(10_800_000,   104, 100),   # returns into gap
    ]
    fvgs = detect_fvgs(bars, tf="1h", atr_14=1.0, stale_after=100, expected_bar_ms=3_600_000)
    assert any(f.type == "FVG_BULL" and f.mitigated for f in fvgs)


def test_fvg_stale_flag_triggers_past_threshold():
    bars = [_b(0, 100, 98), _b(3_600_000, 103, 99, c=102.5), _b(7_200_000, 105, 102)]
    for i in range(3, 200):
        bars.append(_b((3 + i) * 3_600_000, 110, 108))
    fvgs = detect_fvgs(bars, tf="1h", atr_14=1.0, stale_after=100, expected_bar_ms=3_600_000)
    bull = next(f for f in fvgs if f.type == "FVG_BULL")
    assert bull.stale is True
    assert bull.mitigated is False


def test_min_gap_filter_suppresses_micro_gaps():
    # atr_14=10, min_gap = 0.5; a 0.01-wide gap must be filtered out
    bars = [_b(0, 100, 98), _b(3_600_000, 102, 99, c=101.5), _b(7_200_000, 103, 100.01)]
    fvgs = detect_fvgs(bars, tf="1h", atr_14=10.0, stale_after=100, expected_bar_ms=3_600_000)
    assert fvgs == []


def test_atr_zero_disables_min_gap_filter():
    # atr_14=0 → min_gap=0 → any non-zero gap passes
    bars = [_b(0, 100, 98), _b(3_600_000, 103, 99, c=102.5), _b(7_200_000, 105, 100.001)]
    fvgs = detect_fvgs(bars, tf="1h", atr_14=0.0, stale_after=100, expected_bar_ms=3_600_000)
    assert len([f for f in fvgs if f.type == "FVG_BULL"]) == 1


def test_expected_bar_ms_for_forex_returns_none():
    assert expected_bar_ms_for("1h", "forex") is None


def test_expected_bar_ms_for_index_returns_cadence():
    assert expected_bar_ms_for("1h", "index") == 60 * 60 * 1000
    assert expected_bar_ms_for("5m", "stock") == 5 * 60 * 1000


def test_expected_bar_ms_for_weekly_always_none():
    assert expected_bar_ms_for("1w", "index") is None
    assert expected_bar_ms_for("1w", "forex") is None
