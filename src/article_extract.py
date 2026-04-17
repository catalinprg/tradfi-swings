"""Extract main article text from a URL.

Uses `trafilatura` — robust against ads, nav, paywalls headers, and
handles most major news sites (Reuters, CNBC, Bloomberg snippets, FT
preview paragraphs, FXStreet, etc.) without JS rendering.

Extracted content is truncated to MAX_CHARS so 45 articles per run
don't bloat macro_context.json. 1500 chars typically covers the lead
+ first two body paragraphs, which is where the "what happened"
information lives. The agent gets enough to paraphrase, not enough to
quote verbatim (good — avoids copyright drift).

Failures (paywalls, 403s, timeouts) return None. Callers treat that
as "content unavailable, fall back to headline + summary".
"""
from typing import Optional

import requests

try:
    import trafilatura
    _TRAFILATURA_AVAILABLE = True
except ImportError:
    _TRAFILATURA_AVAILABLE = False

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


def extract(url: str) -> Optional[str]:
    """Fetch `url` and return the cleaned main-article text, truncated
    to MAX_CHARS. Returns None on any failure or when the response is
    clearly a consent/redirect wrapper rather than article content."""
    if not _TRAFILATURA_AVAILABLE or not url:
        return None
    if _is_blocked_host(url):
        # Google News RSS wrappers, etc. — fetching lands on a consent
        # page; the agent gets nothing useful. Skip upfront.
        return None
    try:
        r = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT,
            allow_redirects=True,
        )
        # If we got redirected to a consent wrapper, treat as failure.
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
        if len(text) < MIN_MEANINGFUL_CHARS:
            return None
        if _looks_like_consent_page(text):
            return None
        if len(text) > MAX_CHARS:
            text = text[:MAX_CHARS].rsplit(" ", 1)[0] + "…"
        return text
    except Exception:
        return None
