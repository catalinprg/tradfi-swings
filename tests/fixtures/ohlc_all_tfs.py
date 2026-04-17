import math
from src.types import OHLC, Timeframe

def synthetic(tf: Timeframe, n: int, seed: float = 0.0) -> list[OHLC]:
    """Deterministic sine-wave OHLC for tests."""
    out = []
    for i in range(n):
        base = 60000 + 2000 * math.sin((i + seed) * math.pi / 10)
        out.append(OHLC(
            ts=i * 1000,
            open=base,
            high=base + 100,
            low=base - 100,
            close=base + 10,
            volume=1.0,
        ))
    return out

def synthetic_all() -> dict[Timeframe, list[OHLC]]:
    return {
        "1w": synthetic("1w", 104, seed=1),
        "1d": synthetic("1d", 200, seed=2),
        "1h": synthetic("1h", 500, seed=4),
        "5m": synthetic("5m", 500, seed=5),
    }
