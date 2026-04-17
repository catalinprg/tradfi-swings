# tradfi-swings

Fib-confluence S/R briefings for a watchlist of forex pairs, commodities, indices, and single-name stocks, with VIX + DXY as shared market context. Per-instrument Romanian briefings published to Notion.

Mirrors the architecture of `catalinprg/btc-swings` / `catalinprg/eth-swings` but:

- **Data source:** yfinance (keyless) for OHLC; Finnhub + MARKETAUX + Google News RSS for per-instrument news; Forex Factory JSON mirror (refreshed hourly by this repo's own GHA workflow) for the economic calendar.
- **No derivatives** — replaced with a `market_context` module (VIX + DXY) and per-TF momentum (RSI / MACD / ATR-percentile).
- **Per-instrument loop** — one repo, one skill, iterates over `config/watchlist.yaml`.
- **Two-phase orchestration** — once-per-run macro fetch (news + calendar) → per-instrument technical pipeline.
- **Timeframes:** `5m, 1h, 1d, 1w` (1M dropped; 4h dropped because yfinance has no native 4h interval and client-resampled 4h bars cross equity sessions; 5m added for intraday structure).

## Watchlist (config/watchlist.yaml)

15 instruments, news sources per row:

| Slug | Display | Symbol | Class | News |
|---|---|---|---|---|
| eurusd | EUR/USD | `EURUSD=X` | forex | MARKETAUX + RSS |
| gbpusd | GBP/USD | `GBPUSD=X` | forex | MARKETAUX + RSS |
| usdjpy | USD/JPY | `USDJPY=X` | forex | MARKETAUX + RSS |
| gold | Gold | `GC=F` | commodity | MARKETAUX + RSS |
| silver | Silver | `SI=F` | commodity | MARKETAUX + RSS |
| oil | Oil (WTI) | `CL=F` | commodity | MARKETAUX + RSS |
| spx | S&P 500 | `^GSPC` | index | RSS |
| ndx | Nasdaq 100 | `^NDX` | index | RSS |
| dji | Dow Jones | `^DJI` | index | RSS |
| dax | DAX | `^GDAXI` | index | RSS |
| aapl | Apple | `AAPL` | stock | Finnhub |
| nvda | Nvidia | `NVDA` | stock | Finnhub |
| msft | Microsoft | `MSFT` | stock | Finnhub |
| tsla | Tesla | `TSLA` | stock | Finnhub |
| amzn | Amazon | `AMZN` | stock | Finnhub |

Plus shared market context (fetched once per run):

| Slug | Symbol |
|---|---|
| VIX | `^VIX` |
| DXY | `DX-Y.NYB` |

## How it runs

The `tradfi-swings` skill orchestrates in two phases:

**Phase 1 — Macro fetch (once per run)**
1. `python -m scripts.emit_macro` → pulls news per instrument (Finnhub for equities, MARKETAUX for forex + commodities, Google News RSS fallback) plus the economic calendar (next 48h, high/medium impact) → writes `data/macro_context.json`.

**Phase 2 — Per-instrument loop**
2. `python -m scripts.emit_payload <slug>` → fetches OHLC + VIX/DXY + per-TF RSI/MACD/ATR-percentile → writes `data/<slug>/payload.json`.
3. Dispatches the `tradfi-swings-analyst` agent → reads both `data/<slug>/payload.json` and `data/macro_context.json` → writes `data/<slug>/briefing.md` with a Catalizatori section.
4. `python publish_notion.py data/<slug>/briefing.md <slug> <timestamp>` → creates a Notion child page under the instrument's dedicated parent. The TradFI workspace page holds one child per asset (`EUR/USD`, `S&P 500`, `Apple`, etc.); each run stacks a new timestamped analysis inside.

One Telegram summary at the end (all instruments rolled up).

## The briefing format

Full-Romanian. Five sections per instrument:

- **Preț curent** — price line with 24h Δ and ATR.
- **Pe scurt** — 2–4 hedged sentences: 24h move vs ATR, position vs structure, optional VIX/DXY or momentum signal.
- **Rezistență** / **Suport** — nearest-first, each zone tagged `confluență puternică / medie / slabă`. Two-pass strength: structural (score + TF weight + fib type + TF diversity + RSI/MACD alignment) + market-context adjustment (VIX extremes for equities, DXY moves for forex/commodities, class-specific sign conventions).
- **Catalizatori** — up to 3 bullets combining calendar events (📅) and news (📰), filtered to what actually moves this instrument. Omitted when nothing relevant.
- **De urmărit** — Sus / Jos / Invalidare triggers.

## Env vars

- `NOTION_TOKEN` — Notion Internal Integration Token. Each per-asset parent page (listed under `notion_parent` in `config/watchlist.yaml`) must be shared with the integration. Sharing the top-level TradFI page with the integration propagates access to all children automatically.
- `FINNHUB_API_KEY` — equity news. Optional (falls back to RSS for stocks if unset).
- `MARKETAUX_API_KEY` — forex + commodity news. Optional (falls back to RSS if unset).
- `FIRECRAWL_API_KEY` — article-content extraction fallback when trafilatura fails (JS-rendered pages, paywall interstitials). Optional. Without it, only trafilatura-extractable articles gain `content`; the agent still falls back to headline + summary for the rest.
- `FIRECRAWL_BUDGET_PER_RUN` — cap on Firecrawl calls per pipeline run (default `10`). Prevents blowing the free-tier quota on a bad-extraction day.
- `ALPHAVANTAGE_API_KEY` — fallback for the VIX + DXY market-context snapshot when yfinance returns empty. Uses Alpha Vantage's `TIME_SERIES_DAILY` on ETF proxies (defaults: `VIXY` for VIX, `UUP` for DXY). Override the proxies via `ALPHAVANTAGE_VIX_SYMBOL` / `ALPHAVANTAGE_DXY_SYMBOL` if needed. Optional; market_context fails soft without it.
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` — optional; notification degrades silently if unset.

yfinance (OHLC) is keyless. The economic calendar is served by this repo's own `data-mirror/ff_calendar_thisweek.json`, refreshed every 4 hours by `.github/workflows/update-calendar.yml` pulling from `nfs.faireconomy.media`. The repo must be **public** because Claude Code cloud sessions read from `raw.githubusercontent.com` unauthenticated.

## Cloud setup (Claude Code cloud environment)

**Setup command:**
```bash
#!/bin/bash
uv pip install --system yfinance pandas numpy requests PyYAML feedparser trafilatura
```

**Outbound allowlist:**
- `query1.finance.yahoo.com`, `query2.finance.yahoo.com`, `finance.yahoo.com`, `fc.yahoo.com`, `*.finance.yahoo.com` — yfinance
- `finnhub.io` — equity news API
- `api.marketaux.com` — forex/commodity news API
- `news.google.com` — RSS fallback
- `raw.githubusercontent.com` — economic calendar mirror
- `www.alphavantage.co` — market-context fallback (optional)
- `api.notion.com`
- `api.telegram.org`
- **Article-content extraction** reaches into many news publisher domains (Reuters, CNBC, Bloomberg, FT, FXStreet, Yahoo Finance, etc.). On a restricted-allowlist env, extraction fails silently and the agent falls back to headline + summary. On a full-trust env, extraction lands 70–90% of articles depending on publisher paywalls.

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
