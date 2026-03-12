"""
Typed dataclasses for every domain object in the Brand Linter engine.

Hierarchy
---------
Rules
  TextSubstitutionRule  – find a phrase and replace it (Language / Always)
  TextProhibitionRule   – flag a phrase that must never appear (Language / Never)
  StyleRule             – assert or prohibit a formatting property on matched
                          paragraphs (Branding / Formatting / Structure)

Paragraphs
  ParagraphData         – everything the parser extracts for one paragraph

Report
  Change                – a text edit that was applied automatically
  Violation             – a style problem that was flagged but not auto-fixed
  RulePass              – a rule that found nothing to do (audit trail)
  LintReport            – the final container returned by the executor
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class Section(str, Enum):
    """Top-level sections from the brand spec."""
    BRANDING_TYPOGRAPHY = "branding.typography"
    BRANDING_COLORS = "branding.colors"
    BRANDING_LOGOS = "branding.logos"
    FORMATTING = "formatting"
    LANGUAGE = "language"
    STRUCTURE = "structure"


class Bucket(str, Enum):
    """Rule buckets within each section."""
    ALWAYS = "always"
    NEVER = "never"
    # IF_THEN is reserved for future use
    IF_THEN = "if_then"


# ---------------------------------------------------------------------------
# Style match / require descriptors
# ---------------------------------------------------------------------------


@dataclass
class StyleMatch:
    """Criteria that identify which paragraphs a StyleRule applies to.

    All supplied fields must match simultaneously.  Omitted fields are
    treated as wildcards.
    """
    style_name: Optional[str] = None   # e.g. "Normal", "Heading 1"
    font_name: Optional[str] = None
    font_size: Optional[float] = None  # in points
    bold: Optional[bool] = None
    italic: Optional[bool] = None

    def matches(self, para: "ParagraphData") -> bool:
        if self.style_name is not None and para.style_name != self.style_name:
            return False
        if self.font_name is not None and para.font_name != self.font_name:
            return False
        if self.font_size is not None and para.font_size != self.font_size:
            return False
        if self.bold is not None and para.bold != self.bold:
            return False
        if self.italic is not None and para.italic != self.italic:
            return False
        return True


@dataclass
class StyleRequire:
    """The formatting properties that must (or must not) hold on a paragraph."""
    font_name: Optional[str] = None
    font_size: Optional[float] = None
    bold: Optional[bool] = None
    italic: Optional[bool] = None
    color_hex: Optional[str] = None   # e.g. "1F3864"

    def violations_against(self, para: "ParagraphData") -> list[str]:
        """Return a list of human-readable failure messages."""
        msgs: list[str] = []
        if self.font_name is not None and para.font_name != self.font_name:
            msgs.append(
                f"font should be '{self.font_name}', found '{para.font_name}'"
            )
        if self.font_size is not None and para.font_size != self.font_size:
            msgs.append(
                f"font size should be {self.font_size}pt, found {para.font_size}pt"
            )
        if self.bold is not None and para.bold != self.bold:
            expected = "bold" if self.bold else "not bold"
            msgs.append(f"text should be {expected}")
        if self.italic is not None and para.italic != self.italic:
            expected = "italic" if self.italic else "not italic"
            msgs.append(f"text should be {expected}")
        if self.color_hex is not None and para.color_hex != self.color_hex:
            msgs.append(
                f"color should be #{self.color_hex}, found "
                f"{'#' + para.color_hex if para.color_hex else 'none'}"
            )
        return msgs


# ---------------------------------------------------------------------------
# Rule types
# ---------------------------------------------------------------------------


@dataclass
class TextSubstitutionRule:
    """Replace every occurrence of *find* with *replace* in paragraph text.

    Belongs to: Language / Always
    """
    id: str
    description: str
    section: Section
    bucket: Bucket
    find: str
    replace: str
    case_sensitive: bool = False
    whole_word: bool = False

    def _pattern(self) -> re.Pattern[str]:
        escaped = re.escape(self.find)
        if self.whole_word:
            escaped = rf"\b{escaped}\b"
        flags = 0 if self.case_sensitive else re.IGNORECASE
        return re.compile(escaped, flags)

    def apply(self, text: str) -> tuple[str, int]:
        """Return (corrected_text, match_count)."""
        pattern = self._pattern()
        result, n = pattern.subn(self.replace, text)
        return result, n


@dataclass
class TextProhibitionRule:
    """Flag every occurrence of *find*; no automatic correction is made.

    Belongs to: Language / Never
    """
    id: str
    description: str
    section: Section
    bucket: Bucket
    find: str
    case_sensitive: bool = False
    whole_word: bool = False

    def _pattern(self) -> re.Pattern[str]:
        escaped = re.escape(self.find)
        if self.whole_word:
            escaped = rf"\b{escaped}\b"
        flags = 0 if self.case_sensitive else re.IGNORECASE
        return re.compile(escaped, flags)

    def find_occurrences(self, text: str) -> list[re.Match[str]]:
        return list(self._pattern().finditer(text))


@dataclass
class StyleRule:
    """Assert or prohibit formatting properties on paragraphs that match a selector.

    Bucket semantics:
      ALWAYS – every matched paragraph *must* satisfy *require*.
      NEVER  – every matched paragraph *must not* match *require*
               (any single field listed in require that is present violates).
    """
    id: str
    description: str
    section: Section
    bucket: Bucket
    match: StyleMatch
    require: StyleRequire


# ---------------------------------------------------------------------------
# Paragraph data
# ---------------------------------------------------------------------------


@dataclass
class ParagraphData:
    """Everything the parser extracts for a single paragraph."""
    index: int               # 0-based position in the document
    text: str
    style_name: str          # Word paragraph style, e.g. "Normal", "Heading 1"
    font_name: Optional[str] = None
    font_size: Optional[float] = None
    bold: Optional[bool] = None
    italic: Optional[bool] = None
    color_hex: Optional[str] = None  # RGB hex string without '#', e.g. "1F3864"

    @property
    def ref(self) -> str:
        """Human-readable paragraph reference for reports."""
        return f"para[{self.index}]"


# ---------------------------------------------------------------------------
# Report items
# ---------------------------------------------------------------------------


@dataclass
class Change:
    """A text edit applied automatically to a paragraph."""
    paragraph_index: int
    rule_id: str
    rule_description: str
    original_text: str
    corrected_text: str
    occurrences: int = 1

    @property
    def ref(self) -> str:
        return f"para[{self.paragraph_index}]"


@dataclass
class Violation:
    """A problem that was detected but not automatically corrected."""
    paragraph_index: int
    rule_id: str
    rule_description: str
    detail: str             # human-readable explanation of the specific failure

    @property
    def ref(self) -> str:
        return f"para[{self.paragraph_index}]"


@dataclass
class RulePass:
    """A rule that was evaluated and found no problems (audit trail)."""
    rule_id: str
    rule_description: str
    paragraphs_checked: int


# ---------------------------------------------------------------------------
# Report container
# ---------------------------------------------------------------------------


@dataclass
class LintReport:
    """The complete output of one linting run."""
    template_name: str
    source_file: str
    changes: list[Change] = field(default_factory=list)
    violations: list[Violation] = field(default_factory=list)
    passes: list[RulePass] = field(default_factory=list)

    # The corrected paragraphs in order (text only; write-back uses docx).
    # Each entry is either the corrected text (if changed) or the original.
    corrected_paragraphs: list[str] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return not self.changes and not self.violations

    def summary(self) -> dict[str, Any]:
        return {
            "template": self.template_name,
            "source": self.source_file,
            "changes": len(self.changes),
            "violations": len(self.violations),
            "passes": len(self.passes),
            "clean": self.is_clean,
        }
