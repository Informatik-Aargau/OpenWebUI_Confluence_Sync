from __future__ import annotations

import re

from markdownify import markdownify

from src.models import ConfluencePage


def convert_page_to_markdown(page: ConfluencePage, confluence_base_url: str) -> str:
    """Convert a Confluence page (storage format HTML) to Markdown with metadata header."""
    html = _preprocess_confluence_html(page.body_storage, confluence_base_url)
    body_md = markdownify(html, heading_style="ATX", strip=["img"])
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


def _preprocess_confluence_html(html: str, base_url: str) -> str:
    """Handle Confluence-specific macros before markdownify."""
    # Info / Note / Warning / Tip panels → blockquote
    html = re.sub(
        r'<ac:structured-macro\s+ac:name="(info|note|warning|tip)"[^>]*>'
        r".*?<ac:rich-text-body>(.*?)</ac:rich-text-body>"
        r".*?</ac:structured-macro>",
        r"> **\1:** \2",
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )

    # Code blocks
    html = re.sub(
        r'<ac:structured-macro\s+ac:name="code"[^>]*>'
        r".*?<ac:plain-text-body>\s*<!\[CDATA\[(.*?)\]\]>\s*</ac:plain-text-body>"
        r".*?</ac:structured-macro>",
        r"<pre><code>\1</code></pre>",
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )

    # No-format blocks
    html = re.sub(
        r'<ac:structured-macro\s+ac:name="noformat"[^>]*>'
        r".*?<ac:plain-text-body>\s*<!\[CDATA\[(.*?)\]\]>\s*</ac:plain-text-body>"
        r".*?</ac:structured-macro>",
        r"<pre>\1</pre>",
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )

    # Expand macros → just show body
    html = re.sub(
        r'<ac:structured-macro\s+ac:name="expand"[^>]*>'
        r".*?<ac:rich-text-body>(.*?)</ac:rich-text-body>"
        r".*?</ac:structured-macro>",
        r"\1",
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )

    # Strip remaining unknown structured macros — keep body if present
    html = re.sub(
        r"<ac:structured-macro[^>]*>"
        r".*?<ac:rich-text-body>(.*?)</ac:rich-text-body>"
        r".*?</ac:structured-macro>",
        r"\1",
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    # Strip macros without rich-text-body
    html = re.sub(
        r"<ac:structured-macro[^>]*>.*?</ac:structured-macro>",
        "",
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )

    # Convert relative links to absolute
    html = re.sub(
        r'href="(/[^"]*)"',
        rf'href="{base_url}\1"',
        html,
    )

    # Strip remaining ac:* and ri:* tags but keep their text content
    html = re.sub(r"</?ac:[^>]*>", "", html)
    html = re.sub(r"</?ri:[^>]*>", "", html)

    return html


def _postprocess_markdown(md: str) -> str:
    """Clean up converted markdown."""
    # Collapse 3+ consecutive blank lines to 2
    md = re.sub(r"\n{3,}", "\n\n", md)
    # Strip trailing whitespace per line
    md = "\n".join(line.rstrip() for line in md.split("\n"))
    # Ensure single trailing newline
    return md.strip() + "\n"
