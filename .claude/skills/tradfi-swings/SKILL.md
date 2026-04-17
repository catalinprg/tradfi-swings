---
name: tradfi-swings
description: Full TradFi swings analysis pipeline. Fetches OHLC from yfinance across 5 timeframes for a configured watchlist (forex, commodities, indices, stocks) + VIX/DXY market-context snapshot, computes Fibonacci confluence zones, dispatches the tradfi-swings-analyst agent to produce a hedged Romanian briefing per instrument, publishes each to Notion under "TradFI", notifies Telegram once at the end. Use when the user wants TradFi S/R analysis, swing levels, or a trading briefing for forex / indices / stocks.
---

You are executing the tradfi-swings analysis pipeline. The pipeline is **per-instrument**: it loops over every slug in `config/watchlist.yaml` under `instruments:` and produces one Notion child page per slug.

Follow these steps exactly.

## Step 1 — Refresh repo

Run from the repo root:

```bash
git checkout main
git pull --ff-only
```

Ensures HEAD is on the canonical branch and any code changes since session start are pulled in.

## Step 2 — Resolve the watchlist

Read `config/watchlist.yaml`. The instrument slugs to process are all keys under `instruments:` (not under `context:` — those are fetched internally by `market_context.py`, not as separate briefings).

As of build time the watchlist is:
```
eurusd, gbpusd, usdjpy, gold, oil, spx, ndx, dji, stoxx50, ftse, dax, aapl, nvda, msft
```

Always re-read the file in case it has changed.

## Step 3 — Capture one shared timestamp

```bash
echo $(date +%Y%m%d_%H%M%S)
```

Store as `TIMESTAMP`. All instruments in this run share the same timestamp so the Notion page titles group naturally by wall-clock.

## Step 4 — Per-instrument loop

For each `SLUG` in the watchlist, execute substeps 4a→4d **sequentially** and continue to the next instrument regardless of failure (capture outcomes for the final report):

### 4a. Emit payload

```bash
python3 -m scripts.emit_payload SLUG
```

Writes `data/SLUG/payload.json`. If it exits non-zero, skip the rest of this instrument's substeps and record a failure for SLUG.

Required env: none (yfinance is keyless).

### 4b. Dispatch analyst agent

Use the Agent tool to spawn the `tradfi-swings-analyst` with this minimal prompt (substitute `SLUG`):

```
Read and analyze: data/SLUG/payload.json

Write your complete briefing as Markdown to data/SLUG/briefing.md using the Write tool. Do not include a top-level page title — the publisher sets it. After the file is saved, respond with exactly: done data/SLUG/briefing.md
```

That is the complete prompt. The agent's instructions (role, language rules, analysis framework, market-context adjustments) are embedded in its own definition.

If the agent returns `error: ...`, record the failure for SLUG and move on.

### 4c. Publish to Notion

```bash
python3 publish_notion.py data/SLUG/briefing.md SLUG TIMESTAMP
```

The script:
- Reads `data/SLUG/briefing.md`.
- Looks up the display name for SLUG from `config/watchlist.yaml`.
- Creates a child page of the TradFI parent (`345b7f28c04480598b15df10caa0d988`) titled `TradFi — {display} — TIMESTAMP`.
- Prints the new Notion page URL on the last stdout line.

Required env: `NOTION_TOKEN` (Notion Internal Integration Token — the TradFI parent must be shared with the integration).

On non-zero exit, capture stderr and record the failure for SLUG.

### 4d. Accumulate result

Store `{slug, display, outcome, notion_url | error}` for this instrument. Do NOT fire Telegram per instrument — that's done once at the end (Step 5).

## Step 5 — Single Telegram notification

After the loop completes, summarize all instruments in one Telegram message and fire it:

```bash
python3 notify_telegram.py "TradFi Swings briefings published $(date +%Y-%m-%d\ %H:%M)

✅ <display 1>: <notion_url_1>
✅ <display 2>: <notion_url_2>
⚠️ <display N>: <error snippet>
..."
```

(Use the display names from the payload, not the slugs.)

The script is idempotent: if `TELEGRAM_BOT_TOKEN` or `TELEGRAM_CHAT_ID` is unset it exits 0 silently. If the API call fails, it exits non-zero — treat that as non-fatal.

## Step 6 — Confirm to the user

Report a compact summary to the user:

- **All succeeded:** `TradFi Swings: N/N published.` followed by a bullet list `- {display}: {notion_url}` for each instrument.
- **Some failed:** `TradFi Swings: K/N published, N−K failed.` then the success bullets, then a `Failures:` section with `- {display}: {error snippet}`.
- **All failed:** `TradFi Swings: pipeline failed for all N instruments.` followed by failure reasons.

If Step 5's Telegram call failed (non-fatal), append one line: `Telegram notification failed: <stderr>`.

Do not wrap URLs in extra commentary. Keep the report terse.
