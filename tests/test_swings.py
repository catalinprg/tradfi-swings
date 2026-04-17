import math
from src.types import OHLC
from src.swings import atr, detect_pivots, build_pairs, detect_swings

def _c(ts, h, l, o=None, c=None):
    o = o if o is not None else (h + l) / 2
    c = c if c is not None else (h + l) / 2
    return OHLC(ts=ts, open=o, high=h, low=l, close=c, volume=0)

def test_atr_basic():
    # 15 candles, each with TR=100, ATR(14) should be 100
    bars = [_c(i, 100, 0) for i in range(15)]
    result = atr(bars, period=14)
    assert len(result) == 15
    # First 14 are None (not enough data); 15th is 100
    assert result[14] is not None
    assert math.isclose(result[14], 100.0, rel_tol=1e-6)

def test_detect_pivots_finds_sine_wave_extremes():
    # sin(i * pi/5) peaks at i=2.5, 12.5, 22.5; troughs at i=7.5, 17.5
    # With n=3 the valid pivot range is [3, 21], so peak@2 and peak@22 fall outside.
    bars = []
    for i in range(25):
        price = 100 + 10 * math.sin(i * math.pi / 5)
        bars.append(_c(i, price + 0.1, price - 0.1, price, price))
    highs, lows = detect_pivots(bars, n=3)
    high_indices = [h[0] for h in highs]
    low_indices = [l[0] for l in lows]
    assert any(11 <= i <= 14 for i in high_indices), f"got highs {high_indices}"
    assert any(6 <= i <= 9 for i in low_indices), f"got lows {low_indices}"
    assert any(15 <= i <= 19 for i in low_indices), f"got lows {low_indices}"

def test_build_pairs_pairs_adjacent_opposite_pivots():
    # Pivots: high@idx5, low@idx10, high@idx15, low@idx20
    bars = [_c(i, 100, 100) for i in range(25)]
    highs = [(5, 110.0), (15, 120.0)]
    lows = [(10, 95.0), (20, 90.0)]
    pairs = build_pairs(bars, highs, lows, tf="1d")
    # Expect 3 pairs: (h5,l10), (l10,h15), (h15,l20)
    assert len(pairs) == 3
    assert pairs[0].high_ts == 5 and pairs[0].low_ts == 10
    assert pairs[0].direction == "down"  # low more recent
    assert pairs[1].high_ts == 15 and pairs[1].low_ts == 10
    assert pairs[1].direction == "up"    # high more recent
    assert pairs[2].high_ts == 15 and pairs[2].low_ts == 20
    assert pairs[2].direction == "down"

def test_detect_swings_returns_last_three_pairs():
    bars = []
    for i in range(50):
        price = 100 + 10 * math.sin(i * math.pi / 5)
        bars.append(_c(i, price + 0.1, price - 0.1, price, price))
    pairs = detect_swings(bars, tf="1d", max_pairs=3)
    assert 1 <= len(pairs) <= 3
    for p in pairs:
        assert p.tf == "1d"
        assert p.high_price > p.low_price
