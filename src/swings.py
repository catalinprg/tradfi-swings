from src.types import OHLC, SwingPair, Timeframe

def atr(bars: list[OHLC], period: int = 14) -> list[float | None]:
    """Wilder's ATR. Returns list same length as bars; first `period` entries are None."""
    if len(bars) < period + 1:
        return [None] * len(bars)
    trs: list[float] = []
    for i, b in enumerate(bars):
        if i == 0:
            trs.append(b.high - b.low)
            continue
        prev_close = bars[i - 1].close
        tr = max(
            b.high - b.low,
            abs(b.high - prev_close),
            abs(b.low - prev_close),
        )
        trs.append(tr)
    out: list[float | None] = [None] * len(bars)
    # Seed ATR = simple mean of first `period` TRs, placed at index `period`
    seed = sum(trs[1:period + 1]) / period
    out[period] = seed
    for i in range(period + 1, len(bars)):
        out[i] = (out[i - 1] * (period - 1) + trs[i]) / period
    return out

def _pivot_window(atr_val: float, price: float) -> int:
    """Volatility-adaptive N: larger ATR/price ratio => larger window."""
    return max(3, round((atr_val / price) * 1000))

def detect_pivots(
    bars: list[OHLC], n: int | None = None
) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """Return (highs, lows) as lists of (bar_index, price).

    A bar at index i is a swing high if bars[i].high >= all highs in [i-N, i+N].
    If `n` is None, compute per-bar adaptive N using ATR(14).
    When `n` is given explicitly, the returned index is the bar position.
    When `n` is None, the returned index is the bar timestamp (`bars[i].ts`).
    """
    highs: list[tuple[int, float]] = []
    lows: list[tuple[int, float]] = []
    if n is None:
        atr_series = atr(bars, 14)
    else:
        atr_series = [None] * len(bars)

    for i in range(len(bars)):
        if n is not None:
            window = n
        else:
            a = atr_series[i]
            if a is None:
                continue
            window = min(_pivot_window(a, bars[i].close), max(3, len(bars) // 10))
        lo, hi = i - window, i + window
        if lo < 0 or hi >= len(bars):
            continue
        segment = bars[lo:hi + 1]
        if bars[i].high >= max(b.high for b in segment):
            highs.append((bars[i].ts if n is None else i, bars[i].high))
        if bars[i].low <= min(b.low for b in segment):
            lows.append((bars[i].ts if n is None else i, bars[i].low))
    return highs, lows

def build_pairs(
    bars: list[OHLC],
    highs: list[tuple[int, float]],
    lows: list[tuple[int, float]],
    tf: Timeframe,
) -> list[SwingPair]:
    """Walk pivots chronologically; each adjacent (high,low) or (low,high) pair becomes a SwingPair."""
    merged = [("H", ts, p) for ts, p in highs] + [("L", ts, p) for ts, p in lows]
    merged.sort(key=lambda x: x[1])
    pairs: list[SwingPair] = []
    for i in range(len(merged) - 1):
        a, b = merged[i], merged[i + 1]
        if a[0] == b[0]:
            continue
        if a[0] == "H":
            h_ts, h_p, l_ts, l_p = a[1], a[2], b[1], b[2]
            direction = "down"
        else:
            l_ts, l_p, h_ts, h_p = a[1], a[2], b[1], b[2]
            direction = "up"
        pairs.append(SwingPair(
            tf=tf,
            high_price=h_p, high_ts=h_ts,
            low_price=l_p, low_ts=l_ts,
            direction=direction,
        ))
    return pairs

def detect_swings(
    bars: list[OHLC], tf: Timeframe, max_pairs: int = 3
) -> list[SwingPair]:
    """Full pipeline: ATR-adaptive pivots -> pairs -> last N."""
    highs, lows = detect_pivots(bars, n=None)
    pairs = build_pairs(bars, highs, lows, tf)
    return pairs[-max_pairs:]
