"""Send a Telegram notification via the Bot API.

Usage: python3 notify_telegram.py "<message text>"

Requires env vars:
  TELEGRAM_BOT_TOKEN  Bot token from @BotFather
  TELEGRAM_CHAT_ID    Target chat ID (numeric). DM your bot once, then
                      check https://api.telegram.org/bot<TOKEN>/getUpdates
                      or message @userinfobot to find it.

Behavior:
  - If either env var is unset, exits 0 silently (no-op). This keeps the
    pipeline green on environments that haven't configured Telegram yet.
  - Retries once after 10s on transient failures (connection error,
    timeout, HTTP 5xx, HTTP 429). Permanent errors (HTTP 4xx other than
    429) fail immediately — retry won't help if the token or chat_id
    is wrong.
  - If the call still fails after retry, prints the error to stderr and
    exits non-zero so the caller can report a non-fatal notification
    failure.
"""
import os
import sys
import time

import requests

API_BASE = "https://api.telegram.org"
TIMEOUT = 10
RETRY_DELAY_SEC = 10


def _send_once(token: str, chat_id: str, text: str) -> tuple[bool, str]:
    """One HTTP attempt. Returns (success, error_description).
    error_description is empty on success; "transient" for retry-worthy
    failures; or a human-readable error on permanent failures."""
    try:
        r = requests.post(
            f"{API_BASE}/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": False,
            },
            timeout=TIMEOUT,
        )
    except requests.RequestException as e:
        return False, f"transient:request_error:{e}"

    if r.status_code < 400:
        return True, ""
    # 429 (rate limit) and 5xx are transient on Telegram's side
    if r.status_code == 429 or r.status_code >= 500:
        return False, f"transient:HTTP {r.status_code} {r.text}"
    # 4xx other than 429 — permanent (bad token, bad chat_id, malformed msg)
    return False, f"permanent:HTTP {r.status_code} {r.text}"


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

    if not token or not chat_id:
        print("telegram not configured (TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID unset); skipping")
        sys.exit(0)

    if len(sys.argv) != 2:
        print("usage: notify_telegram.py \"<message text>\"", file=sys.stderr)
        sys.exit(2)

    text = sys.argv[1]
    if not text.strip():
        print("error: empty message", file=sys.stderr)
        sys.exit(2)

    ok, err = _send_once(token, chat_id, text)
    if ok:
        print("telegram notification sent")
        return

    if err.startswith("permanent:"):
        print(f"telegram sendMessage failed ({err.split(':', 1)[1]})", file=sys.stderr)
        sys.exit(1)

    # Transient — wait and retry once
    print(
        f"telegram transient error, retrying in {RETRY_DELAY_SEC}s "
        f"({err.split(':', 1)[1]})",
        file=sys.stderr,
    )
    time.sleep(RETRY_DELAY_SEC)
    ok, err = _send_once(token, chat_id, text)
    if ok:
        print("telegram notification sent (on retry)")
        return

    # Final fail
    print(f"telegram sendMessage failed after retry ({err.split(':', 1)[1]})", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
