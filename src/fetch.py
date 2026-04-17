"""yfinance-based OHLC fetch for TradFi instruments.

Unified TF set across all asset classes: 5m, 1h, 1d, 1w.

- 4h was removed: yfinance has no native 4h interval, and resampling 1h
  client-side produces session-crossing bars on equities (Tuesday close
  mixes with Wednesday open). 1h + 1d already bracket that range.
- `5m` history is ~60 days from yfinance — plenty for short-horizon swings.
- 1m is NOT included: yfinance caps 1m at ~7 days, too shallow to produce
  reliable swing pivots. If needed per instrument later, add behind a flag.
"""
import time

import pandas as pd
import yfinance as yf

from src.types import OHLC, Timeframe

# Map our Timeframe label → (yfinance `interval`, `period`).
TF_TO_YF: dict[Timeframe, tuple[str, str]] = {
    "5m":  ("5m",  "60d"),
    "1h":  ("60m", "730d"),
    "1d":  ("1d",  "10y"),
    "1w":  ("1wk", "20y"),
}

# Bar-count floors. If yfinance returns fewer rows than this for a given TF,
# we treat that TF as insufficient and add it to `skipped_tfs` in the payload
# (same graceful-degrade contract as the crypto pipeline).
MIN_BARS: dict[Timeframe, int] = {
    "5m": 300,   # ~1 RTH day of 5m bars
    "1h": 200,   # ~8 trading days
    "1d": 100,   # ~5 months
    "1w": 30,    # ~7 months
}

# Asset classes whose sessions include extended hours. For 5m RTH filtering
# we limit to regular session bars; 24h markets (forex, commodities futures)
# keep the full session.
SESSION_LIMITED_CLASSES = frozenset({"index", "stock"})


def _df_to_ohlc(df: pd.DataFrame) -> list[OHLC]:
    """Convert a yfinance DataFrame (DatetimeIndex) to an OHLC list."""
    if df is None or df.empty:
        return []
    idx = df.index
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    else:
        idx = idx.tz_convert("UTC")
    bars: list[OHLC] = []
    for ts, row in zip(idx, df.itertuples(index=False)):
        try:
            o, h, l, c = float(row.Open), float(row.High), float(row.Low), float(row.Close)
        except (TypeError, ValueError):
            continue
        if any(v != v for v in (o, h, l, c)):  # NaN guard
            continue
        vol = getattr(row, "Volume", 0) or 0
        bars.append(OHLC(
            ts=int(ts.timestamp() * 1000),
            open=o, high=h, low=l, close=c,
            volume=float(vol),
        ))
    return bars


def fetch_one(symbol: str, tf: Timeframe, asset_class: str | None = None) -> list[OHLC]:
    """Fetch a single (symbol, TF) series. Returns [] on any error — caller
    treats empty as 'insufficient data for this TF'.

    `asset_class` controls the pre/post-market filter on 5m: session-limited
    classes (index, stock) request RTH-only bars; 24h markets (forex,
    commodities) fetch the full session."""
    interval, period = TF_TO_YF[tf]
    include_prepost = not (
        tf == "5m" and asset_class in SESSION_LIMITED_CLASSES
    )
    for attempt in range(3):
        try:
            df = yf.Ticker(symbol).history(
                interval=interval,
                period=period,
                auto_adjust=False,
                raise_errors=False,
                prepost=include_prepost,
            )
            return _df_to_ohlc(df)
        except Exception:
            if attempt == 2:
                return []
            time.sleep(2 ** (attempt + 1))
    return []


def fetch_all(symbol: str, asset_class: str | None = None) -> tuple[dict[Timeframe, list[OHLC]], list[Timeframe]]:
    """Fetch all TFs for `symbol`. Returns (ohlc_by_tf, skipped_tfs).
    A TF is skipped when it returned fewer bars than MIN_BARS[tf]."""
    tfs: list[Timeframe] = ["1w", "1d", "1h", "5m"]
    ohlc: dict[Timeframe, list[OHLC]] = {}
    skipped: list[Timeframe] = []
    for tf in tfs:
        bars = fetch_one(symbol, tf, asset_class=asset_class)
        if len(bars) < MIN_BARS[tf]:
            skipped.append(tf)
            continue
        ohlc[tf] = bars
    return ohlc, skipped
