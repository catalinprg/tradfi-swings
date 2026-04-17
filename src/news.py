"""News fetch: Finnhub (equities) + MARKETAUX (forex/commodities) + RSS fallback.

Source selection is driven by per-instrument metadata in config/watchlist.yaml
(`finnhub_symbol`, `marketaux_symbol`, `rss_query`). Caller passes the
watchlist row for the instrument.

Env vars (each optional — module degrades silently when a key is missing):
  FINNHUB_API_KEY     — required only if a watchlist row has finnhub_symbol
  MARKETAUX_API_KEY   — required only if a watchlist row has marketaux_symbol

Returns a list of dicts, each with headline / source / published / url /
summary. Up to NEWS_MAX_ITEMS per call. Empty list on failure across all
sources.
"""
import os
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "")
MARKETAUX_API_KEY = os.environ.get("MARKETAUX_API_KEY", "")

NEWS_MAX_ITEMS = 3
NEWS_MAX_AGE_HOURS = 48
HTTP_TIMEOUT = 8
HTTP_MAX_RETRIES = 3
HTTP_BACKOFF_BASE = 2
RSS_TIMEOUT = 5
RSS_MAX_RETRIES = 3


def _http_get(url: str, headers: Optional[dict] = None, timeout: int = HTTP_TIMEOUT) -> Optional[requests.Response]:
    """GET with exponential backoff on 5xx responses AND transient network
    errors. Returns the final Response (possibly still 5xx) or None if every
    attempt raised. Matches the pattern in financial-briefing — a plain
    `return requests.get(...)` would miss 5xx bodies because requests does
    not raise on them."""
    resp: Optional[requests.Response] = None
    for attempt in range(HTTP_MAX_RETRIES):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            if resp.status_code < 500:
                return resp
            # 5xx → fall through to backoff + retry
        except requests.RequestException:
            # Also retry on connection errors, DNS failures, timeouts
            pass
        if attempt < HTTP_MAX_RETRIES - 1:
            time.sleep(HTTP_BACKOFF_BASE ** attempt)
    return resp


def _is_fresh_iso(iso_str: str) -> bool:
    try:
        pub_dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except Exception:
        return False
    age_h = (datetime.now(timezone.utc) - pub_dt).total_seconds() / 3600
    return 0 <= age_h <= NEWS_MAX_AGE_HOURS


def _is_external_url(url: str) -> bool:
    """Reject Finnhub's own image / tracking URLs masquerading as articles."""
    return bool(url) and "finnhub.io" not in url


def fetch_finnhub(symbol: str, max_items: int = NEWS_MAX_ITEMS) -> Optional[list[dict]]:
    if not FINNHUB_API_KEY:
        return None
    today = datetime.now(timezone.utc).date()
    from_date = (today - timedelta(days=7)).isoformat()
    to_date = today.isoformat()
    url = (
        f"https://finnhub.io/api/v1/company-news"
        f"?symbol={symbol}&from={from_date}&to={to_date}"
    )
    resp = _http_get(url, headers={"X-Finnhub-Token": FINNHUB_API_KEY}, timeout=6)
    if resp is None or resp.status_code != 200:
        return None
    items: list[dict] = []
    for art in resp.json():
        pub_ts = art.get("datetime")
        if not pub_ts:
            continue
        pub_dt = datetime.fromtimestamp(pub_ts, tz=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - pub_dt).total_seconds() / 3600
        if age_hours > NEWS_MAX_AGE_HOURS:
            continue
        article_url = art.get("url", "")
        if not _is_external_url(article_url):
            continue
        headline = art.get("headline", "").strip()
        summary = art.get("summary", "")[:300].strip() or headline or None
        items.append({
            "headline":  headline,
            "source":    art.get("source", "").strip(),
            "published": pub_dt.isoformat(),
            "url":       article_url,
            "summary":   summary,
        })
        if len(items) >= max_items:
            break
    return items or None


