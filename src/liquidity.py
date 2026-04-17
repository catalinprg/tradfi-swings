"""Liquidity-pool proxy layer derived from swing pivots.

Liquidity zones are standard ICT / SMC concepts: resting stops cluster above
swing highs (buy-side liquidity, "BSL") and below swing lows (sell-side,
"SSL"). Price is drawn toward unswept pools because that's where size can
get filled. Pools that have already been swept lose their pulling power.

We derive a proxy for these pools directly from the pivots the swings
module already detects — no extra data source needed.

**This is a separate layer from Fibonacci confluence, not a replacement.**
A fib zone answers "where is the structural math?"; a liquidity pool
answers "where are the stops?". They're orthogonal — their real value is
reinforcement on overlap.

**TradFi caveat.** For forex specifically, there's no consolidated order
book and no public aggregate broker positioning. The swing-based pool proxy
still carries directional meaning (technical-stop placement clusters around
visible highs/lows regardless of venue) but the agent prompt treats forex
pools with softer language (`potențial magnet de liquidity`) than equity/
index pools.

Pool shape — see the crypto-swings liquidity module; the contract is
identical.
"""
from __future__ import annotations

import time

from src.types import OHLC, SwingPair, TF_WEIGHTS, Timeframe

RADIUS_ATR_MULTIPLIER = 0.25
MAX_ZONE_WIDTH_MULTIPLIER = 2.0
MAX_POOL_DISTANCE_PCT = 0.20
MAX_POOLS_PER_SIDE = 6


def _cluster_by_price(
    pivots: list[tuple[float, Timeframe, int]],
    radius: float,
) -> list[list[tuple[float, Timeframe, int]]]:
    """Same cluster-merging rule as fib confluence: within-radius AND total
    cluster width <= 2 * radius."""
    if not pivots:
        return []
    sorted_pivots = sorted(pivots, key=lambda x: x[0])
    clusters: list[list[tuple[float, Timeframe, int]]] = [[sorted_pivots[0]]]
    max_width = radius * MAX_ZONE_WIDTH_MULTIPLIER
    for p in sorted_pivots[1:]:
        within_radius = p[0] - clusters[-1][-1][0] <= radius
        within_width = p[0] - clusters[-1][0][0] <= max_width
        if within_radius and within_width:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    return clusters


def _pick_sweep_tf(
    ohlc: dict[Timeframe, list[OHLC]], pivot_ts_ms: int
) -> Timeframe | None:
    """Smallest-resolution TF whose window covers the pivot's timestamp."""
    tf_order: list[Timeframe] = ["5m", "1h", "1d", "1w"]
    for tf in tf_order:
        bars = ohlc.get(tf)
        if bars and bars[0].ts <= pivot_ts_ms:
            return tf
    return None


def _is_swept(
    pivot_price: float,
    pivot_ts_ms: int,
    pool_type: str,
    ohlc: dict[Timeframe, list[OHLC]],
) -> bool:
    tf = _pick_sweep_tf(ohlc, pivot_ts_ms)
    if tf is None:
        return False
    for b in ohlc[tf]:
        if b.ts <= pivot_ts_ms:
            continue
        if pool_type == "BSL" and b.high > pivot_price:
            return True
        if pool_type == "SSL" and b.low < pivot_price:
            return True
    return False


def _strength_score(tfs: list[Timeframe], touches: int) -> int:
    return sum(TF_WEIGHTS.get(tf, 1) for tf in tfs) * touches


def compute_pools(
    swing_pairs: list[SwingPair],
    ohlc: dict[Timeframe, list[OHLC]],
    current_price: float,
    daily_atr: float,
    *,
    now_ms: int | None = None,
) -> dict[str, list[dict]]:
    """Derive BSL / SSL pools from swings. Filter to within ±20% of price,
    sort unswept-first then by strength, cap at MAX_POOLS_PER_SIDE."""
    if not swing_pairs or current_price <= 0 or daily_atr <= 0:
        return {"buy_side": [], "sell_side": []}

    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    radius = daily_atr * RADIUS_ATR_MULTIPLIER

    highs: list[tuple[float, Timeframe, int]] = [
        (p.high_price, p.tf, p.high_ts) for p in swing_pairs
    ]
    lows: list[tuple[float, Timeframe, int]] = [
        (p.low_price, p.tf, p.low_ts) for p in swing_pairs
    ]

    buy_side = _build_pools(highs, "BSL", radius, ohlc, current_price, now_ms)
    sell_side = _build_pools(lows, "SSL", radius, ohlc, current_price, now_ms)

    return {
        "buy_side": buy_side[:MAX_POOLS_PER_SIDE],
        "sell_side": sell_side[:MAX_POOLS_PER_SIDE],
    }


def _build_pools(
    pivots: list[tuple[float, Timeframe, int]],
    pool_type: str,
    radius: float,
    ohlc: dict[Timeframe, list[OHLC]],
    current_price: float,
    now_ms: int,
) -> list[dict]:
    clusters = _cluster_by_price(pivots, radius)
    pools: list[dict] = []
    for cluster in clusters:
        prices = [p[0] for p in cluster]
        tfs = sorted({p[1] for p in cluster}, key=lambda tf: TF_WEIGHTS.get(tf, 1), reverse=True)
        tss = [p[2] for p in cluster]
        most_recent_ts = max(tss)

        rep_price = max(prices) if pool_type == "BSL" else min(prices)
        price_range = [round(min(prices), 6), round(max(prices), 6)]

        swept = _is_swept(rep_price, most_recent_ts, pool_type, ohlc)

        distance_pct = (rep_price - current_price) / current_price * 100
        if abs(distance_pct) > MAX_POOL_DISTANCE_PCT * 100:
            continue
        if pool_type == "BSL" and rep_price <= current_price:
            continue
        if pool_type == "SSL" and rep_price >= current_price:
            continue

        age_hours = max(0, int((now_ms - most_recent_ts) / 3_600_000))
        touches = len(cluster)
        strength = _strength_score(tfs, touches)

        pools.append({
            "price": round(rep_price, 6),
            "price_range": price_range,
            "type": pool_type,
            "touches": touches,
            "tfs": list(tfs),
            "most_recent_ts": most_recent_ts,
            "age_hours": age_hours,
            "swept": swept,
            "distance_pct": round(distance_pct, 2),
            "strength_score": strength,
        })

    pools.sort(key=lambda p: (p["swept"], -p["strength_score"], abs(p["distance_pct"])))
    return pools
