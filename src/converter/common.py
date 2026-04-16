from __future__ import annotations

import re

from bs4 import BeautifulSoup

from src.models import ConfluencePage


def build_metadata_header(page: ConfluencePage) -> str:
    labels = ", ".join(page.labels) if page.labels else "—"
    lines = [
        f"# {page.title}",
        "",
        f"- **Space:** {page.space_key}",
        f"- **Labels:** {labels}",
        f"- **Last Modified:** {page.last_modified.strftime('%Y-%m-%d %H:%M')}",
        f"- **Version:** {page.version}",
    ]
    if page.url:
        lines.append(f"- **Source:** {page.url}")
    lines.append("")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def preprocess_html(html: str, base_url: str) -> str:
    """Clean up Confluence export_view HTML for conversion.

    export_view is already fully rendered (macros expanded), so we only need
    to absolutise relative links and wrap in a full HTML document.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Convert relative links to absolute
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        if isinstance(href, str) and href.startswith("/"):
            a_tag["href"] = base_url + href

    # Strip any remaining ac:* and ri:* tags (rare in export_view, but just in case)
    for tag in soup.find_all(re.compile(r"^(ac|ri):", re.IGNORECASE)):
        tag.unwrap()

    body_html = str(soup)
    return f"<html><body>{body_html}</body></html>"


def postprocess_markdown(md: str) -> str:
    """Clean up converted markdown."""
    # Collapse 3+ consecutive blank lines to 2
    md = re.sub(r"\n{3,}", "\n\n", md)
    # Strip trailing whitespace per line
    md = "\n".join(line.rstrip() for line in md.split("\n"))
    # Ensure single trailing newline
    return md.strip() + "\n"