def fetch_marketaux(symbol: str, max_items: int = NEWS_MAX_ITEMS) -> Optional[list[dict]]:
    if not MARKETAUX_API_KEY:
        return None
    today = datetime.now(timezone.utc).date()
    published_after = (today - timedelta(days=2)).isoformat() + "T00:00:00"
    url = (
        f"https://api.marketaux.com/v1/news/all"
        f"?symbols={symbol}"
        f"&filter_entities=true"
        f"&published_after={published_after}"
        f"&language=en"
        f"&api_token={MARKETAUX_API_KEY}"
    )
    resp = _http_get(url, timeout=HTTP_TIMEOUT)
    if resp is None or resp.status_code != 200:
        return None
    items: list[dict] = []
    for art in resp.json().get("data", []):
        pub_str = art.get("published_at", "")
        if not pub_str or not _is_fresh_iso(pub_str):
            continue
        article_url = art.get("url", "")
        if not article_url:
            continue
        headline = art.get("title", "").strip()
        summary = (art.get("description") or art.get("snippet") or "")[:300].strip() or headline or None
        items.append({
            "headline":  headline,
            "source":    art.get("source", "").strip(),
            "published": pub_str,
            "url":       article_url,
            "summary":   summary,
        })
        if len(items) >= max_items:
            break
    return items or None


def fetch_rss(query: str, max_items: int = NEWS_MAX_ITEMS) -> list[dict]:
    """Google News RSS fallback. Always returns a list (possibly empty)."""
    try:
        import feedparser  # lazy import — feedparser isn't a runtime dep for everyone
    except ImportError:
        return []

    encoded = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    for attempt in range(RSS_MAX_RETRIES):
        try:
            resp = requests.get(url, headers=headers, timeout=RSS_TIMEOUT)
            if resp.status_code != 200:
                if attempt < RSS_MAX_RETRIES - 1:
                    time.sleep(HTTP_BACKOFF_BASE ** attempt)
                continue
            feed = feedparser.parse(resp.content)
            items: list[dict] = []
            for entry in feed.entries:
                pub_date = entry.get("published", "")
                if not pub_date:
                    continue
                # Google RSS dates like "Wed, 17 Apr 2026 12:34:00 GMT" —
                # try parseable ISO first, fall through if the string format
                # doesn't match.
                try:
                    pub_dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                except Exception:
                    continue
                age_h = (datetime.now(timezone.utc) - pub_dt).total_seconds() / 3600
                if age_h > NEWS_MAX_AGE_HOURS:
                    continue
                source = (
                    entry.source.title if hasattr(entry, "source")
                    else entry.title.rsplit(" - ", 1)[-1]
                )
                title = (
                    entry.title.rsplit(" - ", 1)[0]
                    if " - " in entry.title else entry.title
                )
                items.append({
                    "headline":  title.strip(),
                    "source":    source.strip(),
                    "published": pub_dt.isoformat(),
                    "url":       entry.link,
                    "summary":   title.strip(),
                })
                if len(items) >= max_items:
                    break
            return items
        except requests.RequestException:
            if attempt < RSS_MAX_RETRIES - 1:
                time.sleep(HTTP_BACKOFF_BASE ** attempt)
    return []


def fetch_for_instrument(instr: dict) -> tuple[list[dict], str]:
    """Dispatch news fetch based on a watchlist row. Returns
    (items, source_tag). Source tag is one of: "finnhub", "marketaux",
    "rss", "none"."""
    fh_symbol = instr.get("finnhub_symbol")
    mx_symbol = instr.get("marketaux_symbol")
    rss_query = instr.get("rss_query") or f"{instr.get('symbol', '')} market news"

    if fh_symbol:
        items = fetch_finnhub(fh_symbol)
        if items:
            return items, "finnhub"

    if mx_symbol:
        items = fetch_marketaux(mx_symbol)
        if items:
            return items, "marketaux"

    rss_items = fetch_rss(rss_query)
    if rss_items:
        return rss_items, "rss"

    return [], "none"
