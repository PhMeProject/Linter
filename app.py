"""
Brand Linter – Flask web application.

Routes
------
GET  /                upload form (lists available templates)
POST /lint            upload .docx + template → JSON lint report + session id
POST /download        session id + accepted rule ids → corrected .docx
GET  /settings        settings UI
GET  /api/settings    return current user settings as JSON
POST /api/settings    save user settings and regenerate custom template
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file

from brand_linter.executor import run as lint_run
from brand_linter.loader import load_template
from brand_linter.models import Bucket, TextSubstitutionRule, TextProhibitionRule, StyleRule
from brand_linter.parser import parse_document
from brand_linter.writer import build_corrected_document

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = Flask(__name__, template_folder="web/templates", static_folder="web/static")

TEMPLATES_DIR = Path("templates")
UPLOAD_DIR    = Path("/tmp/brand_linter_sessions")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Keep all runtime-writable state in /tmp so read-only serverless
# filesystems don't crash the function at startup.
_TMP_DIR      = Path("/tmp/brand_linter_state")
_TMP_DIR.mkdir(parents=True, exist_ok=True)
SETTINGS_FILE = _TMP_DIR / "user_settings.json"

# Fixed slug for the user-generated custom template.
CUSTOM_TEMPLATE_SLUG = "custom_template"
_CUSTOM_TEMPLATE_PATH = _TMP_DIR / f"{CUSTOM_TEMPLATE_SLUG}.json"

# Default values – fields matching these are treated as "no rule set".
_TYPO_DEFAULTS = {
    "font_family": "Calibri",
    "font_size": 11,
    "font_color": "#000000",
    "bold": False,
    "italic": False,
}

# Maps settings section key → Word paragraph style name
_STYLE_NAMES = {
    "h1":       "Heading 1",
    "body":     "Normal",
    "captions": "Caption",
}

# In-memory session store.  Maps session_id → dict with docx path + rules.
# Sessions last for the lifetime of the process (sufficient for single-user
# desktop use; swap for Redis/filesystem for multi-user deployments).
_sessions: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_template_list() -> list[dict[str, str]]:
    """Return [{slug, name}, …] for every template in TEMPLATES_DIR plus any
    custom template saved to /tmp."""
    paths = sorted(TEMPLATES_DIR.glob("*.json"))
    # Append the /tmp custom template if it exists and isn't shadowed by a
    # same-named file in TEMPLATES_DIR.
    static_slugs = {p.stem for p in paths}
    if _CUSTOM_TEMPLATE_PATH.exists() and CUSTOM_TEMPLATE_SLUG not in static_slugs:
        paths = list(paths) + [_CUSTOM_TEMPLATE_PATH]
    result = []
    for p in paths:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            name = data.get("template_name", p.stem)
        except Exception:
            name = p.stem
        result.append({"slug": p.stem, "name": name})
    return result


def _find_template_path(slug: str) -> Path | None:
    # Check /tmp first so a saved custom template takes precedence.
    if slug == CUSTOM_TEMPLATE_SLUG and _CUSTOM_TEMPLATE_PATH.exists():
        return _CUSTOM_TEMPLATE_PATH
    candidate = TEMPLATES_DIR / f"{slug}.json"
    return candidate if candidate.exists() else None


def _build_violation_details(violations, rule_map: dict) -> list[dict]:
    """Serialize violations, enriching TextProhibitionRule entries with find info."""
    result = []
    for v in violations:
        detail: dict = {
            "rule_id": v.rule_id,
            "description": v.rule_description,
            "detail": v.detail,
        }
        rule = rule_map.get(v.rule_id)
        if isinstance(rule, TextProhibitionRule):
            detail["find"] = rule.find
            detail["case_sensitive"] = rule.case_sensitive
            detail["whole_word"] = rule.whole_word
        elif isinstance(rule, StyleRule) and rule.bucket == Bucket.ALWAYS:
            # Include the required values so the frontend can apply them visually
            fix: dict = {}
            if rule.require.font_name  is not None: fix["font_name"]  = rule.require.font_name
            if rule.require.font_size  is not None: fix["font_size"]  = rule.require.font_size
            if rule.require.bold       is not None: fix["bold"]       = rule.require.bold
            if rule.require.italic     is not None: fix["italic"]     = rule.require.italic
            if rule.require.color_hex  is not None: fix["color_hex"]  = rule.require.color_hex
            if fix:
                detail["fix"] = fix
        result.append(detail)
    return result


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/")
def index():
    return render_template("index.html", templates=_load_template_list())


@app.post("/lint")
def lint():
    # --- validate inputs ---------------------------------------------------
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    uploaded = request.files["file"]
    if not uploaded.filename or not uploaded.filename.lower().endswith(".docx"):
        return jsonify({"error": "Only .docx files are supported"}), 400

    slug = request.form.get("template", "")
    template_path = _find_template_path(slug)
    if template_path is None:
        return jsonify({"error": f"Unknown template: '{slug}'"}), 400

    # --- save upload -------------------------------------------------------
    session_id = str(uuid.uuid4())
    docx_path = UPLOAD_DIR / f"{session_id}.docx"
    uploaded.save(str(docx_path))

    # --- run linter --------------------------------------------------------
    try:
        template_name, rules = load_template(template_path)
        paragraphs = parse_document(docx_path)
        report = lint_run(
            template_name, rules, paragraphs, source_file=uploaded.filename
        )
    except Exception as exc:
        docx_path.unlink(missing_ok=True)
        return jsonify({"error": str(exc)}), 422

    # --- persist session ---------------------------------------------------
    _sessions[session_id] = {
        "docx_path": docx_path,
        "rules": rules,
        "original_filename": uploaded.filename,
    }

    # --- build response payload --------------------------------------------
    rule_map = {r.id: r for r in rules}

    # Index changes and violations by paragraph index
    changes_by_para: dict[int, list] = {}
    for c in report.changes:
        changes_by_para.setdefault(c.paragraph_index, []).append(c)

    violations_by_para: dict[int, list] = {}
    for v in report.violations:
        violations_by_para.setdefault(v.paragraph_index, []).append(v)

    para_payload = []
    for para in paragraphs:
        idx = para.index
        para_changes = changes_by_para.get(idx, [])
        para_violations = violations_by_para.get(idx, [])

        # Enrich change records with find/replace so the frontend can
        # re-apply/highlight substitutions client-side.
        change_details = []
        for c in para_changes:
            rule = rule_map.get(c.rule_id)
            detail: dict = {
                "rule_id": c.rule_id,
                "description": c.rule_description,
            }
            if isinstance(rule, TextSubstitutionRule):
                detail["find"] = rule.find
                detail["replace"] = rule.replace
                detail["case_sensitive"] = rule.case_sensitive
                detail["whole_word"] = rule.whole_word
            change_details.append(detail)

        para_payload.append(
            {
                "index": idx,
                "style_name": para.style_name,
                "font_size": para.font_size,
                "bold": para.bold,
                "italic": para.italic,
                "color_hex": para.color_hex,
                "original": para.text,
                "corrected": report.corrected_paragraphs[idx],
                "has_changes": bool(para_changes),
                "has_violations": bool(para_violations),
                "changes": change_details,
                "violations": _build_violation_details(para_violations, rule_map),
            }
        )

    return jsonify(
        {
            "session_id": session_id,
            "template_name": template_name,
            "summary": report.summary(),
            "paragraphs": para_payload,
        }
    )


@app.post("/download")
def download():
    payload = request.get_json(silent=True) or {}
    session_id = payload.get("session_id", "")
    accepted_ids: set[str] = set(payload.get("accepted_rule_ids", []))

    session = _sessions.get(session_id)
    if session is None:
        return jsonify({"error": "Session not found or expired"}), 404

    buf = build_corrected_document(
        session["docx_path"], session["rules"], accepted_ids
    )

    stem = Path(session["original_filename"]).stem
    download_name = f"{stem}_corrected.docx"

    return send_file(
        buf,
        mimetype=(
            "application/vnd.openxmlformats-officedocument"
            ".wordprocessingml.document"
        ),
        as_attachment=True,
        download_name=download_name,
    )


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------


def _build_custom_template(settings: dict) -> dict:
    """Convert raw settings dict → template JSON understood by load_template."""
    template_name = (settings.get("template_name") or "Custom Rules").strip() or "Custom Rules"
    typography = settings.get("typography", {})

    always_rules = []
    for section_key, style_name in _STYLE_NAMES.items():
        section = typography.get(section_key, {})
        require: dict = {}

        font_family = section.get("font_family") or _TYPO_DEFAULTS["font_family"]
        if font_family != _TYPO_DEFAULTS["font_family"]:
            require["font_name"] = font_family

        raw_size = section.get("font_size")
        try:
            font_size = float(raw_size) if raw_size is not None else _TYPO_DEFAULTS["font_size"]
        except (TypeError, ValueError):
            font_size = _TYPO_DEFAULTS["font_size"]
        if font_size != _TYPO_DEFAULTS["font_size"]:
            require["font_size"] = font_size

        color = (section.get("font_color") or "").lstrip("#").upper()
        if color and color != "000000":
            require["color_hex"] = color

        if section.get("bold") is True:
            require["bold"] = True

        if section.get("italic") is True:
            require["italic"] = True

        if require:
            rule_id = f"custom-{section_key}-001"
            always_rules.append({
                "type": "style",
                "id": rule_id,
                "description": f"{style_name} typography rules",
                "match": {"style_name": style_name},
                "require": require,
            })

    return {
        "template_name": template_name,
        "version": "1.0",
        "description": "Custom template created via Settings UI",
        "rules": {
            "branding": {
                "typography": {"always": always_rules, "never": []},
                "colors":     {"always": [], "never": []},
                "logos":      {"always": [], "never": []},
            },
            "formatting": {"always": [], "never": []},
            "language":   {"always": [], "never": []},
            "structure":  {"always": [], "never": []},
        },
    }


# ---------------------------------------------------------------------------
# Settings routes
# ---------------------------------------------------------------------------


@app.get("/settings")
def settings_page():
    return render_template("settings.html")


@app.get("/api/settings")
def api_settings_get():
    if SETTINGS_FILE.exists():
        try:
            return jsonify(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    return jsonify({})


@app.post("/api/settings")
def api_settings_post():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Invalid JSON body"}), 400

    # Persist raw settings for round-trip editing
    SETTINGS_FILE.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    # Regenerate the custom template so it's immediately available for linting.
    # Written to /tmp so it works on read-only serverless filesystems.
    template_json = _build_custom_template(payload)
    _CUSTOM_TEMPLATE_PATH.write_text(
        json.dumps(template_json, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True, port=5000)
