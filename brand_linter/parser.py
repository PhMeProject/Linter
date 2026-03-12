"""
Parser – reads a .docx file and returns one ParagraphData per paragraph.

Strategy
--------
python-docx represents a paragraph as a sequence of *runs*.  Formatting
properties (font, size, bold, italic, colour) are inherited from the
paragraph style when not explicitly set on a run.  We resolve each
property by walking:

    run-level override → paragraph-level override → style definition

The "dominant" value for a paragraph is taken from the first non-None
run value, or from the paragraph-level default, or from the style.  For
multi-run paragraphs with mixed formatting we capture what the first run
says (or the paragraph default) – consistent with how Word reports the
paragraph in the Styles pane.

Font sizes in python-docx are stored as ``Pt`` (EMU-based ``Length``)
objects; we convert to plain ``float`` points for storage.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

from docx import Document
from docx.oxml.ns import qn
from docx.shared import Pt

from .models import ParagraphData


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class DocumentParseError(Exception):
    """Raised when the document cannot be read or is not a valid .docx file."""


def parse_document(path: Union[str, Path]) -> list[ParagraphData]:
    """Open a .docx file and return one :class:`ParagraphData` per paragraph.

    Parameters
    ----------
    path:
        Path to a ``.docx`` file.

    Returns
    -------
    list of ParagraphData, in document order (0-indexed).
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Document not found: {path}")
    if path.suffix.lower() != ".docx":
        raise DocumentParseError(f"Only .docx files are supported, got: {path.suffix}")

    try:
        doc = Document(str(path))
    except Exception as exc:
        raise DocumentParseError(f"Could not open {path}: {exc}") from exc

    result: list[ParagraphData] = []
    for idx, para in enumerate(doc.paragraphs):
        result.append(_extract_paragraph(idx, para))
    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_paragraph(idx: int, para) -> ParagraphData:  # type: ignore[no-untyped-def]
    """Convert a python-docx Paragraph object into a ParagraphData."""
    text = para.text  # concatenated run text

    style_name: str = para.style.name if para.style else "Normal"

    font_name = _resolve_font_name(para)
    font_size = _resolve_font_size(para)
    bold = _resolve_bool_prop(para, "bold")
    italic = _resolve_bool_prop(para, "italic")
    color_hex = _resolve_color(para)

    return ParagraphData(
        index=idx,
        text=text,
        style_name=style_name,
        font_name=font_name,
        font_size=font_size,
        bold=bold,
        italic=italic,
        color_hex=color_hex,
    )


def _resolve_font_name(para) -> Optional[str]:
    """Return the effective font name for the paragraph.

    Precedence: first run with an explicit font → paragraph rPr font →
    style font → None.
    """
    # Check runs first
    for run in para.runs:
        if run.font.name is not None:
            return run.font.name

    # Paragraph-level rPr
    pPr = para._p.find(qn("w:pPr"))
    if pPr is not None:
        rPr = pPr.find(qn("w:rPr"))
        if rPr is not None:
            rFonts = rPr.find(qn("w:rFonts"))
            if rFonts is not None:
                name = (
                    rFonts.get(qn("w:ascii"))
                    or rFonts.get(qn("w:hAnsi"))
                    or rFonts.get(qn("w:cs"))
                )
                if name:
                    return name

    # Style-level
    style = para.style
    while style is not None:
        if style.font.name is not None:
            return style.font.name
        style = style.base_style

    return None


def _resolve_font_size(para) -> Optional[float]:
    """Return the effective font size in points.

    Precedence: first run with an explicit size → paragraph rPr size →
    style size → None.
    """
    for run in para.runs:
        if run.font.size is not None:
            return _to_pt(run.font.size)

    # Paragraph-level rPr
    pPr = para._p.find(qn("w:pPr"))
    if pPr is not None:
        rPr = pPr.find(qn("w:rPr"))
        if rPr is not None:
            sz = rPr.find(qn("w:sz"))
            if sz is not None:
                val = sz.get(qn("w:val"))
                if val:
                    return float(val) / 2  # half-points → points

    # Style-level
    style = para.style
    while style is not None:
        if style.font.size is not None:
            return _to_pt(style.font.size)
        style = style.base_style

    return None


def _resolve_bool_prop(para, prop: str) -> Optional[bool]:
    """Resolve 'bold' or 'italic' with the same precedence chain."""
    for run in para.runs:
        value = getattr(run.font, prop, None)
        if value is not None:
            return bool(value)

    # Style-level
    style = para.style
    while style is not None:
        value = getattr(style.font, prop, None)
        if value is not None:
            return bool(value)
        style = style.base_style

    return None


def _resolve_color(para) -> Optional[str]:
    """Return the RGB hex color (without '#') of the first explicit run color.

    Returns None when no explicit color is set (theme / auto colors are
    ignored because they cannot be compared reliably without a theme context).
    """
    for run in para.runs:
        color = run.font.color
        if color is not None and color.type is not None:
            rgb = color.rgb
            if rgb is not None:
                return str(rgb)  # e.g. "1F3864"

    return None


def _to_pt(length) -> float:
    """Convert a python-docx Length (EMUs) to plain float points.

    python-docx ``Length`` objects are ``int`` subclasses that store the value
    in EMUs but expose a ``.pt`` property.  We must check for ``.pt`` before
    falling through to the bare ``int`` branch.
    """
    try:
        return float(length.pt)
    except AttributeError:
        pass
    # Plain int/float (e.g. from tests that pass raw point values)
    return float(length)
