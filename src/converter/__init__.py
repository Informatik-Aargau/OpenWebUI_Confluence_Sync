from __future__ import annotations

import logging

from src.config import settings
from src.converter.common import build_metadata_header, postprocess_markdown, preprocess_html
from src.models import ConfluencePage

logger = logging.getLogger(__name__)


def convert_page_to_markdown(page: ConfluencePage, confluence_base_url: str) -> str:
    """Convert a Confluence page (export_view HTML) to Markdown with metadata header."""
    html = preprocess_html(page.body_storage, confluence_base_url)
    logger.debug("Preprocessed HTML for page %s: %d chars", page.id, len(html))

    engine = settings.converter_engine
    if engine == "markdownify":
        from src.converter.markdownify_engine import convert_html_to_markdown
    elif engine == "docling":
        from src.converter.docling_engine import convert_html_to_markdown
    else:
        raise ValueError(
            f"Unknown converter engine {engine!r}. "
            "Set CONVERTER_ENGINE to 'markdownify' or 'docling'."
        )

    body_md = convert_html_to_markdown(html)
    body_md = postprocess_markdown(body_md)
    logger.debug("Converted markdown for page %s: %d chars (engine=%s)", page.id, len(body_md), engine)

    header = build_metadata_header(page)
    return f"{header}\n{body_md}"
