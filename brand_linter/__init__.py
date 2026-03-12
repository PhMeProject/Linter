"""
brand_linter – document compliance engine.

Quick start
-----------
    from brand_linter import load_template, parse_document, run

    template_name, rules = load_template("templates/newsletter_external.json")
    paragraphs = parse_document("my_newsletter.docx")
    report = run(template_name, rules, paragraphs, source_file="my_newsletter.docx")

    print(report.summary())
    for change in report.changes:
        print(change)
    for violation in report.violations:
        print(violation)
"""

from .executor import run
from .loader import load_template
from .models import (
    Bucket,
    Change,
    LintReport,
    ParagraphData,
    RulePass,
    Section,
    StyleMatch,
    StyleRequire,
    StyleRule,
    TextProhibitionRule,
    TextSubstitutionRule,
    Violation,
)
from .parser import parse_document

__all__ = [
    # Top-level functions
    "load_template",
    "parse_document",
    "run",
    # Models
    "Bucket",
    "Change",
    "LintReport",
    "ParagraphData",
    "RulePass",
    "Section",
    "StyleMatch",
    "StyleRequire",
    "StyleRule",
    "TextProhibitionRule",
    "TextSubstitutionRule",
    "Violation",
]
