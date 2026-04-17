"""Extract main article text from a URL.

Primary path: `trafilatura` — handles most non-JS news sites (Reuters,
CNBC, FT preview, FXStreet, etc.) without rendering.

Fallback path: Firecrawl — handles JS rendering, paywall interstitials,
and sites where trafilatura returns nothing. Only used when trafilatura
fails AND a `FIRECRAWL_API_KEY` is set AND the per-run budget is not
exhausted. The budget (FIRECRAWL_BUDGET_PER_RUN) exists to avoid
blowing the free-tier quota on a bad-extraction day.

Extracted content is truncated to MAX_CHARS so 45 articles per run
don't bloat macro_context.json. 1500 chars typically covers the lead
+ first two body paragraphs — enough for the agent to paraphrase, not
enough to quote verbatim (good — avoids copyright drift).

Failures (paywalls we can't bypass, 403s, timeouts) return None.
Callers treat that as "content unavailable, fall back to headline
+ summary".
"""
import os
from typing import Optional

import requests

try:
    import trafilatura
    _TRAFILATURA_AVAILABLE = True
except ImportError:
    _TRAFILATURA_AVAILABLE = False

FIRECRAWL_API_KEY = os.environ.get("FIRECRAWL_API_KEY", "")
FIRECRAWL_ENDPOINT = "https://api.firecrawl.dev/v1/scrape"
FIRECRAWL_BUDGET_PER_RUN = int(os.environ.get("FIRECRAWL_BUDGET_PER_RUN", "10"))
FIRECRAWL_TIMEOUT = 30   # JS rendering is slow

# Hard paywalls where Firecrawl's free tier reliably fails to unwrap the
# article. Skip them before burning a call. Editable via
# FIRECRAWL_PAYWALL_BLOCKLIST env var (comma-separated domains).
DEFAULT_FIRECRAWL_PAYWALL_BLOCKLIST = (
    "bloomberg.com",
    "ft.com",
    "wsj.com",
    "nytimes.com",
    "barrons.com",
    "economist.com",
    "theinformation.com",
    "seekingalpha.com",
)
_blocklist_env = os.environ.get("FIRECRAWL_PAYWALL_BLOCKLIST", "").strip()
FIRECRAWL_PAYWALL_BLOCKLIST: tuple[str, ...] = (
    tuple(d.strip().lower() for d in _blocklist_env.split(",") if d.strip())
    if _blocklist_env
    else DEFAULT_FIRECRAWL_PAYWALL_BLOCKLIST
)
# After this many consecutive Firecrawl failures on the same domain within
# a single run, skip further Firecrawl attempts for that domain — the run
# budget is limited and one bad publisher shouldn't drain it. trafilatura
# still gets tried upstream.
FIRECRAWL_DOMAIN_FAILURE_THRESHOLD = 2

# Module-level counter decremented on every Firecrawl call. Reset per run
# by the caller (emit_macro initializes macro_context then calls extract
# in a threadpool; emit_macro invocation = new run = counter resets via
# reset_firecrawl_budget).
_firecrawl_remaining = FIRECRAWL_BUDGET_PER_RUN
_firecrawl_domain_failures: dict[str, int] = {}


def reset_firecrawl_budget():
    """Reset the per-run Firecrawl call budget + per-domain failure counter.
    Called by emit_macro at the start of each run."""
    global _firecrawl_remaining
    _firecrawl_remaining = FIRECRAWL_BUDGET_PER_RUN
    _firecrawl_domain_failures.clear()


def _domain_of(url: str) -> str:
    """Bare host of `url` (no subdomain trim — subdomain-specific paywalls
    get their own count). Lowercased. Empty string on parse failure."""
    try:
        from urllib.parse import urlparse
        return (urlparse(url).netloc or "").lower()
    except Exception:
        return ""


def _is_paywall_domain(url: str) -> bool:
    host = _domain_of(url)
    if not host:
        return False
    return any(host == d or host.endswith("." + d) for d in FIRECRAWL_PAYWALL_BLOCKLIST)

TIMEOUT = 10
MAX_CHARS = 1500
MIN_MEANINGFUL_CHARS = 200
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)

# Hosts whose URLs are redirect wrappers to a consent/captcha wall. Fetching
# these produces navigation/consent HTML that looks like content but isn't.
# Skip extraction — the agent falls back to headline + summary.
BLOCKED_HOSTS = (
    "news.google.com",
    "consent.google.com",
    "consent.youtube.com",
)

