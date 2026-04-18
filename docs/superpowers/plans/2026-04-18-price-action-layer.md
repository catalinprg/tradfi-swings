# TradFi-Swings Price-Action Layer

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add FVG, order blocks, and market structure (BOS/CHoCH) to the 15-instrument tradfi pipeline, unify them with existing fib + liquidity-pool layers via a single confluence scorer, and rewrite the analyst agent to reason with all sources.

**Architecture:** Pure OHLC-compute modules — no new data providers (yfinance volume is unreliable on FX / cash indices, so VP and AVWAP are NOT added here; FVG/OB/MS use only price). FVG detection adds a session-boundary filter that skips gaps formed across session close→open (matters for equities and cash indices, irrelevant for 24×5 FX/commodities). Unified `Level` schema routes all sources through one clustering engine; zones classified by distinct-source count and analyst prompt reads source-tagged confluence.

**Tech Stack:** Python 3.12, yfinance, pandas, pytest. No new runtime dependencies.

---

## File Structure

**New modules:**
- `src/fvg.py` — FVG detection with session-boundary filter
- `src/order_blocks.py` — ICT order blocks with 1.5×ATR displacement filter
- `src/market_structure.py` — BOS / CHoCH from swing pivots
- `src/levels.py` — Unified Level schema + multi-source confluence clustering

**Modified:**
- `src/types.py` — Add Level dataclass
- `src/confluence.py` — Either retire in favor of `levels.cluster_levels` or keep as legacy
- `src/fetch.py` — Surface session window per asset class (already exists implicitly via `SESSION_LIMITED_CLASSES`)
- `scripts/emit_payload.py` — Extend payload with new source-tagged zones + structure
- `.claude/agents/tradfi-swings-analyst.md` — Full rewrite of Analysis Framework

**New tests:**
- `tests/test_fvg.py`
- `tests/test_order_blocks.py`
- `tests/test_market_structure.py`
- `tests/test_levels.py`

---

## Worktree Setup

- [ ] **Step 0.1: Create worktree**

Run:
```bash
cd ~/Documents/Intelligence/tradfi-swings
git worktree add -b feature/price-action-layer ../tradfi-swings-pa
cd ../tradfi-swings-pa
uv sync
```
Expected: `../tradfi-swings-pa` created, fresh branch off `main`.

- [ ] **Step 0.2: Verify baseline tests**

Run: `uv run pytest -x`
Expected: all existing tests pass.

---

## Task 1: Unified Level schema (same shape as crypto-swings)

**Files:**
- Modify: `src/types.py`
- Create: `src/levels.py`
- Create: `tests/test_levels.py`

- [ ] **Step 1.1: Extend types.py with Level + LevelSource**

Append to `src/types.py`:
```python
from dataclasses import field
from typing import Literal

LevelSource = Literal[
    # Fibonacci
    "FIB_236", "FIB_382", "FIB_500", "FIB_618", "FIB_786",
    "FIB_1272", "FIB_1618",
    # Liquidity pools
    "LIQ_BSL", "LIQ_SSL",
    # FVG / Order Blocks
    "FVG_BULL", "FVG_BEAR",
    "OB_BULL", "OB_BEAR",
    # Market structure
    "MS_BOS_LEVEL", "MS_CHOCH_LEVEL", "MS_INVALIDATION",
]

@dataclass(frozen=True)
class Level:
    """Source-tagged canonical level for unified confluence clustering.
    Scalar sources set min_price = max_price = price. Zone sources (FVG, OB)
    set the real range."""
    price: float
    min_price: float
    max_price: float
    source: LevelSource
    tf: Timeframe
    strength: float
    age_bars: int
    meta: dict = field(default_factory=dict)
```

Note: tradfi does NOT include the crypto-only sources (POC, AVWAP_*, NAKED_POC_*). Keep the schema lean; don't over-engineer for future expansion.

- [ ] **Step 1.2: Failing test — multi-source clustering**

