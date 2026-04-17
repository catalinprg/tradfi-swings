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
  - If the API call fails, prints the error to stderr and exits non-zero
    so the caller can report a non-fatal notification failure.
"""
import os
import sys

import requests

API_BASE = "https://api.telegram.org"
TIMEOUT = 10


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

    if r.status_code >= 400:
        print(f"telegram sendMessage failed {r.status_code}: {r.text}", file=sys.stderr)
        sys.exit(1)

    print("telegram notification sent")


if __name__ == "__main__":
    main()