# Tokens that indicate the response is a consent/nav page rather than an
# article body. Single-word language codes or UI chrome with no prose.
CONSENT_SHIBBOLETHS = (
    "EnglishUnited States",
    "EspañolLatinoamérica",
    "Before you continue to",
    "Accept all cookies",
    "Sign in to continue",
)


def _is_blocked_host(url: str) -> bool:
    """True when the URL's host is a known redirect/consent wrapper whose
    HTML won't yield real article content."""
    return any(host in url for host in BLOCKED_HOSTS)


def _looks_like_consent_page(text: str) -> bool:
    """True when the extracted text is likely a consent / nav / cookie
    page rather than an article body. These pages typically have tons of
    short lines (language picker, menu items) and low prose density."""
    if any(token in text for token in CONSENT_SHIBBOLETHS):
        return True
    # Heuristic: if avg line length < 15 chars across 20+ lines, it's a menu.
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) >= 20:
        avg = sum(len(ln) for ln in lines) / len(lines)
        if avg < 15:
            return True
    return False


def _truncate(text: str) -> str:
    text = text.strip()
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS].rsplit(" ", 1)[0] + "…"
    return text


def _extract_with_trafilatura(url: str) -> Optional[str]:
    """Primary-path extraction. Returns cleaned text or None on failure."""
    try:
        r = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT,
            allow_redirects=True,
        )
        if _is_blocked_host(r.url):
            return None
        if r.status_code >= 400:
            return None
        text = trafilatura.extract(
            r.text,
            include_comments=False,
            include_tables=False,
            deduplicate=True,
        )
        if not text:
            return None
        text = text.strip()
        if len(text) < MIN_MEANINGFUL_CHARS or _looks_like_consent_page(text):
            return None
        return _truncate(text)
    except Exception:
        return None


def _extract_with_firecrawl(url: str) -> Optional[str]:
    """Fallback via Firecrawl /v1/scrape. Handles JS rendering and
    paywall interstitials that trafilatura can't bypass. Returns
    markdown-formatted content or None.

    Budget-aware: skips the call entirely when (a) the domain is a
    known hard paywall, (b) the per-run call budget is exhausted, or
    (c) we've already failed this domain too many times in this run."""
    global _firecrawl_remaining
    if not FIRECRAWL_API_KEY or _firecrawl_remaining <= 0:
        return None
    if _is_paywall_domain(url):
        return None
    host = _domain_of(url)
    if host and _firecrawl_domain_failures.get(host, 0) >= FIRECRAWL_DOMAIN_FAILURE_THRESHOLD:
        return None
    try:
        r = requests.post(
            FIRECRAWL_ENDPOINT,
            headers={
                "Authorization": f"Bearer {FIRECRAWL_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "url": url,
                "formats": ["markdown"],
                "onlyMainContent": True,
                "waitFor": 2000,
            },
            timeout=FIRECRAWL_TIMEOUT,
        )
        _firecrawl_remaining -= 1
        if r.status_code >= 400:
            if host:
                _firecrawl_domain_failures[host] = _firecrawl_domain_failures.get(host, 0) + 1
            return None
        data = r.json()
        md = ((data.get("data") or {}).get("markdown") or "").strip()
        if len(md) < MIN_MEANINGFUL_CHARS or _looks_like_consent_page(md):
            if host:
                _firecrawl_domain_failures[host] = _firecrawl_domain_failures.get(host, 0) + 1
            return None
        return _truncate(md)
    except Exception:
        if host:
            _firecrawl_domain_failures[host] = _firecrawl_domain_failures.get(host, 0) + 1
        return None


def extract(url: str) -> Optional[str]:
    """Fetch `url` and return cleaned main-article text, truncated to
    MAX_CHARS. Tries trafilatura first, then Firecrawl as a fallback if
    a FIRECRAWL_API_KEY is set and the per-run budget is not exhausted.
    Returns None when both paths fail or when the URL is a known
    redirect/consent wrapper."""
    if not _TRAFILATURA_AVAILABLE or not url:
        return None
    if _is_blocked_host(url):
        # Google News RSS wrappers etc. — fetching lands on a consent
        # page; even Firecrawl usually can't unwrap those. Skip upfront.
        return None

    text = _extract_with_trafilatura(url)
    if text:
        return text

    return _extract_with_firecrawl(url)