Create `tests/test_levels.py`:
```python
from src.levels import cluster_levels, split_by_price
from src.types import Level

def _lvl(price, source, tf="1d", strength=0.5, age=0, lo=None, hi=None):
    lo = lo if lo is not None else price
    hi = hi if hi is not None else price
    return Level(price=price, min_price=lo, max_price=hi,
                 source=source, tf=tf, strength=strength, age_bars=age)

def test_cluster_groups_multiple_sources():
    levels = [
        _lvl(100.0, "FIB_618", tf="1d"),
        _lvl(100.1, "LIQ_BSL", tf="1d"),
        _lvl(100.05, "FVG_BULL", tf="1d", lo=99.8, hi=100.2),
    ]
    zones = cluster_levels(levels, radius=0.3)
    assert len(zones) == 1
    assert zones[0].source_count == 3
    assert zones[0].classification == "strong"

def test_structural_pivot_when_ms_meets_any_other():
    levels = [
        _lvl(100.0, "MS_BOS_LEVEL", tf="1d"),
        _lvl(100.05, "LIQ_BSL",     tf="1d"),
    ]
    zones = cluster_levels(levels, radius=0.5)
    assert zones[0].classification == "structural_pivot"
```

- [ ] **Step 1.3: Run test to fail**

Run: `uv run pytest tests/test_levels.py -v`
Expected: FAIL — module missing.

- [ ] **Step 1.4: Implement src/levels.py**

Create `src/levels.py`:
```python
"""Unified multi-source confluence clustering for tradfi.

Source families — confluence is counted across DISTINCT families. Two FIB
ratios at the same price are not "multi-source"; FIB + LIQ is.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable

from src.types import Level, Timeframe, TF_WEIGHTS, FibLevel

SOURCE_FAMILY: dict[str, str] = {
    **{f"FIB_{r}": "FIB" for r in ("236", "382", "500", "618", "786", "1272", "1618")},
    "LIQ_BSL": "LIQ", "LIQ_SSL": "LIQ",
    "FVG_BULL": "FVG", "FVG_BEAR": "FVG",
    "OB_BULL": "OB", "OB_BEAR": "OB",
    "MS_BOS_LEVEL": "MS", "MS_CHOCH_LEVEL": "MS", "MS_INVALIDATION": "MS",
}

MAX_ZONE_WIDTH_MULTIPLIER = 2.0


@dataclass(frozen=True)
class MultiSourceZone:
    min_price: float
    max_price: float
    levels: tuple[Level, ...]
    source_count: int
    score: float
    classification: str   # "strong" | "confluence" | "structural_pivot" | "level"

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
    score = 3.0 * sc + sum(TF_WEIGHTS.get(l.tf, 1) * l.strength for l in group)
    tfs = {l.tf for l in group}
    if "1w" in tfs:
        score *= 1.3
    elif "1d" in tfs:
        score *= 1.1
    if sc >= 3:
        cls = "strong"
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
                strength=min(1.0, p["strength_score"] / 30.0),
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
```

- [ ] **Step 1.5: Run tests**

Run: `uv run pytest tests/test_levels.py -v`
Expected: PASS.

- [ ] **Step 1.6: Commit**

```bash
git add src/types.py src/levels.py tests/test_levels.py
git commit -m "feat(levels): add unified source-tagged Level schema + multi-source clustering"
```

---

## Task 2: FVG with session-boundary filter

**Files:**
- Create: `src/fvg.py`
- Create: `tests/test_fvg.py`

- [ ] **Step 2.1: Failing test — FVG detection skips session-boundary gaps**

Create `tests/test_fvg.py`:
```python
from src.fvg import detect_fvgs
from src.types import OHLC

def _b(ts, h, l, c=None):
    c = c if c is not None else (h + l) / 2
    return OHLC(ts=ts, open=c, high=h, low=l, close=c, volume=1.0)

def test_bullish_fvg_detected_within_continuous_session():
    # 1h bars, contiguous
    bars = [
        _b(0,            100, 98),
        _b(3_600_000,    103, 99, c=102.5),
        _b(7_200_000,    105, 102),
    ]
    fvgs = detect_fvgs(bars, tf="1h", atr_14=1.0, stale_after=100, expected_bar_ms=3_600_000)
    bulls = [f for f in fvgs if f.type == "FVG_BULL"]
    assert len(bulls) == 1

def test_fvg_skipped_when_gap_spans_session_close_to_open():
    # Third bar is >12h after the middle bar — session break for equities
    bars = [
        _b(0,               100, 98),
        _b(3_600_000,       103, 99, c=102.5),
        _b(3_600_000 + 16*3_600_000, 105, 102),   # 16h gap = overnight
    ]
    fvgs = detect_fvgs(bars, tf="1h", atr_14=1.0, stale_after=100, expected_bar_ms=3_600_000)
    assert fvgs == []

def test_fvg_mitigated_flag():
    bars = [
        _b(0,            100, 98),
        _b(3_600_000,    103, 99, c=102.5),
        _b(7_200_000,    105, 102),
        _b(10_800_000,   104, 100),   # returns into gap
    ]
    fvgs = detect_fvgs(bars, tf="1h", atr_14=1.0, stale_after=100, expected_bar_ms=3_600_000)
    assert any(f.type == "FVG_BULL" and f.mitigated for f in fvgs)
```

