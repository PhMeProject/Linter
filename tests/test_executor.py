"""Tests for brand_linter.executor."""

import pytest

from brand_linter.executor import run
from brand_linter.models import (
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def para(index: int, text: str, **kwargs) -> ParagraphData:
    return ParagraphData(
        index=index,
        text=text,
        style_name=kwargs.get("style_name", "Normal"),
        font_name=kwargs.get("font_name"),
        font_size=kwargs.get("font_size"),
        bold=kwargs.get("bold"),
        italic=kwargs.get("italic"),
        color_hex=kwargs.get("color_hex"),
    )


def sub_rule(rule_id: str, find: str, replace: str, **kwargs) -> TextSubstitutionRule:
    return TextSubstitutionRule(
        id=rule_id,
        description=f"Replace {find} with {replace}",
        section=Section.LANGUAGE,
        bucket=Bucket.ALWAYS,
        find=find,
        replace=replace,
        case_sensitive=kwargs.get("case_sensitive", False),
        whole_word=kwargs.get("whole_word", False),
    )


def prohibit_rule(rule_id: str, find: str, **kwargs) -> TextProhibitionRule:
    return TextProhibitionRule(
        id=rule_id,
        description=f"Never use '{find}'",
        section=Section.LANGUAGE,
        bucket=Bucket.NEVER,
        find=find,
        case_sensitive=kwargs.get("case_sensitive", False),
        whole_word=kwargs.get("whole_word", False),
    )


def style_always(rule_id: str, match: dict, require: dict) -> StyleRule:
    return StyleRule(
        id=rule_id,
        description=f"Style rule {rule_id}",
        section=Section.FORMATTING,
        bucket=Bucket.ALWAYS,
        match=StyleMatch(**match),
        require=StyleRequire(**require),
    )


def style_never(rule_id: str, match: dict, require: dict) -> StyleRule:
    return StyleRule(
        id=rule_id,
        description=f"Style rule {rule_id}",
        section=Section.FORMATTING,
        bucket=Bucket.NEVER,
        match=StyleMatch(**match),
        require=StyleRequire(**require),
    )


# ---------------------------------------------------------------------------
# Return type
# ---------------------------------------------------------------------------


def test_run_returns_lint_report():
    report = run("T", [], [])
    assert isinstance(report, LintReport)


def test_report_stores_template_name():
    report = run("My Template", [], [])
    assert report.template_name == "My Template"


def test_report_stores_source_file():
    report = run("T", [], [], source_file="doc.docx")
    assert report.source_file == "doc.docx"


# ---------------------------------------------------------------------------
# Text substitution
# ---------------------------------------------------------------------------


def test_substitution_applied():
    paragraphs = [para(0, "All employees are welcome.")]
    rule = sub_rule("r1", "employees", "team members")
    report = run("T", [rule], paragraphs)
    assert len(report.changes) == 1
    change = report.changes[0]
    assert change.rule_id == "r1"
    assert change.original_text == "All employees are welcome."
    assert change.corrected_text == "All team members are welcome."
    assert change.paragraph_index == 0


def test_substitution_case_insensitive_by_default():
    paragraphs = [para(0, "EMPLOYEES and Employees work here.")]
    rule = sub_rule("r1", "employees", "team members", case_sensitive=False)
    report = run("T", [rule], paragraphs)
    assert len(report.changes) == 1
    assert "EMPLOYEES" not in report.changes[0].corrected_text
    assert "Employees" not in report.changes[0].corrected_text


def test_substitution_case_sensitive():
    paragraphs = [para(0, "EMPLOYEES and employees here.")]
    rule = sub_rule("r1", "employees", "team members", case_sensitive=True)
    report = run("T", [rule], paragraphs)
    # Only lowercase "employees" should be replaced
    assert len(report.changes) == 1
    assert "EMPLOYEES" in report.changes[0].corrected_text
    assert "team members" in report.changes[0].corrected_text


def test_substitution_whole_word_does_not_match_partial():
    paragraphs = [para(0, "employeeship")]
    rule = sub_rule("r1", "employees", "team members", whole_word=True)
    report = run("T", [rule], paragraphs)
    assert len(report.changes) == 0
    assert len(report.passes) == 1


def test_substitution_multiple_occurrences_counted():
    paragraphs = [para(0, "employees and more employees here")]
    rule = sub_rule("r1", "employees", "team members")
    report = run("T", [rule], paragraphs)
    assert report.changes[0].occurrences == 2


def test_substitution_no_match_produces_pass():
    paragraphs = [para(0, "No problematic words here.")]
    rule = sub_rule("r1", "employees", "team members")
    report = run("T", [rule], paragraphs)
    assert len(report.changes) == 0
    assert len(report.passes) == 1
    assert report.passes[0].rule_id == "r1"


def test_corrected_paragraphs_list_populated():
    paragraphs = [
        para(0, "Hello employees."),
        para(1, "Goodbye."),
    ]
    rule = sub_rule("r1", "employees", "team members")
    report = run("T", [rule], paragraphs)
    assert report.corrected_paragraphs[0] == "Hello team members."
    assert report.corrected_paragraphs[1] == "Goodbye."


def test_substitution_chains_across_rules():
    """Second substitution rule sees text already corrected by first rule."""
    paragraphs = [para(0, "vendors supply employees")]
    rules = [
        sub_rule("r1", "employees", "team members"),
        sub_rule("r2", "team members", "staff"),  # should see corrected text
    ]
    report = run("T", rules, paragraphs)
    assert report.corrected_paragraphs[0] == "vendors supply staff"


# ---------------------------------------------------------------------------
# Text prohibition
# ---------------------------------------------------------------------------


def test_prohibition_flags_term():
    paragraphs = [para(0, "We leverage synergy here.")]
    rule = prohibit_rule("r1", "synergy")
    report = run("T", [rule], paragraphs)
    assert len(report.violations) == 1
    v = report.violations[0]
    assert v.rule_id == "r1"
    assert v.paragraph_index == 0
    assert "synergy" in v.detail.lower()


def test_prohibition_no_match_produces_pass():
    paragraphs = [para(0, "Clean text.")]
    rule = prohibit_rule("r1", "synergy")
    report = run("T", [rule], paragraphs)
    assert len(report.violations) == 0
    assert len(report.passes) == 1


def test_prohibition_does_not_flag_corrected_substitution():
    """A phrase removed by a substitution rule should not trigger prohibition."""
    paragraphs = [para(0, "synergy is bad")]
    rules = [
        sub_rule("r1", "synergy", "collaboration"),
        prohibit_rule("r2", "synergy"),
    ]
    report = run("T", rules, paragraphs)
    # The substitution removes 'synergy' first; prohibition should find nothing.
    assert len(report.violations) == 0


def test_prohibition_whole_word():
    paragraphs = [para(0, "synergytics is fine, but synergy is not")]
    rule = prohibit_rule("r1", "synergy", whole_word=True)
    report = run("T", [rule], paragraphs)
    assert len(report.violations) == 1
    assert "synergy" in report.violations[0].detail


def test_prohibition_multiple_occurrences_each_flagged():
    paragraphs = [para(0, "synergy synergy synergy")]
    rule = prohibit_rule("r1", "synergy")
    report = run("T", [rule], paragraphs)
    assert len(report.violations) == 3


# ---------------------------------------------------------------------------
# Style rules – ALWAYS bucket
# ---------------------------------------------------------------------------


def test_style_always_no_violation_when_compliant():
    paragraphs = [para(0, "Normal text", font_name="Calibri", font_size=11.0)]
    rule = style_always("s1", {"style_name": "Normal"}, {"font_name": "Calibri", "font_size": 11.0})
    report = run("T", [rule], paragraphs)
    assert len(report.violations) == 0
    assert len(report.passes) == 1


def test_style_always_flags_wrong_font():
    paragraphs = [para(0, "Body text", style_name="Normal", font_name="Times New Roman", font_size=11.0)]
    rule = style_always("s1", {"style_name": "Normal"}, {"font_name": "Calibri"})
    report = run("T", [rule], paragraphs)
    assert len(report.violations) == 1
    assert "Calibri" in report.violations[0].detail


def test_style_always_flags_wrong_size():
    paragraphs = [para(0, "Big text", style_name="Normal", font_name="Calibri", font_size=14.0)]
    rule = style_always("s1", {"style_name": "Normal"}, {"font_size": 11.0})
    report = run("T", [rule], paragraphs)
    assert len(report.violations) == 1
    assert "11.0pt" in report.violations[0].detail


def test_style_always_skip_non_matching_paragraphs():
    paragraphs = [
        para(0, "Normal para", style_name="Normal", font_name="Arial"),
        para(1, "Heading para", style_name="Heading 1", font_name="Comic Sans MS"),
    ]
    # Rule targets only Heading 1
    rule = style_always("s1", {"style_name": "Heading 1"}, {"font_name": "Calibri"})
    report = run("T", [rule], paragraphs)
    assert len(report.violations) == 1
    assert report.violations[0].paragraph_index == 1


def test_style_always_bold_requirement():
    paragraphs = [para(0, "Heading", style_name="Heading 1", bold=False)]
    rule = style_always("s1", {"style_name": "Heading 1"}, {"bold": True})
    report = run("T", [rule], paragraphs)
    assert len(report.violations) == 1
    assert "bold" in report.violations[0].detail


def test_style_always_color_requirement():
    paragraphs = [para(0, "Heading", style_name="Heading 1", color_hex="FF0000")]
    rule = style_always("s1", {"style_name": "Heading 1"}, {"color_hex": "1F3864"})
    report = run("T", [rule], paragraphs)
    assert len(report.violations) == 1
    assert "1F3864" in report.violations[0].detail


# ---------------------------------------------------------------------------
# Style rules – NEVER bucket
# ---------------------------------------------------------------------------


def test_style_never_flags_prohibited_combination():
    paragraphs = [para(0, "Normal text", style_name="Normal", font_name="Comic Sans MS")]
    rule = style_never("s1", {}, {"font_name": "Comic Sans MS"})
    report = run("T", [rule], paragraphs)
    assert len(report.violations) == 1


def test_style_never_no_violation_when_absent():
    paragraphs = [para(0, "Normal text", style_name="Normal", font_name="Calibri")]
    rule = style_never("s1", {}, {"font_name": "Comic Sans MS"})
    report = run("T", [rule], paragraphs)
    assert len(report.violations) == 0
    assert len(report.passes) == 1


# ---------------------------------------------------------------------------
# Report state helpers
# ---------------------------------------------------------------------------


def test_is_clean_true_when_no_issues():
    paragraphs = [para(0, "All good here.")]
    report = run("T", [], paragraphs)
    assert report.is_clean is True


def test_is_clean_false_when_violations():
    paragraphs = [para(0, "synergy")]
    rule = prohibit_rule("r1", "synergy")
    report = run("T", [rule], paragraphs)
    assert report.is_clean is False


def test_is_clean_false_when_changes():
    paragraphs = [para(0, "employees")]
    rule = sub_rule("r1", "employees", "team members")
    report = run("T", [rule], paragraphs)
    assert report.is_clean is False


def test_summary_keys():
    report = run("My Template", [], [para(0, "x")], source_file="f.docx")
    s = report.summary()
    assert s["template"] == "My Template"
    assert s["source"] == "f.docx"
    assert "changes" in s
    assert "violations" in s
    assert "passes" in s
    assert "clean" in s


# ---------------------------------------------------------------------------
# Integration: load templates from disk and run against synthetic paragraphs
# ---------------------------------------------------------------------------


def test_integration_newsletter_external(tmp_path):
    from pathlib import Path
    from brand_linter.loader import load_template

    templates_dir = Path(__file__).parent.parent / "templates"
    name, rules = load_template(templates_dir / "newsletter_external.json")

    paragraphs = [
        para(0, "Our employees love synergy.", style_name="Normal",
             font_name="Calibri", font_size=11.0),
    ]
    report = run(name, rules, paragraphs, source_file="test.docx")

    # "employees" → "team members" substitution should fire
    assert any(c.rule_id == "lang-ext-001" for c in report.changes)
    # "synergy" prohibition should fire
    assert any(v.rule_id == "lang-ext-n001" for v in report.violations)
