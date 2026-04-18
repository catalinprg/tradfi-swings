"""Run the TradFi swings pipeline for a single instrument and emit a payload
JSON file for the analyst agent.

Usage:
    uv run python -m scripts.emit_payload <instrument_slug> [output_path]

    # writes to data/{slug}/payload.json by default
    python3 -m scripts.emit_payload eurusd
    python3 -m scripts.emit_payload spx /tmp/spx_payload.json

The slug must exist in config/watchlist.yaml under `instruments:`. The
pipeline:
  1. Load instrument metadata (symbol, display, asset_class).
  2. Fetch OHLC via yfinance across 5 TFs.
  3. Detect swings, compute fib levels, cluster into zones using daily ATR.
  4. Fetch VIX + DXY market-context snapshot.
  5. Write payload JSON.

Env vars required: none for emit_payload (yfinance is keyless). NOTION_TOKEN
is consumed by publish_notion.py downstream.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from src import liquidity as liquidity_mod
from src import market_context as market_context_mod
from src import momentum as momentum_mod
from src.fetch import fetch_all
from src.fibs import compute_all
from src.swings import atr, detect_swings, detect_pivots
from src.types import Timeframe
from src.fvg import detect_fvgs, expected_bar_ms_for
from src.order_blocks import detect_order_blocks
from src.market_structure import analyze_structure, StructureState
from src.levels import (
    cluster_levels, split_by_price,
    fibs_to_levels, pools_to_levels, fvgs_to_levels, obs_to_levels,
    structure_to_levels,
)


MIN_PAIRS_PER_TF = 2
ATR_CLUSTER_MULTIPLIER = 0.25
MAX_EXTENSION_DISTANCE_PCT = 0.15
MAX_ZONE_DISTANCE_PCT = 0.20          # drop zones further than 20% from price
MAX_ZONES_PER_SIDE = 8                # cap payload size; analyst picks top 4


def _latest(series: list[float | None]) -> float:
    for v in reversed(series):
        if v is not None:
            return v
    raise RuntimeError("no ATR value available")


def _load_watchlist() -> dict:
    """Load config/watchlist.yaml from repo root."""
    root = Path(__file__).resolve().parent.parent
    with open(root / "config" / "watchlist.yaml") as f:
        return yaml.safe_load(f)


def _price_decimals(asset_class: str, price: float) -> int:
    """Decimals to round display prices to. Forex majors carry 4–5 decimals,
    JPY pairs 2–3, commodities/indices/stocks 2."""
    if asset_class == "forex":
        # USDJPY-style pairs trade in hundredths; others in pips (0.0001).
        return 3 if price > 50 else 5
    return 2


def build(slug: str) -> dict:
    """Build the full payload for `slug`. Raises on fatal pipeline errors
    (no instrument, no bars at all); returns a payload with `skipped_tfs`
    when partial data is available."""
    watchlist = _load_watchlist()
    instr_cfg = watchlist["instruments"].get(slug)
    if not instr_cfg:
        raise SystemExit(
            f"unknown instrument slug '{slug}'. "
            f"valid: {sorted(watchlist['instruments'].keys())}"
        )

    symbol = instr_cfg["symbol"]
    display = instr_cfg["display"]
    asset_class = instr_cfg["asset_class"]

    # 1. OHLC
    ohlc, skipped = fetch_all(symbol, asset_class=asset_class)
    if not ohlc or "1d" not in ohlc:
        raise SystemExit(
            f"fatal: no daily bars available for {symbol} "
            f"(skipped={skipped})"
        )

    # 2. Swings per TF
    all_pairs = []
    contributing: list[Timeframe] = []
    for tf, bars in ohlc.items():
        pairs = detect_swings(bars, tf=tf, max_pairs=3)
        if len(pairs) < MIN_PAIRS_PER_TF:
            skipped.append(tf)
            continue
        all_pairs.extend(pairs)
        contributing.append(tf)

    # 3. Fib levels + drop far extensions
    fib_levels = compute_all(all_pairs)
    daily_bars = ohlc["1d"]
    current_price = daily_bars[-1].close
    fib_levels = [
        lv for lv in fib_levels
        if lv.kind == "retracement"
        or abs(lv.price - current_price) / current_price <= MAX_EXTENSION_DISTANCE_PCT
    ]

    daily_atr = _latest(atr(daily_bars, 14))
    radius = daily_atr * ATR_CLUSTER_MULTIPLIER

    # 4. Price-action layers: FVG + OB + MS per TF
    fvg_by_tf: dict[Timeframe, list] = {}
    ob_by_tf: dict[Timeframe, list] = {}
    ms_by_tf: dict[Timeframe, StructureState] = {}
    for tf, bars in ohlc.items():
        highs, lows = detect_pivots(bars, n=None)
        ms_by_tf[tf] = analyze_structure(highs, lows, current_price=current_price)
        try:
            tf_atr = _latest(atr(bars, 14))
        except RuntimeError:
            continue   # insufficient bars for ATR
        fvg_by_tf[tf] = detect_fvgs(
            bars, tf=tf, atr_14=tf_atr,
            expected_bar_ms=expected_bar_ms_for(tf, asset_class),
        )
        ob_by_tf[tf] = detect_order_blocks(bars, tf=tf, atr_14=tf_atr)

    # 5. Liquidity pools (unchanged)
    liquidity_pools = liquidity_mod.compute_pools(
        swing_pairs=all_pairs, ohlc=ohlc,
        current_price=current_price, daily_atr=daily_atr,
    )

    # 6. Unified level list from all sources
    unified_levels = fibs_to_levels(fib_levels)
    unified_levels += pools_to_levels(liquidity_pools)
    for fvgs in fvg_by_tf.values():
        unified_levels += fvgs_to_levels(fvgs)
    for obs in ob_by_tf.values():
        unified_levels += obs_to_levels(obs)
    for tf, ms in ms_by_tf.items():
        unified_levels += structure_to_levels(ms, tf=tf)

    # Drop far-away levels before clustering
    unified_levels = [
        l for l in unified_levels
        if abs(l.price - current_price) / current_price <= MAX_ZONE_DISTANCE_PCT
    ]

    zones = cluster_levels(unified_levels, radius=radius)
    support_all, resistance_all = split_by_price(zones, current_price)
    support = [z for z in support_all
               if abs(z.mid - current_price) / current_price <= MAX_ZONE_DISTANCE_PCT]
    resistance = [z for z in resistance_all
                  if abs(z.mid - current_price) / current_price <= MAX_ZONE_DISTANCE_PCT]

    prev_close = daily_bars[-2].close if len(daily_bars) >= 2 else current_price
    change_24h_pct = (current_price - prev_close) / prev_close * 100 if prev_close else 0.0

    # 7. Market context + momentum (unchanged)
    ctx = market_context_mod.fetch()
    momentum = momentum_mod.compute_per_tf(ohlc)

    # 8. Serialize
    decimals = _price_decimals(asset_class, current_price)

    def z_to_dict(z):
        return {
            "min_price": round(z.min_price, decimals),
            "max_price": round(z.max_price, decimals),
            "mid": round(z.mid, decimals),
            "score": z.score,
            "source_count": z.source_count,
            "classification": z.classification,
            "distance_pct": round((z.mid - current_price) / current_price * 100, 2),
            "sources": sorted({l.source for l in z.levels}),
            "contributing_levels": sorted(
                [{"source": l.source, "tf": l.tf, "price": round(l.price, decimals),
                  "meta": l.meta}
                 for l in z.levels],
                key=lambda d: d["price"],
            ),
        }

    return {
        "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "instrument": {
            "slug": slug,
            "symbol": symbol,
            "display": display,
            "asset_class": asset_class,
        },
        "current_price": round(current_price, decimals),
        "change_24h_pct": round(change_24h_pct, 2),
        "daily_atr": round(daily_atr, decimals),
        "contributing_tfs": contributing,
        "skipped_tfs": sorted(set(skipped)),
        "resistance": [z_to_dict(z) for z in resistance[:MAX_ZONES_PER_SIDE]],
        "support": [z_to_dict(z) for z in support[:MAX_ZONES_PER_SIDE]],
        "market_context": ctx,
        "momentum": momentum,
        "liquidity": liquidity_pools,
        "market_structure": {
            tf: {
                "bias": ms.bias,
                "last_bos": ms.last_bos,
                "last_choch": ms.last_choch,
                "invalidation_level": ms.invalidation_level,
            }
            for tf, ms in ms_by_tf.items()
        },
    }


def main() -> int:
    if len(sys.argv) < 2:
        print(
            "usage: emit_payload.py <instrument_slug> [output_path]",
            file=sys.stderr,
        )
        return 2

    slug = sys.argv[1]
    out_path = sys.argv[2] if len(sys.argv) > 2 else f"data/{slug}/payload.json"

    payload = build(slug)

    out_file = Path(out_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w") as f:
        json.dump(payload, f, indent=2)

    ctx = payload["market_context"]
    ctx_note = (
        f"ctx: vix={ctx.get('vix') and ctx['vix']['value']} "
        f"dxy={ctx.get('dxy') and ctx['dxy']['value']}"
    )
    print(f"payload written: {out_file}")
    print(
        f"[{payload['instrument']['display']}] current: {payload['current_price']} "
        f"resistance: {len(payload['resistance'])} "
        f"support: {len(payload['support'])} "
        f"skipped: {payload['skipped_tfs']} {ctx_note}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
