---
name: tradfi-swings
description: Full TradFi swings pipeline. Fetches OHLC from yfinance across 5 timeframes for a 15-instrument watchlist (forex, commodities, indices, stocks) plus VIX/DXY market context and per-TF RSI/MACD. Pulls news (Finnhub/MARKETAUX/RSS) + economic calendar, dispatches the tradfi-swings-analyst per instrument to produce a hedged Romanian briefing with a Catalizatori section, publishes each to Notion under the instrument's dedicated parent page, and sends one Telegram rollup. Use when the user wants TradFi S/R analysis, swing levels, or a trading briefing for forex / indices / stocks.
---

You are executing the tradfi-swings analysis pipeline. The pipeline is **two-phase**: one macro fetch shared across all instruments, then a per-instrument loop that publishes one Notion child page per slug under the instrument's dedicated parent.

Follow these steps exactly.

## Step 1 — Refresh repo

Run from the repo root:

```bash
git checkout main
git pull --ff-only
```

Ensures HEAD is on the canonical branch and any code changes since session start are pulled in.

## Step 2 — Capture one shared timestamp

```bash
echo $(date +%Y%m%d_%H%M%S)
```

Store as `TIMESTAMP`. All instruments in this run share the same timestamp so the Notion page titles group naturally by wall-clock.

## Step 3 — Phase 1: macro fetch (once)

```bash
python3 -m scripts.emit_macro
```

Writes `data/macro_context.json` with:
- Per-instrument news (3 items max per slug, last 48h, via Finnhub for equities / MARKETAUX for forex+commodities / Google News RSS fallback for indices).
- Economic calendar (high/medium impact events in the next 48h, read from this repo's own `data-mirror/ff_calendar_thisweek.json`, refreshed every 4h by a GHA workflow).

Required env: `FINNHUB_API_KEY`, `MARKETAUX_API_KEY` (each optional — news falls back to RSS when a key is missing).

If this step fails entirely, **continue anyway** — the analyst handles an absent or empty `macro_context.json` by omitting the Catalizatori section. Do not abort the pipeline.

## Step 4 — Resolve the watchlist

Read `config/watchlist.yaml`. The instrument slugs to process are all keys under `instruments:` (not under `context:` — those are fetched internally by `market_context.py`, not as separate briefings).

As of build time the watchlist is:
```
eurusd, gbpusd, usdjpy, gold, silver, oil, spx, ndx, dji, dax,
aapl, nvda, msft, tsla, amzn
```

Always re-read the file in case it has changed. **Skip any slug whose `notion_parent` is null** — that means the user hasn't created the Notion parent page yet. Record as a skipped entry in the final report; don't try to publish.

## Step 5 — Phase 2: per-instrument loop

For each `SLUG` in the resolved watchlist, execute substeps 5a→5c **sequentially**. Continue to the next instrument regardless of failure (capture outcomes for the final report).

### 5a. Emit payload

```bash
python3 -m scripts.emit_payload SLUG
```

Writes `data/SLUG/payload.json` with: OHLC-derived fib confluence zones, VIX/DXY context, per-TF RSI/MACD/ATR-percentile.

If non-zero exit, skip 5b+5c, record a failure for SLUG.

### 5b. Dispatch analyst agent

Use the Agent tool to spawn the `tradfi-swings-analyst` with this minimal prompt (substitute `SLUG`):

```
Read and analyze: data/SLUG/payload.json
Also read (if present): data/macro_context.json

Write your complete briefing as Markdown to data/SLUG/briefing.md using the Write tool. Do not include a top-level page title — the publisher sets it. After the file is saved, respond with exactly: done data/SLUG/briefing.md
```

The agent's instructions (role, Catalizatori filtering rules, momentum guidance, language rules) are embedded in its own definition.

If the agent returns `error: ...`, record the failure for SLUG and move on.

### 5c. Publish to Notion

```bash
python3 publish_notion.py data/SLUG/briefing.md SLUG TIMESTAMP
```

The script:
- Reads `data/SLUG/briefing.md`.
- Looks up `notion_parent` for SLUG from `config/watchlist.yaml`.
- Creates a child page under that parent, titled by the formatted timestamp (e.g. `2026-04-17 14:00 UTC`).
- Prints the new Notion page URL on the last stdout line.

Required env: `NOTION_TOKEN` (each per-asset parent page must be shared with the integration).

On non-zero exit, capture stderr and record the failure for SLUG.

## Step 6 — Single Telegram notification

After the loop completes, summarize all instruments in one Telegram message and fire it:

```bash
python3 notify_telegram.py "TradFi Swings briefings published $(date +%Y-%m-%d\ %H:%M)

✅ <display 1>: <notion_url_1>
✅ <display 2>: <notion_url_2>
⚠️ <display N>: <error snippet>
⏭️ <display M>: notion_parent missing, skipped
..."
```

(Use display names, not slugs.)

The script is idempotent. Unset `TELEGRAM_*` env vars → silent no-op. API failure → non-fatal.

## Step 7 — Confirm to the user

Report a compact summary:

- **All succeeded:** `TradFi Swings: K/N published.` with a bullet list per instrument.
- **Some failed / skipped:** `TradFi Swings: K/N published, F failed, S skipped.` then success bullets, then a `Failures:` / `Skipped:` section with one-line reasons.
- **All failed:** `TradFi Swings: pipeline failed for all N instruments.` with failure reasons.

If Step 6's Telegram call failed (non-fatal), append one line: `Telegram notification failed: <stderr>`.

Do not wrap URLs in extra commentary. Keep the report terse.