- [ ] **Step 2.2: Run test to fail**

Run: `uv run pytest tests/test_fvg.py -v`
Expected: FAIL — module missing.

- [ ] **Step 2.3: Implement src/fvg.py**

Create `src/fvg.py`:
```python
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
"""
from __future__ import annotations
from dataclasses import dataclass

from src.types import OHLC, Timeframe

SESSION_GAP_TOLERANCE = 1.5


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
    stale_after: int = 100, expected_bar_ms: int | None = None,
) -> list[FVG]:
    if len(bars) < 3:
        return []
    out: list[FVG] = []
    n = len(bars)
    tol_ms = None
    if expected_bar_ms is not None:
        tol_ms = int(expected_bar_ms * SESSION_GAP_TOLERANCE)
    for i in range(1, n - 1):
        prev, mid, nxt = bars[i - 1], bars[i], bars[i + 1]
        # Session-boundary guard
        if tol_ms is not None:
            if (mid.ts - prev.ts) > tol_ms or (nxt.ts - mid.ts) > tol_ms:
                continue
        if nxt.low > prev.high:
            lo, hi = prev.high, nxt.low
            age = n - 1 - (i + 1)
            mit = _mitigated(bars, i + 1, lo, hi)
            out.append(FVG(
                type="FVG_BULL", tf=tf, lo=lo, hi=hi,
                formation_ts=mid.ts, age_bars=age,
                mitigated=mit, stale=(age > stale_after and not mit),
            ))
        if nxt.high < prev.low:
            lo, hi = nxt.high, prev.low
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
```

- [ ] **Step 2.4: Helper — expected bar cadence per TF**

Add to `src/fvg.py`:
```python
TF_EXPECTED_MS: dict[Timeframe, int] = {
    "5m": 5 * 60 * 1000,
    "1h": 60 * 60 * 1000,
    "1d": 24 * 60 * 60 * 1000,
    "1w": 7 * 24 * 60 * 60 * 1000,
}

SESSION_LIMITED_CLASSES = frozenset({"index", "stock"})


def expected_bar_ms_for(tf: Timeframe, asset_class: str | None) -> int | None:
    """For 24×5 assets (forex, commodity futures) the caller can pass None
    so no gap is considered a session break. For equities / cash indices,
    pass the TF's native cadence so overnight/weekend gaps get filtered.

    On tradfi the weekly bar has no intra-session concept; return None for 1w
    regardless of asset class — no meaningful session boundary inside a weekly bar."""
    if tf == "1w":
        return None
    if asset_class in SESSION_LIMITED_CLASSES:
        return TF_EXPECTED_MS[tf]
    return None
```

- [ ] **Step 2.5: Run tests**

Run: `uv run pytest tests/test_fvg.py -v`
Expected: PASS.

- [ ] **Step 2.6: Commit**

```bash
git add src/fvg.py tests/test_fvg.py
git commit -m "feat(fvg): add FVG detection with session-boundary filter for equities/indices"
```

---

## Task 3: Order blocks (identical to crypto-swings)

**Files:**
- Create: `src/order_blocks.py`
- Create: `tests/test_order_blocks.py`

- [ ] **Step 3.1: Failing test**

