"""
Executor – runs every rule against the parsed document and returns a LintReport.

Design
------
The executor is stateless: given a list of rules and a list of ParagraphData
objects it performs a single pass and returns an immutable LintReport.  Callers
can run the same executor against different documents or templates without
side-effects.

Rule application order
----------------------
1. Text substitution rules  (Language / Always) – applied in declaration order.
   Each rule mutates the working copy of the paragraph text so subsequent rules
   see already-corrected text.  The original text is preserved in the Change
   record for the diff view.

2. Text prohibition rules   (Language / Never) – applied after substitutions so
   that a phrase corrected by step 1 is not also flagged.

3. Style rules              (Branding / Formatting / Structure) – each matched
   paragraph is checked against the require descriptor; failures become
   Violation records.

NEVER-bucket style rules
------------------------
A StyleRule with bucket=NEVER means: if the paragraph matches *match* AND
also matches *require*, that is a violation.  Concretely, "never use red text
on Normal paragraphs" would have match={style_name:"Normal"} and
require={color_hex:"FF0000"}.  We reuse StyleRequire.violations_against() in
inverted form: a violation occurs when there are *zero* property mismatches
(i.e. require matches perfectly).
"""

from __future__ import annotations

from copy import deepcopy
from typing import Union

from .models import (
    Bucket,
    Change,
    LintReport,
    ParagraphData,
    RulePass,
    StyleRule,
    TextProhibitionRule,
    TextSubstitutionRule,
    Violation,
)

AnyRule = Union[TextSubstitutionRule, TextProhibitionRule, StyleRule]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run(
    template_name: str,
    rules: list[AnyRule],
    paragraphs: list[ParagraphData],
    source_file: str = "",
) -> LintReport:
    """Apply *rules* to *paragraphs* and return a :class:`LintReport`.

    Parameters
    ----------
    template_name:
        Human-readable name taken from the template (used in the report header).
    rules:
        Typed rule objects returned by :func:`brand_linter.loader.load_template`.
    paragraphs:
        Paragraph data returned by :func:`brand_linter.parser.parse_document`.
    source_file:
        Original document path, stored in the report for traceability.

    Returns
    -------
    LintReport
    """
    report = LintReport(
        template_name=template_name,
        source_file=source_file,
    )

    # Working copies of paragraph text (updated as substitutions are applied).
    working_texts: list[str] = [p.text for p in paragraphs]

    # --- 1. Text substitution rules -----------------------------------------
    substitution_rules = [r for r in rules if isinstance(r, TextSubstitutionRule)]
    for rule in substitution_rules:
        changed_count = 0
        for para in paragraphs:
            original = working_texts[para.index]
            corrected, n = rule.apply(original)
            if n > 0:
                report.changes.append(
                    Change(
                        paragraph_index=para.index,
                        rule_id=rule.id,
                        rule_description=rule.description,
                        original_text=original,
                        corrected_text=corrected,
                        occurrences=n,
                    )
                )
                working_texts[para.index] = corrected
                changed_count += 1

        if changed_count == 0:
            report.passes.append(
                RulePass(
                    rule_id=rule.id,
                    rule_description=rule.description,
                    paragraphs_checked=len(paragraphs),
                )
            )

    # --- 2. Text prohibition rules ------------------------------------------
    prohibition_rules = [r for r in rules if isinstance(r, TextProhibitionRule)]
    for rule in prohibition_rules:
        flagged_count = 0
        for para in paragraphs:
            # Use corrected text so already-fixed phrases aren't double-flagged.
            text = working_texts[para.index]
            matches = rule.find_occurrences(text)
            for m in matches:
                report.violations.append(
                    Violation(
                        paragraph_index=para.index,
                        rule_id=rule.id,
                        rule_description=rule.description,
                        detail=(
                            f"Prohibited term '{m.group()}' found at position "
                            f"{m.start()}–{m.end()} in {para.ref}"
                        ),
                    )
                )
                flagged_count += 1

        if flagged_count == 0:
            report.passes.append(
                RulePass(
                    rule_id=rule.id,
                    rule_description=rule.description,
                    paragraphs_checked=len(paragraphs),
                )
            )

    # --- 3. Style rules -----------------------------------------------------
    style_rules = [r for r in rules if isinstance(r, StyleRule)]
    for rule in style_rules:
        matched_paras = [p for p in paragraphs if rule.match.matches(p)]
        violation_count = 0

        for para in matched_paras:
            if rule.bucket == Bucket.ALWAYS:
                # Require that *require* properties hold.
                failures = rule.require.violations_against(para)
                for detail in failures:
                    report.violations.append(
                        Violation(
                            paragraph_index=para.index,
                            rule_id=rule.id,
                            rule_description=rule.description,
                            detail=f"{para.ref}: {detail}",
                        )
                    )
                    violation_count += 1

            elif rule.bucket == Bucket.NEVER:
                # Violation when *require* matches the paragraph perfectly
                # (none of the listed properties differ).
                failures = rule.require.violations_against(para)
                if not failures:
                    # Every specified property matches → violation.
                    report.violations.append(
                        Violation(
                            paragraph_index=para.index,
                            rule_id=rule.id,
                            rule_description=rule.description,
                            detail=(
                                f"{para.ref}: paragraph matches a prohibited "
                                "style combination"
                            ),
                        )
                    )
                    violation_count += 1

        if violation_count == 0:
            report.passes.append(
                RulePass(
                    rule_id=rule.id,
                    rule_description=rule.description,
                    paragraphs_checked=len(matched_paras),
                )
            )

    # Store the final corrected texts in report order.
    report.corrected_paragraphs = list(working_texts)

    return report
