from __future__ import annotations

from markdownify import markdownify


def convert_html_to_markdown(html: str) -> str:
    """Convert HTML to Markdown using markdownify."""
    return markdownify(html, heading_style="ATX", bullets="-")
