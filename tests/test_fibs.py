import math
from src.types import SwingPair, RETRACEMENT_RATIOS, EXTENSION_RATIOS
from src.fibs import compute_levels

def _pair(direction, h=200.0, l=100.0):
    return SwingPair(
        tf="1d",
        high_price=h, high_ts=1,
        low_price=l, low_ts=2 if direction == "down" else 0,
        direction=direction,
    )

def test_retracement_midpoint_equals_arithmetic_mean():
    pair = _pair("up")
    levels = compute_levels(pair)
    r50 = next(x for x in levels if x.ratio == 0.5 and x.kind == "retracement")
    assert math.isclose(r50.price, 150.0)

def test_retracement_all_between_high_and_low():
    pair = _pair("up")
    levels = compute_levels(pair)
    retracements = [x for x in levels if x.kind == "retracement"]
    assert len(retracements) == len(RETRACEMENT_RATIOS)
    for l in retracements:
        assert 100.0 <= l.price <= 200.0

def test_upswing_extensions_project_above_high():
    pair = _pair("up", h=200, l=100)
    levels = compute_levels(pair)
    extensions = [x for x in levels if x.kind == "extension"]
    assert len(extensions) == len(EXTENSION_RATIOS)
    for l in extensions:
        assert l.price > 200.0
    e1272 = next(x for x in extensions if x.ratio == 1.272)
    assert math.isclose(e1272.price, 200 + 100 * 0.272)

def test_downswing_extensions_project_below_low():
    pair = _pair("down", h=200, l=100)
    levels = compute_levels(pair)
    extensions = [x for x in levels if x.kind == "extension"]
    for l in extensions:
        assert l.price < 100.0
    e1618 = next(x for x in extensions if x.ratio == 1.618)
    assert math.isclose(e1618.price, 100 - 100 * 0.618)

def test_level_carries_source_pair_and_tf():
    pair = _pair("up")
    levels = compute_levels(pair)
    for l in levels:
        assert l.tf == "1d"
        assert l.pair is pair
