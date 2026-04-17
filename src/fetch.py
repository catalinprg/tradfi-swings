"""yfinance-based OHLC fetch for TradFi instruments.

Unified TF set across all asset classes: 5m, 1h, 4h, 1d, 1w.

- `4h` is not a native yfinance interval; we pull 1h and resample client-side
  (groupby every 4 bars, aligned to 00:00 UTC).
- `5m` history is ~60 days from yfinance — plenty for short-horizon swings.
- 1m is NOT included: yfinance caps 1m at ~7 days, too shallow to produce
  reliable swing pivots. If needed per instrument later, add behind a flag.
"""
import time

import pandas as pd
import yfinance as yf

from src.types import OHLC, Timeframe

# Map our Timeframe label → (yfinance `interval`, `period`).
# For 4h we fetch 1h then resample; base interval/period are the same as 1h,
# with resample happening in `fetch_one`.
TF_TO_YF: dict[Timeframe, tuple[str, str]] = {
    "5m":  ("5m",  "60d"),
    "1h":  ("60m", "730d"),
    "4h":  ("60m", "730d"),
    "1d":  ("1d",  "10y"),
    "1w":  ("1wk", "20y"),
}

# Bar-count floors. If yfinance returns fewer rows than this for a given TF,
# we treat that TF as insufficient and add it to `skipped_tfs` in the payload
# (same graceful-degrade contract as the crypto pipeline).
MIN_BARS: dict[Timeframe, int] = {
    "5m": 300,   # ~1 RTH day of 5m bars
    "1h": 200,   # ~8 trading days
    "4h": 150,   # ~25 trading days after 4x resample
    "1d": 100,   # ~5 months
    "1w": 30,    # ~7 months
}


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


def _resample_1h_to_4h(bars_1h: list[OHLC]) -> list[OHLC]:
    """Group 1h bars into 4h buckets aligned to 00:00 UTC. Each 4h bar:
    open = first 1h open, high = max high, low = min low, close = last 1h
    close, volume = sum. Incomplete buckets (<4 bars) are dropped."""
    if not bars_1h:
        return []
    BUCKET_MS = 4 * 3600 * 1000
    buckets: dict[int, list[OHLC]] = {}
    for b in bars_1h:
        key = (b.ts // BUCKET_MS) * BUCKET_MS
        buckets.setdefault(key, []).append(b)
    out: list[OHLC] = []
    for key in sorted(buckets.keys()):
        group = buckets[key]
        if len(group) < 4:
            continue
        group.sort(key=lambda x: x.ts)
        out.append(OHLC(
            ts=key,
            open=group[0].open,
            high=max(b.high for b in group),
            low=min(b.low for b in group),
            close=group[-1].close,
            volume=sum(b.volume for b in group),
        ))
    return out


def fetch_one(symbol: str, tf: Timeframe) -> list[OHLC]:
    """Fetch a single (symbol, TF) series. Returns [] on any error — caller
    treats empty as 'insufficient data for this TF'."""
    interval, period = TF_TO_YF[tf]
    for attempt in range(3):
        try:
            df = yf.Ticker(symbol).history(
                interval=interval,
                period=period,
                auto_adjust=False,
                raise_errors=False,
            )
            bars = _df_to_ohlc(df)
            if tf == "4h":
                bars = _resample_1h_to_4h(bars)
            return bars
        except Exception:
            if attempt == 2:
                return []
            time.sleep(2 ** (attempt + 1))
    return []


def fetch_all(symbol: str) -> tuple[dict[Timeframe, list[OHLC]], list[Timeframe]]:
    """Fetch all five TFs for `symbol`. Returns (ohlc_by_tf, skipped_tfs).
    A TF is considered skipped if it returned fewer bars than MIN_BARS[tf]."""
    tfs: list[Timeframe] = ["1w", "1d", "4h", "1h", "5m"]
    ohlc: dict[Timeframe, list[OHLC]] = {}
    skipped: list[Timeframe] = []
    for tf in tfs:
        bars = fetch_one(symbol, tf)
        if len(bars) < MIN_BARS[tf]:
            skipped.append(tf)
            continue
        ohlc[tf] = bars
    return ohlc, skipped
