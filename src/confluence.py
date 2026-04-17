from src.types import FibLevel, Zone, TF_WEIGHTS, LEVEL_WEIGHTS

MAX_ZONE_WIDTH_MULTIPLIER = 2.0

def cluster(levels: list[FibLevel], radius: float) -> list[Zone]:
    """Merge levels into zones. A level joins the current zone only if it's within
    `radius` of the last level AND the resulting zone width stays <= 2 * radius.
    Without the width cap, transitive chaining can produce absurdly wide zones."""
    if not levels:
        return []
    sorted_levels = sorted(levels, key=lambda l: l.price)
    zones: list[list[FibLevel]] = [[sorted_levels[0]]]
    max_width = radius * MAX_ZONE_WIDTH_MULTIPLIER
    for lvl in sorted_levels[1:]:
        within_radius = lvl.price - zones[-1][-1].price <= radius
        within_width = lvl.price - zones[-1][0].price <= max_width
        if within_radius and within_width:
            zones[-1].append(lvl)
        else:
            zones.append([lvl])
    out: list[Zone] = []
    for group in zones:
        z = Zone(
            min_price=min(l.price for l in group),
            max_price=max(l.price for l in group),
            score=0,
            levels=tuple(group),
        )
        out.append(score_zone(z))
    return out

def score_zone(zone: Zone) -> Zone:
    s = 0
    for lvl in zone.levels:
        s += TF_WEIGHTS[lvl.tf] * LEVEL_WEIGHTS[lvl.ratio]
    return Zone(
        min_price=zone.min_price,
        max_price=zone.max_price,
        score=s,
        levels=zone.levels,
    )

def split_by_price(
    zones: list[Zone], current_price: float
) -> tuple[list[Zone], list[Zone]]:
    """Return (support, resistance) — support below price, resistance above.
    Zones straddling the current price are assigned to whichever side their midpoint sits."""
    support: list[Zone] = []
    resistance: list[Zone] = []
    for z in zones:
        if z.mid < current_price:
            support.append(z)
        else:
            resistance.append(z)
    support.sort(key=lambda z: z.score, reverse=True)
    resistance.sort(key=lambda z: z.score, reverse=True)
    return support, resistance
