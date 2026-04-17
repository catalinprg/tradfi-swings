# tradfi-swings

Fib-confluence S/R briefings for a watchlist of forex pairs, commodities, indices, and single-name stocks, with VIX + DXY as shared market context. Per-instrument Romanian briefings published to Notion.

Mirrors the architecture of `catalinprg/btc-swings` / `catalinprg/eth-swings` but:

- **Data source:** yfinance (keyless) instead of Binance + Coinalyze.
- **No derivatives** — replaced with a `market_context` module holding VIX + DXY.
- **Per-instrument loop** — one repo, one skill, iterates over `config/watchlist.yaml`.
- **Timeframes:** `5m, 1h, 4h, 1d, 1w` (1M dropped; 5m added for intraday structure).

## Watchlist (config/watchlist.yaml)

| Slug | Display | Symbol | Class |
|---|---|---|---|
| eurusd | EUR/USD | `EURUSD=X` | forex |
| gbpusd | GBP/USD | `GBPUSD=X` | forex |
| usdjpy | USD/JPY | `USDJPY=X` | forex |
| gold | Gold | `GC=F` | commodity |
| oil | Oil (WTI) | `CL=F` | commodity |
| spx | S&P 500 | `^GSPC` | index |
| ndx | Nasdaq 100 | `^NDX` | index |
| dji | Dow Jones | `^DJI` | index |
| stoxx50 | EuroStoxx 50 | `^STOXX50E` | index |
| ftse | FTSE 100 | `^FTSE` | index |
| dax | DAX | `^GDAXI` | index |
| aapl | Apple | `AAPL` | stock |
| nvda | Nvidia | `NVDA` | stock |
| msft | Microsoft | `MSFT` | stock |

Plus shared market context (fetched once per run):

| Slug | Symbol |
|---|---|
| VIX | `^VIX` |
| DXY | `DX-Y.NYB` |

## How it runs

The `tradfi-swings` skill orchestrates, per instrument:

1. `python -m scripts.emit_payload <slug>` → fetches OHLC + VIX/DXY snapshot → writes `data/<slug>/payload.json`.
2. Dispatches the `tradfi-swings-analyst` agent → reads the payload → writes `data/<slug>/briefing.md`.
3. `python publish_notion.py data/<slug>/briefing.md <slug> <timestamp>` → creates a Notion child page under the TradFI parent (`345b7f28c04480598b15df10caa0d988`).

One Telegram summary at the end (all instruments rolled up).

## The briefing format

Full-Romanian. Four sections per instrument:

- **Preț curent** — price line with 24h Δ and ATR.
- **Pe scurt** — 2–4 hedged sentences: 24h move vs ATR, position vs structure, optional VIX/DXY signal weighted by asset class.
- **Rezistență** / **Suport** — nearest-first, each zone tagged `confluență puternică / medie / slabă`. Two-pass strength: structural (score + TF weight + fib type + TF diversity) + market-context adjustment (VIX extremes for equities, DXY moves for forex/commodities, class-specific sign conventions).
- **De urmărit** — Sus / Jos / Invalidare triggers.

## Env vars

- `NOTION_TOKEN` — Notion Internal Integration Token. TradFI parent page (`345b7f28c04480598b15df10caa0d988`) must be shared with the integration.
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` — optional; notification degrades silently if unset.

No API keys for data (yfinance is keyless).

## Cloud setup (Claude Code cloud environment)

**Setup command:**
```bash
#!/bin/bash
uv pip install --system yfinance pandas numpy requests PyYAML
```

**Outbound allowlist:**
- `query1.finance.yahoo.com`, `query2.finance.yahoo.com`, `finance.yahoo.com`, `fc.yahoo.com`, `*.finance.yahoo.com` — yfinance
- `api.notion.com`
- `api.telegram.org`

## Local dev

```bash
python3 -m venv .venv
.venv/bin/pip install yfinance pandas numpy requests PyYAML pytest
.venv/bin/python -m pytest -q           # run tests
.venv/bin/python -m scripts.emit_payload eurusd   # smoke test
```

## Design notes

- Timeframe `4h` isn't native to yfinance — we fetch 1h and resample client-side.
- `1m` is excluded: yfinance caps it at ~7 days of history, too shallow for reliable swing pivots.
- Strength labels are agent judgment, not a mechanical fib count. See `.claude/agents/tradfi-swings-analyst.md` for the full rubric.
- VIX/DXY adjustments downgrade (never upgrade) strength tiers; thresholds are per-asset-class. See the agent prompt for specifics.
