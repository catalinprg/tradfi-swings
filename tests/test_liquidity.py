"""Tests for the liquidity-pool proxy layer."""
from src.liquidity import compute_pools, _cluster_by_price, _is_swept
from src.types import OHLC, SwingPair


def _sp(tf, h, h_ts, l, l_ts, direction="down"):
    return SwingPair(
        tf=tf,
        high_price=h, high_ts=h_ts,
        low_price=l, low_ts=l_ts,
        direction=direction,
    )


def _bar(ts, high, low, close=None, open_=None, volume=0.0):
    c = close if close is not None else (high + low) / 2
    o = open_ if open_ is not None else c
    return OHLC(ts=ts, open=o, high=high, low=low, close=c, volume=volume)


def test_cluster_by_price_groups_within_radius():
    pivots = [
        (1.0850, "1h", 1),
        (1.0853, "1h", 2),   # within radius → same cluster
        (1.0920, "1d", 3),   # outside → new cluster
    ]
    clusters = _cluster_by_price(pivots, radius=0.0020)
    assert len(clusters) == 2
    assert len(clusters[0]) == 2


def test_compute_pools_works_on_small_decimal_forex_prices():
    # EUR/USD-style, typical 4-5 dp.
    pairs = [
        _sp("1d", h=1.0920, h_ts=1_000_000, l=1.0780, l_ts=1_500_000),
        _sp("1h", h=1.0918, h_ts=2_000_000, l=1.0790, l_ts=2_500_000),
    ]
    ohlc = {
        "1d": [
            _bar(500_000, high=1.0900, low=1.0770),
            _bar(1_000_000, high=1.0920, low=1.0780),
            _bar(2_000_000, high=1.0918, low=1.0790),
            _bar(3_000_000, high=1.0870, low=1.0820),
        ],
    }
    pools = compute_pools(
        swing_pairs=pairs, ohlc=ohlc,
        current_price=1.0850, daily_atr=0.0060,
        now_ms=3_000_000,
    )
    # Both ~1.0920 highs should cluster into one BSL pool.
    assert len(pools["buy_side"]) == 1
    bsl = pools["buy_side"][0]
    assert bsl["touches"] == 2
    assert bsl["swept"] is False


def test_compute_pools_produces_buy_side_above_and_sell_side_below():
    pairs = [_sp("1d", h=5400, h_ts=1_000_000, l=5100, l_ts=1_500_000)]
    ohlc = {
        "1d": [
            _bar(500_000, high=5380, low=5080),
            _bar(1_000_000, high=5400, low=5100),
            _bar(2_000_000, high=5250, low=5150),   # no sweep
        ],
    }
    pools = compute_pools(
        swing_pairs=pairs, ohlc=ohlc,
        current_price=5200.0, daily_atr=60.0,
        now_ms=2_000_000,
    )
    assert pools["buy_side"][0]["type"] == "BSL"
    assert pools["buy_side"][0]["swept"] is False
    assert pools["sell_side"][0]["type"] == "SSL"


def test_compute_pools_tf_stacking_uses_tradfi_weights():
    # TF_WEIGHTS for tradfi: {1w: 5, 1d: 4, 1h: 2, 5m: 1} (4h dropped).
    pairs = [
        _sp("1w", h=200.0, h_ts=1000, l=180, l_ts=1100),
        _sp("1d", h=200.2, h_ts=2000, l=185, l_ts=2100),
        _sp("1h", h=199.9, h_ts=3000, l=190, l_ts=3100),
    ]
    ohlc = {"1d": [_bar(500, 199, 179), _bar(3500, 198, 181)]}
    pools = compute_pools(
        swing_pairs=pairs, ohlc=ohlc,
        current_price=195.0, daily_atr=4.0,
        now_ms=3500,
    )
    assert len(pools["buy_side"]) == 1
    pool = pools["buy_side"][0]
    assert pool["touches"] == 3
    assert set(pool["tfs"]) == {"1w", "1d", "1h"}
    # (5 + 4 + 2) × 3 = 33
    assert pool["strength_score"] == 33


def test_compute_pools_filters_far_distance():
    pairs = [_sp("1d", h=150, h_ts=1000, l=95, l_ts=1100)]
    ohlc = {"1d": [_bar(500, 100, 90)]}
    pools = compute_pools(
        swing_pairs=pairs, ohlc=ohlc,
        current_price=100.0, daily_atr=2.0,
        now_ms=2000,
    )
    assert pools["buy_side"] == []
    assert len(pools["sell_side"]) == 1


def test_compute_pools_ranks_unswept_before_swept():
    pairs = [
        _sp("1w", h=110, h_ts=1000, l=90, l_ts=1100),   # swept
        _sp("1h", h=108, h_ts=2000, l=95, l_ts=2100),   # unswept
    ]
    ohlc = {
        "1h": [
            _bar(500, 100, 90),
            _bar(1000, 110, 95),
            _bar(1500, 112, 95),   # sweeps 110
            _bar(2000, 108, 95),
            _bar(2500, 107, 100),
        ],
    }
    pools = compute_pools(
        swing_pairs=pairs, ohlc=ohlc,
        current_price=105.0, daily_atr=3.0,
        now_ms=2500,
    )
    buy = pools["buy_side"]
    assert len(buy) == 2
    assert buy[0]["swept"] is False
    assert buy[1]["swept"] is True


def test_is_swept_direct():
    ohlc = {"1h": [_bar(500, 104, 94), _bar(1000, 105, 94), _bar(2000, 110, 95)]}
    assert _is_swept(105, 1000, "BSL", ohlc) is True
    assert _is_swept(90, 1000, "SSL", ohlc) is False


def test_compute_pools_handles_empty_inputs():
    assert compute_pools([], {}, 100.0, 1.0) == {"buy_side": [], "sell_side": []}
