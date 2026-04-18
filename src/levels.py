"""Unified multi-source confluence clustering for tradfi.

Source families — confluence is counted across DISTINCT families. Two FIB
ratios at the same price are not "multi-source"; FIB + LIQ is.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable, Literal

from src.types import Level, Timeframe, TF_WEIGHTS, FibLevel

SOURCE_FAMILY: dict[str, str] = {
    **{f"FIB_{r}": "FIB" for r in ("236", "382", "500", "618", "786", "1272", "1618")},
    "LIQ_BSL": "LIQ", "LIQ_SSL": "LIQ",
    "FVG_BULL": "FVG", "FVG_BEAR": "FVG",
    "OB_BULL": "OB", "OB_BEAR": "OB",
    "MS_BOS_LEVEL": "MS", "MS_CHOCH_LEVEL": "MS", "MS_INVALIDATION": "MS",
}

MAX_ZONE_WIDTH_MULTIPLIER = 2.0
FAMILY_BONUS = 3.0
HTF_WEEK_MULT = 1.3
HTF_DAY_MULT = 1.1
POOL_STRENGTH_NORMALIZER = 30.0


@dataclass(frozen=True)
class MultiSourceZone:
    min_price: float
    max_price: float
    levels: tuple[Level, ...]
    source_count: int
    score: float
    classification: Literal["strong", "confluence", "structural_pivot", "level"]

    @property
    def mid(self) -> float:
        return (self.min_price + self.max_price) / 2


def cluster_levels(levels: Iterable[Level], radius: float) -> list[MultiSourceZone]:
    lvl_list = sorted(levels, key=lambda l: l.price)
    if not lvl_list:
        return []
    groups: list[list[Level]] = [[lvl_list[0]]]
    max_width = radius * MAX_ZONE_WIDTH_MULTIPLIER
    for l in lvl_list[1:]:
        near = l.price - groups[-1][-1].price <= radius
        within = l.price - groups[-1][0].price <= max_width
        if near and within:
            groups[-1].append(l)
        else:
            groups.append([l])
    return [_build(g) for g in groups]


def _build(group: list[Level]) -> MultiSourceZone:
    families = {SOURCE_FAMILY.get(l.source, l.source) for l in group}
    sc = len(families)
    # ≥3 families = strong; 2 = confluence or structural_pivot (if MS present); 1 = level
    score = FAMILY_BONUS * sc + sum(TF_WEIGHTS.get(l.tf, 1) * l.strength for l in group)
    tfs = {l.tf for l in group}
    if "1w" in tfs:
        score *= HTF_WEEK_MULT
    elif "1d" in tfs:
        score *= HTF_DAY_MULT
    if sc >= 3:
        cls: Literal["strong", "confluence", "structural_pivot", "level"] = "strong"
    elif sc == 2:
        cls = "structural_pivot" if "MS" in families else "confluence"
    else:
        cls = "level"
    return MultiSourceZone(
        min_price=min(l.min_price for l in group),
        max_price=max(l.max_price for l in group),
        levels=tuple(group),
        source_count=sc,
        score=round(score, 2),
        classification=cls,
    )


def split_by_price(
    zones: list[MultiSourceZone], current_price: float,
) -> tuple[list[MultiSourceZone], list[MultiSourceZone]]:
    support, resistance = [], []
    for z in zones:
        if z.mid < current_price:
            support.append(z)
        else:
            resistance.append(z)
    support.sort(key=lambda z: z.score, reverse=True)
    resistance.sort(key=lambda z: z.score, reverse=True)
    return support, resistance


# ---- Source → Level adapters ----
#
# Strength ranking (design choice, not derived):
#   Structure levels: MS_CHOCH=0.9, MS_BOS=0.8, MS_INVALIDATION=0.6
#   Fibs: key ratios (0.5/0.618/0.382)=0.6, others=0.4
#   Liquidity pools: strength_score / 30 (normalized)
#   FVG unmitigated=0.6, stale=0.3
#   OB  unmitigated=0.7, stale=0.35

_RATIO_TO_SRC = {
    0.236: "FIB_236", 0.382: "FIB_382", 0.5: "FIB_500",
    0.618: "FIB_618", 0.786: "FIB_786",
    1.272: "FIB_1272", 1.618: "FIB_1618",
}


def fibs_to_levels(fibs: list[FibLevel]) -> list[Level]:
    out: list[Level] = []
    for f in fibs:
        src = _RATIO_TO_SRC.get(f.ratio)
        if src is None:
            continue
        out.append(Level(
            price=f.price, min_price=f.price, max_price=f.price,
            source=src, tf=f.tf,
            strength=0.6 if f.ratio in (0.5, 0.618, 0.382) else 0.4,
            age_bars=0, meta={"ratio": f.ratio, "kind": f.kind},
        ))
    return out


def pools_to_levels(pools: dict[str, list[dict]]) -> list[Level]:
    out: list[Level] = []
    for side in ("buy_side", "sell_side"):
        for p in pools.get(side, []):
            if p.get("swept"):
                continue
            src = "LIQ_BSL" if p["type"] == "BSL" else "LIQ_SSL"
            rng = p["price_range"]
            out.append(Level(
                price=p["price"], min_price=rng[0], max_price=rng[1],
                source=src, tf=p["tfs"][0] if p["tfs"] else "1d",
                strength=min(1.0, p["strength_score"] / POOL_STRENGTH_NORMALIZER),
                age_bars=p["age_hours"],
                meta={"touches": p["touches"], "tfs": p["tfs"]},
            ))
    return out


def fvgs_to_levels(fvgs) -> list[Level]:
    out: list[Level] = []
    for f in fvgs:
        if f.mitigated:
            continue
        mid = (f.lo + f.hi) / 2
        strength = 0.6 if not f.stale else 0.3
        out.append(Level(
            price=mid, min_price=f.lo, max_price=f.hi,
            source=f.type, tf=f.tf, strength=strength,
            age_bars=f.age_bars, meta={"stale": f.stale},
        ))
    return out


def obs_to_levels(obs) -> list[Level]:
    out: list[Level] = []
    for o in obs:
        if o.mitigated:
            continue
        mid = (o.lo + o.hi) / 2
        strength = 0.7 if not o.stale else 0.35
        out.append(Level(
            price=mid, min_price=o.lo, max_price=o.hi,
            source=o.type, tf=o.tf, strength=strength,
            age_bars=o.age_bars, meta={"stale": o.stale},
        ))
    return out


def structure_to_levels(state, *, tf: Timeframe) -> list[Level]:
    out: list[Level] = []
    if state.last_bos:
        lvl = state.last_bos["level"]
        out.append(Level(
            price=lvl, min_price=lvl, max_price=lvl,
            source="MS_BOS_LEVEL", tf=tf, strength=0.8, age_bars=0,
            meta={"direction": state.last_bos["direction"]},
        ))
    if state.last_choch:
        lvl = state.last_choch["level"]
        out.append(Level(
            price=lvl, min_price=lvl, max_price=lvl,
            source="MS_CHOCH_LEVEL", tf=tf, strength=0.9, age_bars=0,
            meta={"direction": state.last_choch["direction"]},
        ))
    if state.invalidation_level is not None:
        out.append(Level(
            price=state.invalidation_level,
            min_price=state.invalidation_level, max_price=state.invalidation_level,
            source="MS_INVALIDATION", tf=tf, strength=0.6, age_bars=0,
        ))
    return out