Create `tests/test_order_blocks.py`:
```python
from src.order_blocks import detect_order_blocks
from src.types import OHLC

def _b(ts, o, h, l, c, v=1.0):
    return OHLC(ts=ts, open=o, high=h, low=l, close=c, volume=v)

def test_bullish_ob_at_last_down_candle_before_displacement():
    atr = 1.0
    bars = [
        _b(0, 100, 101, 99, 100.5),
        _b(1, 100.5, 102, 100, 101.5),
        _b(2, 101.5, 101.8, 101, 101.2),
        _b(3, 101.2, 101.5, 100.5, 100.7),   # down candle (OB candidate)
        _b(4, 100.7, 104.5, 100.7, 104.3),   # displacement up, breaks 102
    ]
    obs = detect_order_blocks(bars, tf="1h", atr_14=atr, stale_after=100)
    bulls = [o for o in obs if o.type == "OB_BULL"]
    assert len(bulls) == 1
    assert bulls[0].formation_ts == bars[3].ts

def test_no_ob_below_displacement_threshold():
    atr = 2.0
    bars = [
        _b(0, 100, 101, 99, 100),
        _b(1, 100, 102, 99, 101),
        _b(2, 101, 101, 100, 100.5),
        _b(3, 100.5, 102, 100.3, 101.8),   # range 1.7 < 1.5×ATR=3.0
    ]
    obs = detect_order_blocks(bars, tf="1h", atr_14=atr, stale_after=100)
    assert obs == []
```

- [ ] **Step 3.2: Run test to fail**

Run: `uv run pytest tests/test_order_blocks.py -v`
Expected: FAIL.

- [ ] **Step 3.3: Implement src/order_blocks.py**

Create `src/order_blocks.py` with the same implementation as the crypto-swings repo (see crypto-swings plan Task 6 Step 6.3). The logic is asset-agnostic.

For completeness, paste this exact content:
```python
"""ICT Order Blocks with 1.5×ATR displacement filter.

Bullish OB = last down candle preceding a displacement UP candle whose range
exceeds 1.5×ATR and whose close breaks above the most recent swing high
within the lookback window. Bearish OB = mirror.
"""
from __future__ import annotations
from dataclasses import dataclass

from src.types import OHLC, Timeframe

DISPLACEMENT_ATR_MULT = 1.5
PRIOR_SWING_LOOKBACK = 20


@dataclass(frozen=True)
class OrderBlock:
    type: str
    tf: Timeframe
    lo: float
    hi: float
    formation_ts: int
    age_bars: int
    mitigated: bool
    stale: bool


def detect_order_blocks(
    bars: list[OHLC], *, tf: Timeframe, atr_14: float, stale_after: int = 100,
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
    seen = {}
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
```

- [ ] **Step 3.4: Run tests**

Run: `uv run pytest tests/test_order_blocks.py -v`
Expected: PASS.

- [ ] **Step 3.5: Commit**

```bash
git add src/order_blocks.py tests/test_order_blocks.py
git commit -m "feat(order_blocks): add ICT order block detection with 1.5×ATR displacement filter"
```

---

## Task 4: Market Structure (BOS / CHoCH)

**Files:**
- Create: `src/market_structure.py`
- Create: `tests/test_market_structure.py`

- [ ] **Step 4.1: Failing test**

Create `tests/test_market_structure.py`:
```python
from src.market_structure import analyze_structure

def test_uptrend_bullish_with_bos_when_price_above_last_hh():
    highs = [(1, 100.0), (3, 105.0), (5, 110.0)]
    lows  = [(2, 98.0),  (4, 102.0)]
    state = analyze_structure(highs, lows, current_price=112.0)
    assert state.bias == "bullish"
    assert state.last_bos is not None
    assert state.last_bos["direction"] == "bullish"

def test_choch_bearish_when_bullish_structure_breaks_last_hl():
    highs = [(1, 100.0), (3, 105.0)]
    lows  = [(2, 98.0),  (4, 102.0)]
    state = analyze_structure(highs, lows, current_price=101.0)
    assert state.last_choch is not None
    assert state.last_choch["direction"] == "bearish"
```

- [ ] **Step 4.2: Run test to fail**

Run: `uv run pytest tests/test_market_structure.py -v`
Expected: FAIL.

- [ ] **Step 4.3: Implement src/market_structure.py**

