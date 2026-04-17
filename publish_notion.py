"""Publish a markdown briefing to Notion as a child page under the
instrument's dedicated parent page.

Usage: python3 publish_notion.py <path_to_briefing.md> <INSTRUMENT_SLUG> <TIMESTAMP>

Looks up the instrument's `notion_parent` from config/watchlist.yaml and
creates the briefing as a child page under it. The parent page for each
asset lives under the top-level TradFI page; this script never touches
that top-level page directly.

`TIMESTAMP` can be either the compact `YYYYMMDD_HHMMSS` format emitted by
the skill or any free-form string — it's passed through to the Notion
title after a best-effort reformat to `YYYY-MM-DD HH:MM UTC`.

Requires env var: NOTION_TOKEN (Notion Internal Integration Token). Each
per-asset parent page must be shared with the integration.

Exits 0 on success, prints the page URL on the last stdout line.
Exits non-zero on failure, prints error to stderr.
"""
import os
import re
import sys
from pathlib import Path

import requests
import yaml

NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")
NOTION_VERSION = "2022-06-28"
API_BASE = "https://api.notion.com/v1"
BATCH = 100

COMPACT_TS_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})$")


def _load_instrument(slug: str) -> dict:
    root = Path(__file__).resolve().parent
    with open(root / "config" / "watchlist.yaml") as f:
        watchlist = yaml.safe_load(f)
    instr = watchlist["instruments"].get(slug)
    if not instr:
        raise SystemExit(f"unknown instrument slug '{slug}'")
    if not instr.get("notion_parent"):
        raise SystemExit(f"instrument '{slug}' has no notion_parent configured")
    return instr


def _format_title_timestamp(ts: str) -> str:
    """Reformat `YYYYMMDD_HHMMSS` → `YYYY-MM-DD HH:MM UTC`. Pass through
    any other format unchanged."""
    m = COMPACT_TS_RE.match(ts.strip())
    if not m:
        return ts
    y, mo, d, h, mi, _ = m.groups()
    return f"{y}-{mo}-{d} {h}:{mi} UTC"

INLINE_PATTERN = re.compile(
    r"(\*\*([^*]+)\*\*)"              # **bold**
    r"|(\*([^*]+)\*)"                 # *italic*
    r"|(`([^`]+)`)"                   # `code`
    r"|(\[([^\]]+)\]\(([^)]+)\))"     # [label](url)
)


def inline_rich_text(text):
    """Convert inline markdown (bold/italic/code/links) to Notion rich_text array."""
    segments = []
    pos = 0
    for m in INLINE_PATTERN.finditer(text):
        if m.start() > pos:
            segments.append({"type": "text", "text": {"content": text[pos:m.start()]}})
        if m.group(1):
            segments.append({"type": "text", "text": {"content": m.group(2)}, "annotations": {"bold": True}})
        elif m.group(3):
            segments.append({"type": "text", "text": {"content": m.group(4)}, "annotations": {"italic": True}})
        elif m.group(5):
            segments.append({"type": "text", "text": {"content": m.group(6)}, "annotations": {"code": True}})
        elif m.group(7):
            segments.append({
                "type": "text",
                "text": {"content": m.group(8), "link": {"url": m.group(9)}},
            })
        pos = m.end()
    if pos < len(text):
        segments.append({"type": "text", "text": {"content": text[pos:]}})
    if not segments:
        segments.append({"type": "text", "text": {"content": text}})
    return segments


