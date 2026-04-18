from src.market_structure import analyze_structure, StructureState


def test_uptrend_bullish_with_bos():
    highs = [(1, 100.0), (3, 105.0), (5, 110.0)]
    lows  = [(2, 98.0),  (4, 102.0)]
    state = analyze_structure(highs, lows, current_price=109.0)
    assert state.bias == "bullish"
    assert state.last_bos is not None
    assert state.last_bos["direction"] == "bullish"
    assert state.invalidation_level == 102.0


def test_choch_bearish_when_bullish_breaks_last_hl():
    highs = [(1, 100.0), (3, 105.0)]
    lows  = [(2, 98.0),  (4, 102.0)]
    state = analyze_structure(highs, lows, current_price=101.0)
    assert state.bias == "bullish"
    assert state.last_choch is not None
    assert state.last_choch["direction"] == "bearish"


def test_bearish_mirror():
    highs = [(1, 110.0), (3, 105.0), (5, 100.0)]
    lows  = [(2, 102.0), (4, 98.0)]
    state = analyze_structure(highs, lows, current_price=99.0)
    assert state.bias == "bearish"
    assert state.last_bos is not None
    assert state.last_bos["direction"] == "bearish"
    assert state.invalidation_level == 100.0


def test_range_when_mixed_pivots():
    highs = [(1, 100.0), (3, 105.0)]
    lows  = [(2, 99.0),  (4, 98.0)]   # lows not strictly rising → not hl_seq
    state = analyze_structure(highs, lows, current_price=101.0)
    assert state.bias == "range"
    assert state.last_bos is None
    assert state.last_choch is None
    assert state.invalidation_level is None


def test_insufficient_pivots_returns_range():
    state = analyze_structure([(1, 100.0)], [(2, 98.0)], current_price=99.0)
    assert state.bias == "range"
    state2 = analyze_structure([], [], current_price=99.0)
    assert state2.bias == "range"