Create `src/market_structure.py` with the same content as crypto-swings plan Task 7 Step 7.3 — the module is asset-agnostic:
```python
"""Market structure: BOS (Break of Structure) + CHoCH (Change of Character)
derived from swing pivots.

Bias: bullish (HH+HL), bearish (LH+LL), range otherwise.
BOS: price breaks most recent pivot in trend direction (continuation).
CHoCH: first break against trend (reversal).
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class StructureState:
    bias: str
    last_bos: dict | None
    last_choch: dict | None
    invalidation_level: float | None


def analyze_structure(
    highs: list[tuple[int, float]],
    lows: list[tuple[int, float]],
    current_price: float,
) -> StructureState:
    if len(highs) < 2 or len(lows) < 2:
        return StructureState(bias="range", last_bos=None, last_choch=None,
                              invalidation_level=None)
    hh = all(highs[i][1] > highs[i - 1][1] for i in range(1, len(highs)))
    ll = all(lows[i][1]  < lows[i - 1][1]  for i in range(1, len(lows)))
    hl = all(lows[i][1]  > lows[i - 1][1]  for i in range(1, len(lows)))
    lh = all(highs[i][1] < highs[i - 1][1] for i in range(1, len(highs)))
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
        if current_price > last_hh[1]:
            last_bos = {"direction": "bullish", "level": last_hh[1], "ts": last_hh[0]}
        if current_price < last_hl[1]:
            last_choch = {"direction": "bearish", "level": last_hl[1], "ts": last_hl[0]}
    elif bias == "bearish":
        last_ll = lows[-1]
        last_lh = highs[-1]
        invalidation = last_lh[1]
        if current_price < last_ll[1]:
            last_bos = {"direction": "bearish", "level": last_ll[1], "ts": last_ll[0]}
        if current_price > last_lh[1]:
            last_choch = {"direction": "bullish", "level": last_lh[1], "ts": last_lh[0]}
    return StructureState(
        bias=bias, last_bos=last_bos, last_choch=last_choch,
        invalidation_level=invalidation,
    )
```

- [ ] **Step 4.4: Run tests**

Run: `uv run pytest tests/test_market_structure.py -v`
Expected: PASS.

- [ ] **Step 4.5: Commit**

```bash
git add src/market_structure.py tests/test_market_structure.py
git commit -m "feat(market_structure): add BOS/CHoCH structure bias analyzer"
```

---

## Task 5: Wire new layers into emit_payload

**Files:**
- Modify: `scripts/emit_payload.py` (or wherever per-instrument payload is assembled — check current location)

- [ ] **Step 5.1: Locate payload assembly**

Run:
```bash
grep -l "build\|emit_payload\|payload.json" scripts/ src/ -r
```
Expected: confirms the current payload path. The crypto template uses `scripts/emit_payload.py`; tradfi may assemble payload inside `src/main.py` or a similar orchestrator. Identify the function that writes `data/{slug}/payload.json`.

- [ ] **Step 5.2: Extend payload assembly with new modules**

In the payload-build function, after the existing fib + liquidity computation, add:
```python
from src.fvg import detect_fvgs, expected_bar_ms_for
from src.order_blocks import detect_order_blocks
from src.market_structure import analyze_structure
from src.swings import atr, detect_pivots
from src.levels import (
    cluster_levels, split_by_price,
    fibs_to_levels, pools_to_levels, fvgs_to_levels, obs_to_levels,
    structure_to_levels,
)

# Per-TF price-action signals
fvg_by_tf  = {}
ob_by_tf   = {}
ms_by_tf   = {}
for tf, bars in ohlc.items():
    tf_atr = _latest(atr(bars, 14))
    fvg_by_tf[tf] = detect_fvgs(
        bars, tf=tf, atr_14=tf_atr,
        expected_bar_ms=expected_bar_ms_for(tf, instrument["asset_class"]),
    )
    ob_by_tf[tf]  = detect_order_blocks(bars, tf=tf, atr_14=tf_atr)
    highs, lows = detect_pivots(bars, n=None)
    ms_by_tf[tf] = analyze_structure(highs, lows, current_price=current_price)

# Unified level list
levels = fibs_to_levels(fib_levels)
levels += pools_to_levels(liquidity_pools)
for fvgs in fvg_by_tf.values():
    levels += fvgs_to_levels(fvgs)
for obs in ob_by_tf.values():
    levels += obs_to_levels(obs)
for tf, ms in ms_by_tf.items():
    levels += structure_to_levels(ms, tf=tf)

# Drop far-away levels
levels = [l for l in levels if abs(l.price - current_price) / current_price <= 0.20]

# Cluster using daily ATR × 0.25 radius (existing convention)
radius = daily_atr * 0.25
zones = cluster_levels(levels, radius=radius)
support, resistance = split_by_price(zones, current_price)
```

