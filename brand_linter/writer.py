"""
Writer – applies accepted text substitution rules back to a .docx file.

Strategy
--------
Substitutions are applied run-by-run within each paragraph.  This
preserves per-run formatting (bold, italic, colour) for the common case
where a matched phrase lies entirely within a single run.  Phrases that
span multiple runs will not be matched – a known V1 limitation.

The corrected document is written to an in-memory BytesIO buffer so the
caller can stream it directly to the HTTP response without touching disk.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Union

from docx import Document

from .models import TextSubstitutionRule

AnyRule = object  # Union type kept loose to avoid circular imports at runtime


def build_corrected_document(
    source_path: Union[str, Path],
    rules: list,
    accepted_rule_ids: set[str],
) -> BytesIO:
    """Return a BytesIO buffer containing the corrected .docx.

    Parameters
    ----------
    source_path:
        Path to the original uploaded .docx file.
    rules:
        Full rule list from :func:`brand_linter.loader.load_template`.
        Only :class:`TextSubstitutionRule` entries whose id is in
        *accepted_rule_ids* are applied; all others are skipped.
    accepted_rule_ids:
        Set of rule IDs the user accepted in the review UI.

    Returns
    -------
    BytesIO positioned at offset 0, ready for ``send_file``.
    """
    accepted: list[TextSubstitutionRule] = [
        r
        for r in rules
        if isinstance(r, TextSubstitutionRule) and r.id in accepted_rule_ids
    ]

    doc = Document(str(source_path))

    for para in doc.paragraphs:
        for rule in accepted:
            for run in para.runs:
                corrected, _ = rule.apply(run.text)
                if corrected != run.text:
                    run.text = corrected

    buf = BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf
