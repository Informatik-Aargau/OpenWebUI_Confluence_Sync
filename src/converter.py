from __future__ import annotations

import logging
import re
from io import BytesIO

from bs4 import BeautifulSoup
from docling.datamodel.base_models import DocumentStream
from docling.document_converter import DocumentConverter

from src.models import ConfluencePage

logger = logging.getLogger(__name__)

_converter = DocumentConverter()


def convert_page_to_markdown(page: ConfluencePage, confluence_base_url: str) -> str:
    """Convert a Confluence page (export_view HTML) to Markdown with metadata header."""
    html = _preprocess_html(page.body_storage, confluence_base_url)

    source = DocumentStream(
        name=f"{page.id}.html",
        stream=BytesIO(html.encode("utf-8")),
    )
    result = _converter.convert(source)
    body_md = result.document.export_to_markdown()
    body_md = _postprocess_markdown(body_md)

    header = _build_metadata_header(page)
    return f"{header}\n{body_md}"


def _build_metadata_header(page: ConfluencePage) -> str:
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


def _preprocess_html(html: str, base_url: str) -> str:
    """Clean up Confluence export_view HTML for docling.

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


def _postprocess_markdown(md: str) -> str:
    """Clean up converted markdown."""
    # Collapse 3+ consecutive blank lines to 2
    md = re.sub(r"\n{3,}", "\n\n", md)
    # Strip trailing whitespace per line
    md = "\n".join(line.rstrip() for line in md.split("\n"))
    # Ensure single trailing newline
    return md.strip() + "\n"