And update the zone-to-dict serializer to emit the new fields:
```python
def z_to_dict(z):
    return {
        "min_price": round(z.min_price, 6),
        "max_price": round(z.max_price, 6),
        "mid": round(z.mid, 6),
        "score": z.score,
        "source_count": z.source_count,
        "classification": z.classification,
        "distance_pct": round((z.mid - current_price) / current_price * 100, 2),
        "sources": sorted({l.source for l in z.levels}),
        "contributing_levels": sorted(
            [{"source": l.source, "tf": l.tf, "price": round(l.price, 6), "meta": l.meta}
             for l in z.levels],
            key=lambda d: d["price"],
        ),
    }
```

Append `market_structure` to the payload dict:
```python
payload["market_structure"] = {
    tf: {
        "bias": ms.bias,
        "last_bos": ms.last_bos,
        "last_choch": ms.last_choch,
        "invalidation_level": ms.invalidation_level,
    }
    for tf, ms in ms_by_tf.items()
}
```

- [ ] **Step 5.3: Run full test suite**

Run: `uv run pytest -x`
Expected: all tests pass. Fix any test fixtures that still expect the old fib-only zone shape.

- [ ] **Step 5.4: Live smoke test against 3 asset classes**

Generate payloads for one forex (`eurusd`), one index (`spx`), one stock (`nvda`):
```bash
uv run python -m scripts.emit_payload eurusd
uv run python -m scripts.emit_payload spx
uv run python -m scripts.emit_payload nvda
```
For each, inspect the produced `data/{slug}/payload.json`:
- `resistance`/`support` contain zones with `sources` containing at least one non-FIB source.
- `market_structure.1d.bias` is set.
- For `spx` and `nvda` (equities/indices): FVGs across session boundaries should NOT appear — verify by checking FVG timestamps don't straddle midnight UTC.
- For `eurusd` (forex): FVGs may appear across any timestamp — forex is 24×5.

- [ ] **Step 5.5: Commit**

```bash
git add scripts/emit_payload.py  # or the file you modified
git commit -m "feat(payload): integrate FVG+OB+MS into unified multi-source confluence zones"
```

---

## Task 6: Rewrite analyst agent prompt

**Files:**
- Modify: `.claude/agents/tradfi-swings-analyst.md`

- [ ] **Step 6.1: Replace Input Schema and Analysis Framework**

In the analyst prompt, update the `## Input Schema` section to document the new zone fields (`source_count`, `classification`, `sources`) and the `market_structure` block. Add the following BEFORE the existing `### Rezistență` section (replace the old Pass 1 / Pass 2 / Pass 3 confluence mechanic):

```markdown
### Context structural

One short Romanian line per TF where `market_structure[tf].bias` exists, in
order 1w → 1d → 1h → 5m. Format:

- **{tf}** — {bias_ro} (ultima {BOS|CHoCH}: {direction_ro} la {price}). Invalidare: {price}.

Where `bias_ro` = `bullish` → `bullish (HH + HL)`, `bearish` → `bearish (LH + LL)`,
`range` → `range fără structură clară`. Skip range TFs unless they contradict
a HTF — note the contradiction in Pe scurt.

### Confluence classification

The pipeline assigns `classification` per zone. Use verbatim:

| Classification       | Label                       | Meaning                                           |
|----------------------|-----------------------------|---------------------------------------------------|
| `structural_pivot`   | `pivot structural`          | MS level + another source — directional          |
| `strong`             | `confluență puternică`      | 3+ distinct source families                      |
| `confluence`         | `confluență medie`          | 2 distinct source families                       |
| `level`              | — (omit from S/R)           | 1 family only                                    |

Do NOT fabricate a different label. The old `puternică/medie/slabă` Pass-1
mechanic is replaced entirely. Pass-3 catalyst-driven caution is kept below.

### Zone bullets (Rezistență + Suport)

Format:
```
- **{range}** ({distance}%) — {label} · {up to 4 sources, comma-separated}
```

Examples:
```
- **$5,612–$5,625** (+0.82%) — confluență puternică · FIB_618 (1d) · LIQ_BSL · FVG_BEAR (1h)
- **$5,490–$5,500** (−1.18%) — pivot structural · MS_CHOCH_LEVEL (1d) bearish · OB_BULL (1d)
```

Rules:
- Nearest first. Up to 4 bullets per side.
- Drop zones classified `level` unless fewer than 2 remain per side.
- When sources include MS, tag the direction (`bullish`/`bearish`) — the
  break direction is the trading thesis.
- "FVG" and "OB" stay English; timeframe tags stay English.

### Confluence combos to recognize in Pe scurt / De urmărit

- **FIB + LIQ** → stop-hunt at retrace.
- **FIB + FVG** → imbalance fill inside retrace.
- **LIQ + FVG + OB** → institutional re-entry.
- **MS_BOS + LIQ** → break triggers sweep (directional).
- **MS_CHOCH + FVG + OB** → reversal zone with entry trigger (highest conviction).

### Pass-3 catalyst caution (KEEP unchanged from previous version)

The catalyst-driven one-tier downgrade on `classification` still applies
when a high-impact event is within 6h or earnings ≤72h. Downgrade maps:
- `strong` → `confluence`
- `confluence` → `level` (drops off the list)
- `structural_pivot` → `confluence` (still surface, but softer label)
```

