"""FVG detection with session-boundary filter.

Bullish FVG: bars[i+1].low > bars[i-1].high → gap [bars[i-1].high, bars[i+1].low].
Bearish FVG: bars[i+1].high < bars[i-1].low → gap [bars[i+1].high, bars[i-1].low].

Session-boundary filter: if the elapsed time between consecutive bars
exceeds 1.5× the expected bar cadence, that triplet is treated as spanning
a session close→open and is skipped. This avoids classifying overnight /
weekend price discontinuities on equities and indices as FVGs. For 24×5
assets (forex, commodity futures) the caller passes the bar's native
cadence and same-value expected cadence — no triplet gets skipped.

Lifecycle — `mitigated` (any subsequent bar traded into the gap range) and
`stale` (age > stale_after, still unmitigated) are flags on the output,
same as crypto-swings.

Output feeds Task 1's `fvgs_to_levels` adapter → Level objects for unified
confluence clustering.
"""
from __future__ import annotations
from dataclasses import dataclass

from src.types import OHLC, Timeframe

SESSION_GAP_TOLERANCE = 1.5
DEFAULT_STALE_AFTER = 100
MIN_GAP_ATR_MULT = 0.05


@dataclass(frozen=True)
class FVG:
    type: str           # "FVG_BULL" | "FVG_BEAR"
    tf: Timeframe
    lo: float
    hi: float
    formation_ts: int
    age_bars: int
    mitigated: bool
    stale: bool


def detect_fvgs(
    bars: list[OHLC], *, tf: Timeframe, atr_14: float,
    stale_after: int = DEFAULT_STALE_AFTER,
    expected_bar_ms: int | None = None,
) -> list[FVG]:
    """Scan 3-bar windows. Returns all unmitigated + mitigated FVGs formed
    in the window, with `stale` flag set on unmitigated FVGs older than
    `stale_after` bars.

    When `expected_bar_ms` is None, session-boundary filter is disabled
    (24×5 markets). When provided, any triplet whose inter-bar gap exceeds
    SESSION_GAP_TOLERANCE × expected_bar_ms is skipped.

    When `atr_14` is 0 or negative, the minimum-gap filter is disabled
    (all non-zero gaps pass through) — appropriate when calibration data
    is unavailable.
    """
    if len(bars) < 3:
        return []
    out: list[FVG] = []
    n = len(bars)
    tol_ms = None
    if expected_bar_ms is not None:
        tol_ms = int(expected_bar_ms * SESSION_GAP_TOLERANCE)
    min_gap = max(0.0, atr_14) * MIN_GAP_ATR_MULT
    for i in range(1, n - 1):
        prev, mid, nxt = bars[i - 1], bars[i], bars[i + 1]
        # Session-boundary guard
        if tol_ms is not None:
            if (mid.ts - prev.ts) > tol_ms or (nxt.ts - mid.ts) > tol_ms:
                continue
        if nxt.low > prev.high:
            lo, hi = prev.high, nxt.low
            if (hi - lo) < min_gap:
                continue
            age = n - 1 - (i + 1)
            mit = _mitigated(bars, i + 1, lo, hi)
            out.append(FVG(
                type="FVG_BULL", tf=tf, lo=lo, hi=hi,
                formation_ts=mid.ts, age_bars=age,
                mitigated=mit, stale=(age > stale_after and not mit),
            ))
        if nxt.high < prev.low:
            lo, hi = nxt.high, prev.low
            if (hi - lo) < min_gap:
                continue
            age = n - 1 - (i + 1)
            mit = _mitigated(bars, i + 1, lo, hi)
            out.append(FVG(
                type="FVG_BEAR", tf=tf, lo=lo, hi=hi,
                formation_ts=mid.ts, age_bars=age,
                mitigated=mit, stale=(age > stale_after and not mit),
            ))
    return out


def _mitigated(bars: list[OHLC], start_idx: int, lo: float, hi: float) -> bool:
    for b in bars[start_idx + 1:]:
        if b.low <= hi and b.high >= lo:
            return True
    return False


# ---- Helper: expected-bar-ms resolver per TF + asset class ----

TF_EXPECTED_MS: dict[Timeframe, int] = {
    "5m": 5 * 60 * 1000,
    "1h": 60 * 60 * 1000,
    "1d": 24 * 60 * 60 * 1000,
    "1w": 7 * 24 * 60 * 60 * 1000,
}

SESSION_LIMITED_CLASSES = frozenset({"index", "stock"})


def expected_bar_ms_for(tf: Timeframe, asset_class: str | None) -> int | None:
    """Decides whether the session-boundary filter applies for this (tf, class):
      - 1w on any asset: no meaningful intra-session concept inside a weekly bar → return None.
      - 24×5 assets (forex, commodity): return None; no sessions.
      - Equities/cash-indices on 5m/1h/1d: return the TF's native cadence so
        overnight/weekend gaps get filtered.
    """
    if tf == "1w":
        return None
    if asset_class in SESSION_LIMITED_CLASSES:
        return TF_EXPECTED_MS[tf]
    return None
