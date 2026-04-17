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
from datetime import datetime, timezone
from pathlib import Path

import yaml

from src import econ_calendar as econ_calendar_mod
from src import news as news_mod


def _load_watchlist() -> dict:
    root = Path(__file__).resolve().parent.parent
    with open(root / "config" / "watchlist.yaml") as f:
        return yaml.safe_load(f)


def build() -> dict:
    watchlist = _load_watchlist()

    per_instrument: dict[str, dict] = {}
    for slug, instr in watchlist["instruments"].items():
        items, source = news_mod.fetch_for_instrument(instr)
        per_instrument[slug] = {
            "display":         instr["display"],
            "asset_class":     instr["asset_class"],
            "relevance_terms": instr.get("relevance_terms", []),
            "news_source":     source,
            "items":           items,
        }

    events = econ_calendar_mod.fetch()

    return {
        "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "per_instrument_news": per_instrument,
        "economic_calendar":   events,
        "coverage_note":       (
            "News items are up to 3 per instrument, last 48h. "
            "Calendar holds high/medium impact events within a 48h window."
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
    print(f"macro context written: {out_file}")
    print(
        f"  news items: {total_news} across {len(payload['per_instrument_news'])} instruments"
    )
    print(f"  calendar events (48h window): {len(payload['economic_calendar'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