Keep Language, Workflow (except Pass-1/2 references), Catalizatori, De urmărit, Boundaries, and Response Format sections intact — they remain accurate.

- [ ] **Step 6.2: Commit**

```bash
git add .claude/agents/tradfi-swings-analyst.md
git commit -m "feat(analyst): rewrite tradfi-swings-analyst prompt for unified multi-source confluence"
```

---

## Task 7: End-to-end validation

- [ ] **Step 7.1: Run the full test suite**

Run: `uv run pytest -x`
Expected: all pass.

- [ ] **Step 7.2: Generate briefings across asset classes**

Choose one instrument per class and run the full pipeline through to briefing:
- Forex: `eurusd`
- Commodity (futures): `gold`
- Cash index: `spx`
- Equity: `nvda`

For each, inspect `data/{slug}/briefing.md`:
- Context structural section present, one line per TF.
- Zone bullets cite multiple source families where present.
- No FVGs spanning overnight gaps for `spx` / `nvda`.
- `eurusd` can legitimately emit FVGs across any timestamp.
- Pass-3 downgrades still fire when earnings are within 72h (test: pick a stock with upcoming earnings).

- [ ] **Step 7.3: Regression diff**

Compare each briefing against the most recent version from Notion for the same instrument:
- Overall length should be similar (≤1200 words) or slightly longer (new Context structural section).
- Zone counts shouldn't drop by more than ~30% — if they do, `classification=level` is over-filtering; loosen by lowering radius or re-including single-source zones.

- [ ] **Step 7.4: Commit any fixup + open PR**

```bash
git push -u origin feature/price-action-layer
gh pr create --title "Price-action layer (FVG + OB + MS) for tradfi" --body "$(cat <<'EOF'
## Summary
- Add FVG (with session-boundary filter for equities/indices), ICT order blocks with 1.5×ATR displacement filter, and market structure (BOS/CHoCH) modules
- Unify fib + liquidity + new sources into a single multi-source confluence scorer
- Rewrite tradfi-swings-analyst prompt: classification-based labels, combo recognition, structural context section
- Pass-3 catalyst caution retained

## Test plan
- [x] Unit tests green for all new modules
- [x] Forex/index/equity smoke briefings reviewed
- [x] FVG session-boundary filter verified on SPX and NVDA (no overnight false-positives)
EOF
)"
```

---

## Self-review notes (author-facing, pre-merge)

1. The `confluence.py` module is now effectively a legacy helper. Decide whether to delete it in a follow-up or keep it as a thin wrapper around `levels.cluster_levels` for backwards-compat of any external consumer.
2. Session-boundary filter uses the instrument's `asset_class` to decide whether to apply. Verify that every watchlist entry has `asset_class` populated (check `config/watchlist.yaml`).
3. The PRIOR_SWING_LOOKBACK = 20 constant in order_blocks.py is tuned for crypto bar counts. For tradfi 5m (RTH only ~78 bars/day) it's probably fine, but consider a per-TF tuning if OBs misfire.
4. For 1w TF: MS produces bias, but FVG produces nothing (no weekly overnight session to filter across — `expected_bar_ms_for("1w", _)` returns None so no session filter; correct). OBs on 1w are rare but valid. Verify behavior empirically.
