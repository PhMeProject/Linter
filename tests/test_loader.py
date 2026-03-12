"""Tests for brand_linter.loader."""

import json
import textwrap
from pathlib import Path

import pytest

from brand_linter.loader import TemplateLoadError, load_template
from brand_linter.models import (
    Bucket,
    Section,
    StyleRule,
    TextProhibitionRule,
    TextSubstitutionRule,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


def write_template(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "template.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


MINIMAL_TEMPLATE = {
    "template_name": "Test Template",
    "version": "1.0",
    "rules": {
        "branding": {
            "typography": {"always": [], "never": []},
            "colors": {"always": [], "never": []},
            "logos": {"always": [], "never": []},
        },
        "formatting": {"always": [], "never": []},
        "language": {"always": [], "never": []},
        "structure": {"always": [], "never": []},
    },
}


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------


def test_load_newsletter_external():
    path = TEMPLATES_DIR / "newsletter_external.json"
    name, rules = load_template(path)
    assert name == "Newsletter External"
    assert len(rules) > 0


def test_load_newsletter_internal():
    path = TEMPLATES_DIR / "newsletter_internal.json"
    name, rules = load_template(path)
    assert name == "Newsletter Internal"
    assert len(rules) > 0


def test_minimal_template(tmp_path):
    p = write_template(tmp_path, MINIMAL_TEMPLATE)
    name, rules = load_template(p)
    assert name == "Test Template"
    assert rules == []


def test_text_substitution_rule_parsed(tmp_path):
    data = {**MINIMAL_TEMPLATE}
    data["rules"] = {
        **MINIMAL_TEMPLATE["rules"],
        "language": {
            "always": [
                {
                    "type": "text_substitution",
                    "id": "lang-001",
                    "description": "employees → team members",
                    "find": "employees",
                    "replace": "team members",
                    "case_sensitive": False,
                    "whole_word": True,
                }
            ],
            "never": [],
        },
    }
    _, rules = load_template(write_template(tmp_path, data))
    assert len(rules) == 1
    rule = rules[0]
    assert isinstance(rule, TextSubstitutionRule)
    assert rule.id == "lang-001"
    assert rule.find == "employees"
    assert rule.replace == "team members"
    assert rule.whole_word is True
    assert rule.case_sensitive is False
    assert rule.section == Section.LANGUAGE
    assert rule.bucket == Bucket.ALWAYS


def test_text_prohibition_rule_parsed(tmp_path):
    data = {**MINIMAL_TEMPLATE}
    data["rules"] = {
        **MINIMAL_TEMPLATE["rules"],
        "language": {
            "always": [],
            "never": [
                {
                    "type": "text_prohibition",
                    "id": "lang-n001",
                    "description": "never synergy",
                    "find": "synergy",
                }
            ],
        },
    }
    _, rules = load_template(write_template(tmp_path, data))
    rule = rules[0]
    assert isinstance(rule, TextProhibitionRule)
    assert rule.bucket == Bucket.NEVER
    assert rule.find == "synergy"


def test_style_rule_parsed(tmp_path):
    data = {**MINIMAL_TEMPLATE}
    data["rules"] = {
        **MINIMAL_TEMPLATE["rules"],
        "branding": {
            "typography": {
                "always": [
                    {
                        "type": "style",
                        "id": "typo-001",
                        "description": "Normal must be Calibri 11pt",
                        "match": {"style_name": "Normal"},
                        "require": {"font_name": "Calibri", "font_size": 11},
                    }
                ],
                "never": [],
            },
            "colors": {"always": [], "never": []},
            "logos": {"always": [], "never": []},
        },
    }
    _, rules = load_template(write_template(tmp_path, data))
    rule = rules[0]
    assert isinstance(rule, StyleRule)
    assert rule.section == Section.BRANDING_TYPOGRAPHY
    assert rule.bucket == Bucket.ALWAYS
    assert rule.match.style_name == "Normal"
    assert rule.require.font_name == "Calibri"
    assert rule.require.font_size == 11.0


def test_font_size_stored_as_float(tmp_path):
    data = {**MINIMAL_TEMPLATE}
    data["rules"] = {
        **MINIMAL_TEMPLATE["rules"],
        "branding": {
            "typography": {
                "always": [
                    {
                        "type": "style",
                        "id": "typo-float",
                        "description": "size check",
                        "match": {"style_name": "Normal"},
                        "require": {"font_size": 11},
                    }
                ],
                "never": [],
            },
            "colors": {"always": [], "never": []},
            "logos": {"always": [], "never": []},
        },
    }
    _, rules = load_template(write_template(tmp_path, data))
    assert isinstance(rules[0].require.font_size, float)


# ---------------------------------------------------------------------------
# Error-path tests
# ---------------------------------------------------------------------------


def test_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        load_template("/nonexistent/path/template.json")


def test_wrong_extension_raises(tmp_path):
    p = tmp_path / "template.txt"
    p.write_text("{}", encoding="utf-8")
    with pytest.raises(TemplateLoadError, match="json"):
        load_template(p)


def test_invalid_json_raises(tmp_path):
    p = tmp_path / "template.json"
    p.write_text("{ invalid json", encoding="utf-8")
    with pytest.raises(TemplateLoadError, match="Invalid JSON"):
        load_template(p)


def test_missing_template_name_raises(tmp_path):
    data = {**MINIMAL_TEMPLATE}
    del data["template_name"]
    with pytest.raises(TemplateLoadError, match="template_name"):
        load_template(write_template(tmp_path, data))


def test_unknown_rule_type_raises(tmp_path):
    data = {**MINIMAL_TEMPLATE}
    data["rules"] = {
        **MINIMAL_TEMPLATE["rules"],
        "language": {
            "always": [
                {
                    "type": "unknown_type",
                    "id": "x-001",
                    "description": "bad",
                }
            ],
            "never": [],
        },
    }
    with pytest.raises(TemplateLoadError, match="unknown rule type"):
        load_template(write_template(tmp_path, data))


def test_missing_rule_id_raises(tmp_path):
    data = {**MINIMAL_TEMPLATE}
    data["rules"] = {
        **MINIMAL_TEMPLATE["rules"],
        "language": {
            "always": [
                {
                    "type": "text_substitution",
                    "description": "no id",
                    "find": "x",
                    "replace": "y",
                }
            ],
            "never": [],
        },
    }
    with pytest.raises(TemplateLoadError, match="missing 'id'"):
        load_template(write_template(tmp_path, data))


def test_substitution_missing_find_raises(tmp_path):
    data = {**MINIMAL_TEMPLATE}
    data["rules"] = {
        **MINIMAL_TEMPLATE["rules"],
        "language": {
            "always": [
                {
                    "type": "text_substitution",
                    "id": "x-001",
                    "description": "desc",
                    "replace": "y",
                }
            ],
            "never": [],
        },
    }
    with pytest.raises(TemplateLoadError, match="missing 'find'"):
        load_template(write_template(tmp_path, data))


def test_style_rule_invalid_font_size_raises(tmp_path):
    data = {**MINIMAL_TEMPLATE}
    data["rules"] = {
        **MINIMAL_TEMPLATE["rules"],
        "branding": {
            "typography": {
                "always": [
                    {
                        "type": "style",
                        "id": "typo-bad",
                        "description": "bad size",
                        "match": {},
                        "require": {"font_size": "not-a-number"},
                    }
                ],
                "never": [],
            },
            "colors": {"always": [], "never": []},
            "logos": {"always": [], "never": []},
        },
    }
    with pytest.raises(TemplateLoadError, match="must be a number"):
        load_template(write_template(tmp_path, data))
