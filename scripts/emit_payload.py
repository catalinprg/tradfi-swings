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

from src import market_context as market_context_mod
from src.confluence import cluster, split_by_price
from src.fetch import fetch_all
from src.fibs import compute_all
from src.swings import atr, detect_swings
from src.types import Timeframe


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
    ohlc, skipped = fetch_all(symbol)
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
    levels = compute_all(all_pairs)
    daily_bars = ohlc["1d"]
    current_price = daily_bars[-1].close
    levels = [
        lv for lv in levels
        if lv.kind == "retracement"
        or abs(lv.price - current_price) / current_price <= MAX_EXTENSION_DISTANCE_PCT
    ]

    # 4. Cluster using daily ATR
    daily_atr = _latest(atr(daily_bars, 14))
    radius = daily_atr * ATR_CLUSTER_MULTIPLIER
    zones = cluster(levels, radius=radius)

    # 5. Split S/R and rank, then filter far zones
    support_all, resistance_all = split_by_price(zones, current_price)
    support = [z for z in support_all
               if abs(z.mid - current_price) / current_price <= MAX_ZONE_DISTANCE_PCT]
    resistance = [z for z in resistance_all
                  if abs(z.mid - current_price) / current_price <= MAX_ZONE_DISTANCE_PCT]

    prev_close = daily_bars[-2].close if len(daily_bars) >= 2 else current_price
    change_24h_pct = (current_price - prev_close) / prev_close * 100 if prev_close else 0.0

    # 6. Market context (VIX + DXY)
    ctx = market_context_mod.fetch()

    # 7. Serialize
    decimals = _price_decimals(asset_class, current_price)

    def z_to_dict(z):
        return {
            "min_price": round(z.min_price, decimals),
            "max_price": round(z.max_price, decimals),
            "score": z.score,
            "distance_pct": round((z.mid - current_price) / current_price * 100, 2),
            "contributing_levels": sorted({f"{lv.tf} {lv.ratio}" for lv in z.levels}),
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
