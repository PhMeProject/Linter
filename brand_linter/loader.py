"""
Loader – reads a JSON template file and returns a validated list of typed
rule objects ready for the executor.

JSON schema (one template file)
--------------------------------
{
  "template_name": "Newsletter External",
  "version": "1.0",
  "rules": {
    "branding": {
      "typography": { "always": [...], "never": [...] },
      "colors":     { "always": [...], "never": [...] },
      "logos":      { "always": [...], "never": [...] }
    },
    "formatting": { "always": [...], "never": [...] },
    "language":   { "always": [...], "never": [...] },
    "structure":  { "always": [...], "never": [...] }
  }
}

Each rule entry must have a "type" field:
  "text_substitution" -> TextSubstitutionRule
  "text_prohibition"  -> TextProhibitionRule
  "style"             -> StyleRule
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Union

from .models import (
    Bucket,
    Section,
    StyleMatch,
    StyleRequire,
    StyleRule,
    TextProhibitionRule,
    TextSubstitutionRule,
)

AnyRule = Union[TextSubstitutionRule, TextProhibitionRule, StyleRule]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class TemplateLoadError(Exception):
    """Raised when a template file is malformed."""


def load_template(path: Union[str, Path]) -> tuple[str, list[AnyRule]]:
    """Load a template JSON file and return (template_name, rules).

    Parameters
    ----------
    path:
        Absolute or relative path to a ``.json`` template file.

    Returns
    -------
    template_name : str
    rules : list of typed rule objects
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Template not found: {path}")
    if path.suffix.lower() != ".json":
        raise TemplateLoadError(f"Template must be a .json file, got: {path.suffix}")

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TemplateLoadError(f"Invalid JSON in {path}: {exc}") from exc

    return _parse_template(raw, source=str(path))


def load_template_from_dict(data: dict) -> tuple[str, list[AnyRule]]:
    """Parse a template from an in-memory dict and return (template_name, rules).

    Identical validation to load_template() but requires no file on disk.
    Use this when the template JSON has been built in memory (e.g. derived
    from user settings stored in the database).

    Parameters
    ----------
    data:
        A dict matching the template JSON schema.

    Returns
    -------
    template_name : str
    rules : list of typed rule objects
    """
    if not isinstance(data, dict):
        raise TemplateLoadError("Template data must be a JSON object")
    return _parse_template(data, source="<db>")


# ---------------------------------------------------------------------------
# Internal parsing
# ---------------------------------------------------------------------------


def _parse_template(raw: dict[str, Any], source: str) -> tuple[str, list[AnyRule]]:
    if not isinstance(raw, dict):
        raise TemplateLoadError(f"{source}: root must be a JSON object")

    template_name: str = raw.get("template_name", "")
    if not template_name:
        raise TemplateLoadError(f"{source}: missing 'template_name'")

    rules_raw = raw.get("rules")
    if not isinstance(rules_raw, dict):
        raise TemplateLoadError(f"{source}: missing or invalid 'rules' object")

    rules: list[AnyRule] = []

    # Branding sub-sections
    branding = rules_raw.get("branding", {})
    rules.extend(_parse_section(branding.get("typography", {}), Section.BRANDING_TYPOGRAPHY, source))
    rules.extend(_parse_section(branding.get("colors", {}), Section.BRANDING_COLORS, source))
    rules.extend(_parse_section(branding.get("logos", {}), Section.BRANDING_LOGOS, source))

    # Flat sections
    rules.extend(_parse_section(rules_raw.get("formatting", {}), Section.FORMATTING, source))
    rules.extend(_parse_section(rules_raw.get("language", {}), Section.LANGUAGE, source))
    rules.extend(_parse_section(rules_raw.get("structure", {}), Section.STRUCTURE, source))

    return template_name, rules


def _parse_section(
    section_raw: dict[str, Any],
    section: Section,
    source: str,
) -> list[AnyRule]:
    rules: list[AnyRule] = []
    for bucket_key in (Bucket.ALWAYS, Bucket.NEVER):
        entries = section_raw.get(bucket_key.value, [])
        if not isinstance(entries, list):
            raise TemplateLoadError(
                f"{source}: '{section.value}.{bucket_key.value}' must be a list"
            )
        for entry in entries:
            rules.append(_parse_rule(entry, section, bucket_key, source))
    return rules


