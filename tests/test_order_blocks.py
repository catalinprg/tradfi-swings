from src.order_blocks import detect_order_blocks
from src.types import OHLC

def _b(ts, o, h, l, c, v=1.0):
    return OHLC(ts=ts, open=o, high=h, low=l, close=c, volume=v)

def test_bullish_ob_at_last_down_candle_before_displacement():
    atr = 1.0
    bars = [
        _b(0, 100, 101, 99, 100.5),
        _b(1, 100.5, 102, 100, 101.5),
        _b(2, 101.5, 101.8, 101, 101.2),
        _b(3, 101.2, 101.5, 100.5, 100.7),   # down candle (OB candidate)
        _b(4, 100.7, 104.5, 100.7, 104.3),   # displacement up, breaks 102
    ]
    obs = detect_order_blocks(bars, tf="1h", atr_14=atr, stale_after=100)
    bulls = [o for o in obs if o.type == "OB_BULL"]
    assert len(bulls) == 1
    assert bulls[0].formation_ts == bars[3].ts

def test_no_ob_below_displacement_threshold():
    atr = 2.0
    bars = [
        _b(0, 100, 101, 99, 100),
        _b(1, 100, 102, 99, 101),
        _b(2, 101, 101, 100, 100.5),
        _b(3, 100.5, 102, 100.3, 101.8),   # range 1.7 < 1.5×ATR=3.0
    ]
    obs = detect_order_blocks(bars, tf="1h", atr_14=atr, stale_after=100)
    assert obs == []

def test_bearish_ob_mirror():
    atr = 1.0
    bars = [
        _b(0, 100, 100.5, 99.5, 100.3),
        _b(1, 100.3, 100.8, 99, 99.5),       # swing low ~99
        _b(2, 99.5, 99.8, 99.2, 99.6),
        _b(3, 99.6, 100.1, 99.4, 100.0),     # up candle (OB candidate)
        _b(4, 100.0, 100.0, 95.5, 95.8),     # displacement down, breaks 99
    ]
    obs = detect_order_blocks(bars, tf="1h", atr_14=atr, stale_after=100)
    bears = [o for o in obs if o.type == "OB_BEAR"]
    assert len(bears) == 1
    assert bears[0].formation_ts == bars[3].ts

def test_mitigation_flag():
    atr = 1.0
    bars = [
        _b(0, 100, 101, 99, 100.5),
        _b(1, 100.5, 102, 100, 101.5),
        _b(2, 101.5, 101.8, 101, 101.2),
        _b(3, 101.2, 101.5, 100.5, 100.7),
        _b(4, 100.7, 104.5, 100.7, 104.3),
        _b(5, 104.3, 104.5, 103, 103.5),
        _b(6, 103.5, 103.5, 100.8, 101.0),   # retraces into OB [100.5, 101.5]
    ]
    obs = detect_order_blocks(bars, tf="1h", atr_14=atr, stale_after=100)
    assert any(o.type == "OB_BULL" and o.mitigated for o in obs)

def test_stale_flag():
    atr = 1.0
    bars = [
        _b(0, 100, 101, 99, 100.5),
        _b(1, 100.5, 102, 100, 101.5),
        _b(2, 101.5, 101.8, 101, 101.2),
        _b(3, 101.2, 101.5, 100.5, 100.7),
        _b(4, 100.7, 104.5, 100.7, 104.3),
    ]
    # Add 155 bars far above the OB, never returning
    for i in range(5, 160):
        bars.append(_b(i, 109, 112, 109, 110.5))
    obs = detect_order_blocks(bars, tf="1h", atr_14=atr, stale_after=100)
    bulls = [o for o in obs if o.type == "OB_BULL"]
    assert bulls[0].stale is True
    assert bulls[0].mitigated is False

def test_no_ob_when_displacement_does_not_break_prior_swing():
    atr = 1.0
    bars = [
        _b(0, 100, 105, 99, 104),            # swing high 105
        _b(1, 104, 104.5, 103, 103.5),
        _b(2, 103.5, 104, 103, 103.3),
        _b(3, 103.3, 103.5, 102.5, 102.8),   # down candle
        _b(4, 102.8, 104.7, 102.8, 104.7),   # range 1.9 > 1.5 ATR BUT close 104.7 < prior high 105
    ]
    obs = detect_order_blocks(bars, tf="1h", atr_14=atr, stale_after=100)
    assert obs == []
