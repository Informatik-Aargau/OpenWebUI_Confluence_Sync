from __future__ import annotations

from datetime import datetime

import pytest

from src.models import ConfluencePage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_page(body: str) -> ConfluencePage:
    return ConfluencePage(
        id="123",
        title="Test Page",
        space_key="TST",
        version=1,
        last_modified=datetime(2025, 1, 1, 12, 0),
        body_storage=body,
        labels=["label1"],
        url="https://confluence.example.com/pages/123",
    )


BASE_URL = "https://confluence.example.com"


# ---------------------------------------------------------------------------
# Tests for common functions
# ---------------------------------------------------------------------------

class TestPreprocessHtml:
    def test_relative_links_absolutised(self):
        from src.converter.common import preprocess_html

        html = '<a href="/wiki/page">link</a>'
        result = preprocess_html(html, BASE_URL)
        assert f"{BASE_URL}/wiki/page" in result

    def test_ac_ri_tags_unwrapped(self):
        from src.converter.common import preprocess_html

        html = "<ac:structured-macro>content</ac:structured-macro>"
        result = preprocess_html(html, BASE_URL)
        assert "ac:structured-macro" not in result
        assert "content" in result

    def test_wraps_in_html_body(self):
        from src.converter.common import preprocess_html

        result = preprocess_html("<p>hello</p>", BASE_URL)
        assert result.startswith("<html><body>")
        assert result.endswith("</body></html>")


class TestPostprocessMarkdown:
    def test_collapses_blank_lines(self):
        from src.converter.common import postprocess_markdown

        md = "a\n\n\n\n\nb"
        result = postprocess_markdown(md)
        assert "\n\n\n" not in result
        assert "a\n\nb" in result

    def test_strips_trailing_whitespace(self):
        from src.converter.common import postprocess_markdown

        result = postprocess_markdown("hello   \nworld  ")
        assert "hello\nworld\n" == result

    def test_single_trailing_newline(self):
        from src.converter.common import postprocess_markdown

        result = postprocess_markdown("text\n\n\n")
        assert result.endswith("\n")
        assert not result.endswith("\n\n")


class TestBuildMetadataHeader:
    def test_includes_title_and_fields(self):
        from src.converter.common import build_metadata_header

        page = _make_page("<p>body</p>")
        header = build_metadata_header(page)
        assert "# Test Page" in header
        assert "**Space:** TST" in header
        assert "**Labels:** label1" in header
        assert "**Source:** https://confluence.example.com/pages/123" in header
        assert "---" in header


# ---------------------------------------------------------------------------
# Tests for markdownify engine
# ---------------------------------------------------------------------------

class TestMarkdownifyEngine:
    def test_basic_paragraph(self):
        from src.converter.markdownify_engine import convert_html_to_markdown

        result = convert_html_to_markdown("<p>Hello world</p>")
        assert "Hello world" in result

    def test_text_before_heading_preserved(self):
        from src.converter.markdownify_engine import convert_html_to_markdown

        html = "<p>Intro text before heading</p><h2>Title</h2><p>Body text</p>"
        result = convert_html_to_markdown(html)
        assert "Intro text before heading" in result
        assert "Title" in result
        assert "Body text" in result

    def test_text_without_headings_preserved(self):
        from src.converter.markdownify_engine import convert_html_to_markdown

        html = "<p>First paragraph</p><p>Second paragraph</p>"
        result = convert_html_to_markdown(html)
        assert "First paragraph" in result
        assert "Second paragraph" in result

    def test_list_preserved(self):
        from src.converter.markdownify_engine import convert_html_to_markdown

        html = "<ul><li>Item A</li><li>Item B</li></ul>"
        result = convert_html_to_markdown(html)
        assert "Item A" in result
        assert "Item B" in result

    def test_table_preserved(self):
        from src.converter.markdownify_engine import convert_html_to_markdown

        html = "<table><tr><th>Col1</th></tr><tr><td>Val1</td></tr></table>"
        result = convert_html_to_markdown(html)
        assert "Col1" in result
        assert "Val1" in result

    def test_link_preserved(self):
        from src.converter.markdownify_engine import convert_html_to_markdown

        html = '<a href="https://example.com">Click here</a>'
        result = convert_html_to_markdown(html)
        assert "Click here" in result
        assert "https://example.com" in result

    def test_empty_body(self):
        from src.converter.markdownify_engine import convert_html_to_markdown

        result = convert_html_to_markdown("")
        assert result.strip() == ""


# ---------------------------------------------------------------------------
# Tests for docling engine
# ---------------------------------------------------------------------------

@pytest.mark.slow
class TestDoclingEngine:
    def test_basic_paragraph(self):
        from src.converter.docling_engine import convert_html_to_markdown

        html = "<html><body><p>Hello world</p></body></html>"
        result = convert_html_to_markdown(html)
        assert "Hello world" in result

    def test_text_before_heading_preserved(self):
        from src.converter.docling_engine import convert_html_to_markdown

        html = "<html><body><p>Intro text before heading</p><h2>Title</h2><p>Body text</p></body></html>"
        result = convert_html_to_markdown(html)
        assert "Intro text before heading" in result
        assert "Title" in result
        assert "Body text" in result

    def test_text_without_headings_preserved(self):
        from src.converter.docling_engine import convert_html_to_markdown

        html = "<html><body><p>First paragraph</p><p>Second paragraph</p></body></html>"
        result = convert_html_to_markdown(html)
        assert "First paragraph" in result
        assert "Second paragraph" in result

    def test_empty_body(self):
        from src.converter.docling_engine import convert_html_to_markdown

        html = "<html><body></body></html>"
        result = convert_html_to_markdown(html)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Tests for dispatch (__init__.py)
# ---------------------------------------------------------------------------

class TestConvertPageToMarkdown:
    def test_markdownify_dispatch(self, monkeypatch):
        monkeypatch.setattr("src.config.settings.converter_engine", "markdownify")
        from src.converter import convert_page_to_markdown

        page = _make_page("<p>Hello from markdownify</p>")
        result = convert_page_to_markdown(page, BASE_URL)
        assert "# Test Page" in result
        assert "Hello from markdownify" in result

    def test_invalid_engine_raises(self, monkeypatch):
        monkeypatch.setattr("src.config.settings.converter_engine", "bogus")
        from src.converter import convert_page_to_markdown

        page = _make_page("<p>text</p>")
        with pytest.raises(ValueError, match="Unknown converter engine"):
            convert_page_to_markdown(page, BASE_URL)
