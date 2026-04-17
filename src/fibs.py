from src.types import (
    SwingPair, FibLevel, RETRACEMENT_RATIOS, EXTENSION_RATIOS,
)

def compute_levels(pair: SwingPair) -> list[FibLevel]:
    H, L = pair.high_price, pair.low_price
    rng = H - L
    levels: list[FibLevel] = []

    for r in RETRACEMENT_RATIOS:
        price = L + rng * r
        levels.append(FibLevel(
            price=price, tf=pair.tf, ratio=r, kind="retracement", pair=pair,
        ))

    for r in EXTENSION_RATIOS:
        if pair.direction == "up":
            price = H + rng * (r - 1)
        else:
            price = L - rng * (r - 1)
        levels.append(FibLevel(
            price=price, tf=pair.tf, ratio=r, kind="extension", pair=pair,
        ))

    return levels

def compute_all(pairs: list[SwingPair]) -> list[FibLevel]:
    out: list[FibLevel] = []
    for p in pairs:
        out.extend(compute_levels(p))
    return out
