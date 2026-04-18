from src.levels import cluster_levels, split_by_price
from src.types import Level

def _lvl(price, source, tf="1d", strength=0.5, age=0, lo=None, hi=None):
    lo = lo if lo is not None else price
    hi = hi if hi is not None else price
    return Level(price=price, min_price=lo, max_price=hi,
                 source=source, tf=tf, strength=strength, age_bars=age)

def test_cluster_groups_multiple_sources():
    levels = [
        _lvl(100.0, "FIB_618", tf="1d"),
        _lvl(100.1, "LIQ_BSL", tf="1d"),
        _lvl(100.05, "FVG_BULL", tf="1d", lo=99.8, hi=100.2),
    ]
    zones = cluster_levels(levels, radius=0.3)
    assert len(zones) == 1
    assert zones[0].source_count == 3
    assert zones[0].classification == "strong"

def test_structural_pivot_when_ms_meets_any_other():
    levels = [
        _lvl(100.0, "MS_BOS_LEVEL", tf="1d"),
        _lvl(100.05, "LIQ_BSL",     tf="1d"),
    ]
    zones = cluster_levels(levels, radius=0.5)
    assert zones[0].classification == "structural_pivot"

def test_empty_input_returns_empty_list():
    assert cluster_levels([], radius=1.0) == []

def test_split_by_price_partitions_correctly():
    levels = [_lvl(90.0, "FIB_618"), _lvl(110.0, "LIQ_BSL")]
    zones = cluster_levels(levels, radius=0.1)
    sup, res = split_by_price(zones, current_price=100.0)
    assert all(z.mid < 100.0 for z in sup)
    assert all(z.mid >= 100.0 for z in res)
