"""Market structure: BOS (Break of Structure) + CHoCH (Change of Character)
derived from swing pivots.

Bias: bullish (HH + HL), bearish (LH + LL), range otherwise.

BOS: historical fact. In a confirmed bullish sequence (HH+HL), the most
recent HH IS a BOS event — each successive higher high is a break of the
prior one. `last_bos` is set whenever bias is bullish/bearish, pointing at
the most recent trend-direction pivot. No `current_price` dependency.

CHoCH: live event. First break against the prevailing trend:
- Bullish trend → price closes below the most recent HL → bearish CHoCH
- Bearish trend → price closes above the most recent LH → bullish CHoCH

Output feeds `structure_to_levels` adapter → Level objects for unified
confluence clustering (emits MS_BOS_LEVEL, MS_CHOCH_LEVEL, MS_INVALIDATION).
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class StructureState:
    bias: str                          # "bullish" | "bearish" | "range"
    last_bos: dict | None              # {direction, level, ts} or None
    last_choch: dict | None
    invalidation_level: float | None


def analyze_structure(
    highs: list[tuple[int, float]],
    lows: list[tuple[int, float]],
    current_price: float,
) -> StructureState:
    """`highs`/`lows` are (ts_or_idx, price) tuples from `detect_pivots`.
    Works on either index or timestamp keys — only ordering matters."""
    if len(highs) < 2 or len(lows) < 2:
        return StructureState(bias="range", last_bos=None, last_choch=None,
                              invalidation_level=None)
    # Bias reflects the MOST RECENT swing — not history-wide monotonicity.
    # Classic SMC: structure is bullish when the last HH > prior HH AND the
    # last HL > prior HL. Older pivots are historical; one stale outlier
    # shouldn't void a current trend shift. Using only the last two pivots
    # keeps MS responsive to the actual market state (otherwise range
    # dominates on noisy real data — 0 of 4 TFs produced bias pre-fix on all
    # tradfi asset classes).
    hh = highs[-1][1] > highs[-2][1]
    ll = lows[-1][1]  < lows[-2][1]
    hl = lows[-1][1]  > lows[-2][1]
    lh = highs[-1][1] < highs[-2][1]
    if hh and hl:
        bias = "bullish"
    elif ll and lh:
        bias = "bearish"
    else:
        bias = "range"
    last_bos = None
    last_choch = None
    invalidation = None
    if bias == "bullish":
        last_hh = highs[-1]
        last_hl = lows[-1]
        invalidation = last_hl[1]
        last_bos = {"direction": "bullish", "level": last_hh[1], "ts": last_hh[0]}
        if current_price < last_hl[1]:
            last_choch = {"direction": "bearish", "level": last_hl[1], "ts": last_hl[0]}
    elif bias == "bearish":
        last_ll = lows[-1]
        last_lh = highs[-1]
        invalidation = last_lh[1]
        last_bos = {"direction": "bearish", "level": last_ll[1], "ts": last_ll[0]}
        if current_price > last_lh[1]:
            last_choch = {"direction": "bullish", "level": last_lh[1], "ts": last_lh[0]}
    return StructureState(
        bias=bias, last_bos=last_bos, last_choch=last_choch,
        invalidation_level=invalidation,
    )
