from src.types import SwingPair, FibLevel
from src.confluence import cluster, score_zone, split_by_price

def _lvl(price, tf, ratio, kind="retracement"):
    pair = SwingPair(
        tf=tf, high_price=price+10, high_ts=1,
        low_price=price-10, low_ts=0, direction="up",
    )
    return FibLevel(price=price, tf=tf, ratio=ratio, kind=kind, pair=pair)

def test_cluster_merges_levels_within_radius():
    levels = [
        _lvl(100.0, "1d", 0.618),
        _lvl(100.5, "1w", 0.5),   # within 1.0 of prior
        _lvl(200.0, "1d", 0.382), # far away
    ]
    zones = cluster(levels, radius=1.0)
    assert len(zones) == 2
    assert len(zones[0].levels) == 2
    assert zones[0].min_price == 100.0
    assert zones[0].max_price == 100.5
    assert len(zones[1].levels) == 1

def test_cluster_single_level_produces_single_zone():
    levels = [_lvl(100.0, "1d", 0.5)]
    zones = cluster(levels, radius=1.0)
    assert len(zones) == 1
    assert zones[0].min_price == zones[0].max_price == 100.0

def test_score_zone_combines_tf_and_level_weights():
    levels = [
        _lvl(100.0, "1w", 0.618),  # 5 * 3 = 15
        _lvl(100.2, "1d", 0.5),    # 4 * 3 = 12
    ]
    zones = cluster(levels, radius=1.0)
    scored = score_zone(zones[0])
    assert scored.score == 15 + 12

def test_cluster_caps_zone_width_at_2x_radius():
    # 5 levels each 0.9 apart with radius=1.0 would chain to width=3.6 without the cap.
    # With the cap (2 * radius = 2.0), the zone must split once adding a new level
    # would make the total width exceed 2.0.
    levels = [
        _lvl(100.0, "1d", 0.618),
        _lvl(100.9, "1w", 0.5),
        _lvl(101.8, "1d", 0.382),
        _lvl(102.7, "1w", 0.786),
        _lvl(103.6, "1d", 0.236),
    ]
    zones = cluster(levels, radius=1.0)
    for z in zones:
        assert z.max_price - z.min_price <= 2.0, (
            f"zone width {z.max_price - z.min_price} exceeded 2x radius"
        )
    # Expect at least 2 zones because chaining alone would make one zone 3.6 wide.
    assert len(zones) >= 2

def test_split_by_price_separates_support_and_resistance():
    zone_below = _make_zone(90.0)
    zone_above = _make_zone(110.0)
    support, resistance = split_by_price([zone_below, zone_above], current_price=100.0)
    assert len(support) == 1 and support[0] is zone_below
    assert len(resistance) == 1 and resistance[0] is zone_above

def _make_zone(price):
    return cluster([_lvl(price, "1d", 0.5)], radius=1.0)[0]