def md_to_blocks(markdown):
    """Convert markdown string to an array of Notion block objects."""
    blocks = []
    lines = markdown.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        # Fenced code block
        if stripped.startswith("```"):
            lang = stripped[3:].strip() or "plain text"
            code_lines = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            blocks.append({
                "type": "code",
                "code": {
                    "rich_text": [{"type": "text", "text": {"content": "\n".join(code_lines)}}],
                    "language": lang,
                },
            })
            i += 1
            continue

        # Headings
        if stripped.startswith("### "):
            blocks.append({
                "type": "heading_3",
                "heading_3": {"rich_text": inline_rich_text(stripped[4:])},
            })
            i += 1
            continue
        if stripped.startswith("## "):
            blocks.append({
                "type": "heading_2",
                "heading_2": {"rich_text": inline_rich_text(stripped[3:])},
            })
            i += 1
            continue
        if stripped.startswith("# "):
            blocks.append({
                "type": "heading_1",
                "heading_1": {"rich_text": inline_rich_text(stripped[2:])},
            })
            i += 1
            continue

        # Divider
        if stripped == "---":
            blocks.append({"type": "divider", "divider": {}})
            i += 1
            continue

        # Bulleted list
        if stripped.startswith("- ") or stripped.startswith("* "):
            blocks.append({
                "type": "bulleted_list_item",
                "bulleted_list_item": {"rich_text": inline_rich_text(stripped[2:])},
            })
            i += 1
            continue

        # Numbered list
        if re.match(r"^\d+\.\s", stripped):
            content = re.sub(r"^\d+\.\s", "", stripped)
            blocks.append({
                "type": "numbered_list_item",
                "numbered_list_item": {"rich_text": inline_rich_text(content)},
            })
            i += 1
            continue

        # Paragraph (collect continuation lines until blank line)
        para_lines = [stripped]
        i += 1
        while i < len(lines) and lines[i].strip() and not _is_block_start(lines[i].strip()):
            para_lines.append(lines[i].strip())
            i += 1
        blocks.append({
            "type": "paragraph",
            "paragraph": {"rich_text": inline_rich_text(" ".join(para_lines))},
        })

    return blocks


def _is_block_start(stripped):
    if stripped.startswith(("# ", "## ", "### ", "- ", "* ", "```")):
        return True
    if stripped == "---":
        return True
    if re.match(r"^\d+\.\s", stripped):
        return True
    return False


def _headers():
    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def create_page(title, children, parent_page_id):
    payload = {
        "parent": {"page_id": parent_page_id},
        "properties": {
            "title": {"title": [{"type": "text", "text": {"content": title}}]}
        },
        "children": children,
    }
    r = requests.post(f"{API_BASE}/pages", headers=_headers(), json=payload, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"create_page failed {r.status_code}: {r.text}")
    data = r.json()
    return data["id"], data["url"]


def append_children(page_id, children):
    r = requests.patch(
        f"{API_BASE}/blocks/{page_id}/children",
        headers=_headers(),
        json={"children": children},
        timeout=30,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"append_children failed {r.status_code}: {r.text}")


def main():
    if not NOTION_TOKEN:
        print("error: NOTION_TOKEN env var not set", file=sys.stderr)
        sys.exit(1)
    if len(sys.argv) != 4:
        print(
            "usage: publish_notion.py <briefing.md> <INSTRUMENT_SLUG> <TIMESTAMP>",
            file=sys.stderr,
        )
        sys.exit(2)

    md_path, slug, timestamp = sys.argv[1], sys.argv[2], sys.argv[3]
    instr = _load_instrument(slug)
    parent_page_id = instr["notion_parent"]
    # Parent page already identifies the asset (e.g. "EUR/USD"), so the child
    # title is just the formatted timestamp.
    title = _format_title_timestamp(timestamp)

    with open(md_path, "r", encoding="utf-8") as f:
        markdown = f.read()

    blocks = md_to_blocks(markdown)
    if not blocks:
        print("error: briefing is empty, no blocks to publish", file=sys.stderr)
        sys.exit(3)

    page_id, page_url = create_page(title, blocks[:BATCH], parent_page_id)
    for i in range(BATCH, len(blocks), BATCH):
        append_children(page_id, blocks[i:i + BATCH])

    print(page_url)


if __name__ == "__main__":
    main()
