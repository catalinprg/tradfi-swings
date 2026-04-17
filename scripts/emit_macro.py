"""Run the macro-fetch phase of the tradfi-swings pipeline.

Fetches news for every instrument in the watchlist and the economic
calendar once. Emits data/macro_context.json, which the analyst reads
alongside the per-instrument payload to build the Catalizatori section.

Usage:
    python -m scripts.emit_macro [output_path]
    # default: data/macro_context.json

Env vars: FINNHUB_API_KEY, MARKETAUX_API_KEY (both optional — each news
source degrades silently when its key is missing; RSS fallback covers
the gap).

This script is deliberately non-fatal: if every news source fails and
the calendar is unreachable, it still emits an empty-but-structured
payload so Phase 2 can run with a blank Catalizatori section rather
than breaking.
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import yaml

from src import article_extract as article_extract_mod
from src import earnings_calendar as earnings_calendar_mod
from src import econ_calendar as econ_calendar_mod
from src import news as news_mod


EXTRACT_CONCURRENCY = 10


def _norm_url(url: str) -> str:
    """Cross-publisher URL normalization for dedup. Strips query string,
    fragment, and trailing slash; lowercases the host + path. Two URLs with
    the same (host, path) map to the same key regardless of tracking
    parameters (utm_*, ref, ...)."""
    if not url:
        return ""
    try:
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc.lower()}{p.path.rstrip('/').lower()}"
    except Exception:
        return url.strip().lower()


def _dedup_key(item: dict) -> str:
    """Preferred dedup key is the normalized URL; fall back to the
    lowercased headline when URL is missing (which can happen for some
    RSS feeds that only emit a guid)."""
    norm = _norm_url(item.get("url") or "")
    if norm:
        return "u:" + norm
    headline = (item.get("headline") or "").strip().lower()
    return "h:" + headline


def _specificity_score(item: dict, instr: dict) -> int:
    """How many of this instrument's `relevance_terms` appear in the
    article. Higher = the article is more obviously about this instrument.
    Uses headline + summary + content when available."""
    terms = [t.lower() for t in (instr.get("relevance_terms") or []) if t]
    if not terms:
        return 0
    text = " ".join([
        (item.get("headline")  or ""),
        (item.get("summary")   or ""),
        (item.get("content")   or ""),
    ]).lower()
    return sum(1 for t in terms if t in text)


def _dedup_across_instruments(per_instrument: dict, watchlist: dict) -> int:
    """Remove duplicate news items from every instrument except the one
    where the item is most specific. Ties break by the instrument's
    position in the watchlist (earlier wins, which is stable and keeps
    asset-class ordering — forex before indices before single names).

    Returns the count of dropped duplicates (informational). Mutates
    `per_instrument[slug]['items']` in place."""
    order = {slug: i for i, slug in enumerate(watchlist["instruments"].keys())}

    # 1. Collect every (slug, item) pair grouped by dedup key.
    by_key: dict[str, list[tuple[str, dict]]] = {}
    for slug, row in per_instrument.items():
        for it in row.get("items", []):
            by_key.setdefault(_dedup_key(it), []).append((slug, it))

    # 2. For each group with > 1 claimant, pick the winner.
    to_drop: dict[str, set[str]] = {}  # slug -> set of dedup keys to drop
    for key, claimants in by_key.items():
        if len(claimants) < 2:
            continue
        def rank(pair):
            slug, item = pair
            return (
                -_specificity_score(item, watchlist["instruments"][slug]),
                order.get(slug, 10_000),
            )
        winner_slug, _ = min(claimants, key=rank)
        for slug, _ in claimants:
            if slug != winner_slug:
                to_drop.setdefault(slug, set()).add(key)

    # 3. Apply.
    dropped = 0
    for slug, keys in to_drop.items():
        kept = []
        for it in per_instrument[slug]["items"]:
            if _dedup_key(it) in keys:
                dropped += 1
                continue
            kept.append(it)
        per_instrument[slug]["items"] = kept
    return dropped


def _load_watchlist() -> dict:
    root = Path(__file__).resolve().parent.parent
    with open(root / "config" / "watchlist.yaml") as f:
        return yaml.safe_load(f)


def _enrich_with_content(items: list[dict]) -> list[dict]:
    """Fan out article fetches across a thread pool; each item gets a
    `content` field added IN PLACE so per_instrument's dict references
    stay live. Failures pass through silently as `content: None` — the
    agent falls back to the `summary` field when content is absent."""
    if not items:
        return items

    def _work(item):
        return item, article_extract_mod.extract(item.get("url") or "")

    with ThreadPoolExecutor(max_workers=EXTRACT_CONCURRENCY) as pool:
        futures = [pool.submit(_work, it) for it in items]
        for fut in as_completed(futures):
            item, content = fut.result()
            item["content"] = content   # mutate in place
    return items


def build() -> dict:
    watchlist = _load_watchlist()
    article_extract_mod.reset_firecrawl_budget()

    per_instrument: dict[str, dict] = {}
    # Collect every news item across all instruments so we can fan out
    # article extraction in one parallel pass (faster than serial-per-slug).
    all_items: list[dict] = []
    for slug, instr in watchlist["instruments"].items():
        items, source = news_mod.fetch_for_instrument(instr)
        per_instrument[slug] = {
            "display":         instr["display"],
            "asset_class":     instr["asset_class"],
            "relevance_terms": instr.get("relevance_terms", []),
            "news_source":     source,
            "items":           items,
        }
        all_items.extend(items)

    # Dedup shared articles across instruments BEFORE extraction so we
    # don't burn Firecrawl budget fetching the same Reuters page 5 times.
    dropped_dupes = _dedup_across_instruments(per_instrument, watchlist)
    # Rebuild the flat list from the pruned per-instrument dicts so
    # `_enrich_with_content` only hits unique items.
    all_items = [
        it
        for row in per_instrument.values()
        for it in row["items"]
    ]

    # Single-pass parallel enrichment. Modifies the item dicts in place
    # (they're the same references inside per_instrument).
    _enrich_with_content(all_items)

    events = econ_calendar_mod.fetch()
    earnings = earnings_calendar_mod.fetch_for_watchlist(watchlist["instruments"])

    return {
        "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "per_instrument_news": per_instrument,
        "economic_calendar":   events,
        "earnings_calendar":   earnings,
        "dedup_stats":         {"cross_instrument_duplicates_dropped": dropped_dupes},
        "coverage_note":       (
            "News items are up to 3 per instrument, last 48h. "
            "Calendar holds high/medium impact events within a 48h window. "
            "Earnings cover the next 14 days for watchlist stocks. "
            "Articles that matched multiple instruments are kept only on "
            "the one with the highest relevance-term specificity."
        ),
    }


def main() -> int:
    out_path = sys.argv[1] if len(sys.argv) > 1 else "data/macro_context.json"
    payload = build()

    out_file = Path(out_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w") as f:
        json.dump(payload, f, indent=2)

    total_news = sum(len(v["items"]) for v in payload["per_instrument_news"].values())
    items_with_content = sum(
        1
        for v in payload["per_instrument_news"].values()
        for it in v["items"]
        if it.get("content")
    )
    print(f"macro context written: {out_file}")
    print(
        f"  news items: {total_news} across {len(payload['per_instrument_news'])} instruments "
        f"({items_with_content}/{total_news} with extracted article content)"
    )
    print(f"  calendar events (48h window): {len(payload['economic_calendar'])}")
    print(f"  earnings events (14d window): {len(payload['earnings_calendar'])}")
    print(f"  cross-instrument duplicates dropped: {payload['dedup_stats']['cross_instrument_duplicates_dropped']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
