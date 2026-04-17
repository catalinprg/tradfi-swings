from dataclasses import dataclass
from typing import Literal

Timeframe = Literal["1w", "1d", "1h", "5m"]
SwingDirection = Literal["up", "down"]
LevelKind = Literal["retracement", "extension"]

@dataclass(frozen=True)
class OHLC:
    ts: int         # open time, ms since epoch
    open: float
    high: float
    low: float
    close: float
    volume: float

@dataclass(frozen=True)
class SwingPair:
    tf: Timeframe
    high_price: float
    high_ts: int
    low_price: float
    low_ts: int
    direction: SwingDirection  # "up" = high more recent; "down" = low more recent

@dataclass(frozen=True)
class FibLevel:
    price: float
    tf: Timeframe
    ratio: float
    kind: LevelKind
    pair: SwingPair

@dataclass(frozen=True)
class Zone:
    min_price: float
    max_price: float
    score: int
    levels: tuple[FibLevel, ...]

    @property
    def mid(self) -> float:
        return (self.min_price + self.max_price) / 2

TF_WEIGHTS: dict[Timeframe, int] = {"1w": 5, "1d": 4, "1h": 2, "5m": 1}
LEVEL_WEIGHTS: dict[float, int] = {
    0.236: 1, 0.382: 2, 0.5: 3, 0.618: 3, 0.786: 2, 1.272: 2, 1.618: 3,
}
RETRACEMENT_RATIOS = (0.236, 0.382, 0.5, 0.618, 0.786)
EXTENSION_RATIOS = (1.272, 1.618)
