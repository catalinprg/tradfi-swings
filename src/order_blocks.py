"""ICT Order Blocks with 1.5×ATR displacement filter.

Bullish OB = last down candle (close < open) preceding a displacement UP
candle whose range exceeds 1.5×ATR and whose close breaks above the most
recent swing high within the lookback window. Bearish OB = mirror.

Lifecycle:
  - `mitigated`: subsequent bar trades into the OB's price range → spent.
  - `stale`: age > stale_after AND unmitigated → flagged but retained.

OB range = the precursor candle's [low, high]. Output feeds `obs_to_levels`
adapter → Level objects for unified confluence clustering.

NOTE: age_bars is measured from the OB candle (ob_idx), not from the
displacement bar. This differs from fvg.py, which measures from i+1 (the
completing bar of the 3-bar window). Consumers should be aware when
comparing ages across sources.
"""
from __future__ import annotations
from dataclasses import dataclass

from src.types import OHLC, Timeframe

DISPLACEMENT_ATR_MULT = 1.5
# NOTE: Fixed across TFs. On 1w this spans 20 weeks; consider TF-scaled
# lookback in Task 5 wiring if misfires occur on weekly bars.
PRIOR_SWING_LOOKBACK = 20
DEFAULT_STALE_AFTER = 100


@dataclass(frozen=True)
class OrderBlock:
    type: str            # "OB_BULL" | "OB_BEAR"
    tf: Timeframe
    lo: float
    hi: float
    formation_ts: int
    age_bars: int
    mitigated: bool
    stale: bool


def detect_order_blocks(
    bars: list[OHLC], *, tf: Timeframe, atr_14: float,
    stale_after: int = DEFAULT_STALE_AFTER,
) -> list[OrderBlock]:
    if len(bars) < 3 or atr_14 <= 0:
        return []
    threshold = DISPLACEMENT_ATR_MULT * atr_14
    out: list[OrderBlock] = []
    n = len(bars)
    for i in range(1, n):
        disp = bars[i]
        disp_range = disp.high - disp.low
        if disp_range < threshold:
            continue
        if disp.close > disp.open:
            look_lo = max(0, i - PRIOR_SWING_LOOKBACK)
            prior_high = max(b.high for b in bars[look_lo:i])
            if disp.close <= prior_high:
                continue
            ob_idx = _last_down_before(bars, i)
            if ob_idx is None:
                continue
            ob = bars[ob_idx]
            mit = _mitigated(bars, ob_idx, ob.low, ob.high)
            age = n - 1 - ob_idx
            out.append(OrderBlock(
                type="OB_BULL", tf=tf, lo=ob.low, hi=ob.high,
                formation_ts=ob.ts, age_bars=age,
                mitigated=mit, stale=(age > stale_after and not mit),
            ))
        elif disp.close < disp.open:
            look_lo = max(0, i - PRIOR_SWING_LOOKBACK)
            prior_low = min(b.low for b in bars[look_lo:i])
            if disp.close >= prior_low:
                continue
            ob_idx = _last_up_before(bars, i)
            if ob_idx is None:
                continue
            ob = bars[ob_idx]
            mit = _mitigated(bars, ob_idx, ob.low, ob.high)
            age = n - 1 - ob_idx
            out.append(OrderBlock(
                type="OB_BEAR", tf=tf, lo=ob.low, hi=ob.high,
                formation_ts=ob.ts, age_bars=age,
                mitigated=mit, stale=(age > stale_after and not mit),
            ))
    # De-duplicate by (type, formation_ts): last write wins, same mitigation state
    seen: dict = {}
    for o in out:
        seen[(o.type, o.formation_ts)] = o
    return list(seen.values())


def _last_down_before(bars, end_idx):
    for k in range(end_idx - 1, -1, -1):
        if bars[k].close < bars[k].open:
            return k
    return None


def _last_up_before(bars, end_idx):
    for k in range(end_idx - 1, -1, -1):
        if bars[k].close > bars[k].open:
            return k
    return None


def _mitigated(bars, from_idx, lo, hi):
    for b in bars[from_idx + 2:]:
        if b.low <= hi and b.high >= lo:
            return True
    return False
