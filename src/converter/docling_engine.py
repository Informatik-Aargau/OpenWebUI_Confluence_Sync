from __future__ import annotations

from io import BytesIO

from docling.datamodel.backend_options import HTMLBackendOptions
from docling.datamodel.base_models import DocumentStream, InputFormat
from docling.document_converter import DocumentConverter, HTMLFormatOption

_converter: DocumentConverter | None = None


def _get_converter() -> DocumentConverter:
    global _converter
    if _converter is None:
        html_options = HTMLBackendOptions(infer_furniture=False)
        format_options = {
            InputFormat.HTML: HTMLFormatOption(backend_options=html_options),
        }
        _converter = DocumentConverter(format_options=format_options)
    return _converter


def convert_html_to_markdown(html: str) -> str:
    """Convert HTML to Markdown using docling with infer_furniture=False."""
    converter = _get_converter()
    source = DocumentStream(
        name="page.html",
        stream=BytesIO(html.encode("utf-8")),
    )
    result = converter.convert(source)
    return result.document.export_to_markdown()