def _parse_rule(
    entry: dict[str, Any],
    section: Section,
    bucket: Bucket,
    source: str,
) -> AnyRule:
    if not isinstance(entry, dict):
        raise TemplateLoadError(f"{source}: rule entry must be a JSON object, got {type(entry)}")

    rule_type = entry.get("type")
    rule_id = entry.get("id", "")
    description = entry.get("description", "")

    if not rule_id:
        raise TemplateLoadError(f"{source}: rule is missing 'id' field: {entry}")
    if not description:
        raise TemplateLoadError(f"{source}: rule '{rule_id}' is missing 'description'")

    if rule_type == "text_substitution":
        return _parse_text_substitution(entry, section, bucket, source)
    elif rule_type == "text_prohibition":
        return _parse_text_prohibition(entry, section, bucket, source)
    elif rule_type == "style":
        return _parse_style_rule(entry, section, bucket, source)
    else:
        raise TemplateLoadError(
            f"{source}: unknown rule type '{rule_type}' in rule '{rule_id}'"
        )


def _parse_text_substitution(
    entry: dict[str, Any],
    section: Section,
    bucket: Bucket,
    source: str,
) -> TextSubstitutionRule:
    rule_id = entry["id"]
    find = entry.get("find")
    replace = entry.get("replace")
    if not find:
        raise TemplateLoadError(f"{source}: rule '{rule_id}' missing 'find'")
    if replace is None:
        raise TemplateLoadError(f"{source}: rule '{rule_id}' missing 'replace'")
    return TextSubstitutionRule(
        id=rule_id,
        description=entry["description"],
        section=section,
        bucket=bucket,
        find=find,
        replace=replace,
        case_sensitive=bool(entry.get("case_sensitive", False)),
        whole_word=bool(entry.get("whole_word", False)),
    )


def _parse_text_prohibition(
    entry: dict[str, Any],
    section: Section,
    bucket: Bucket,
    source: str,
) -> TextProhibitionRule:
    rule_id = entry["id"]
    find = entry.get("find")
    if not find:
        raise TemplateLoadError(f"{source}: rule '{rule_id}' missing 'find'")
    return TextProhibitionRule(
        id=rule_id,
        description=entry["description"],
        section=section,
        bucket=bucket,
        find=find,
        case_sensitive=bool(entry.get("case_sensitive", False)),
        whole_word=bool(entry.get("whole_word", False)),
    )


def _parse_style_rule(
    entry: dict[str, Any],
    section: Section,
    bucket: Bucket,
    source: str,
) -> StyleRule:
    rule_id = entry["id"]

    match_raw = entry.get("match", {})
    if not isinstance(match_raw, dict):
        raise TemplateLoadError(f"{source}: rule '{rule_id}' 'match' must be an object")

    require_raw = entry.get("require", {})
    if not isinstance(require_raw, dict):
        raise TemplateLoadError(f"{source}: rule '{rule_id}' 'require' must be an object")

    match = StyleMatch(
        style_name=match_raw.get("style_name"),
        font_name=match_raw.get("font_name"),
        font_size=_optional_float(match_raw.get("font_size"), rule_id, "match.font_size", source),
        bold=match_raw.get("bold"),
        italic=match_raw.get("italic"),
    )

    require = StyleRequire(
        font_name=require_raw.get("font_name"),
        font_size=_optional_float(require_raw.get("font_size"), rule_id, "require.font_size", source),
        bold=require_raw.get("bold"),
        italic=require_raw.get("italic"),
        color_hex=require_raw.get("color_hex"),
    )

    return StyleRule(
        id=rule_id,
        description=entry["description"],
        section=section,
        bucket=bucket,
        match=match,
        require=require,
    )


def _optional_float(
    value: Any,
    rule_id: str,
    field_name: str,
    source: str,
) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise TemplateLoadError(
            f"{source}: rule '{rule_id}' field '{field_name}' must be a number"
        ) from exc
