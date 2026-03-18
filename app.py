"""
Brand Linter – Flask web application.

Routes
------
GET  /                upload form (lists available templates)
POST /lint            upload .docx + template → JSON lint report + session id
POST /download        session id + accepted rule ids → corrected .docx
GET  /settings        settings UI
GET  /api/settings    return current user settings as JSON

Persistence
-----------
Templates are stored in Supabase (PostgreSQL).  There is exactly ONE source
of truth:

    user_templates table
    ├── id            TEXT PRIMARY KEY   (UUID v4, generated in Python)
    ├── template_name TEXT               (display name)
    ├── ui_json       JSONB              (what the user typed in the form)
    └── updated_at    TIMESTAMPTZ        (set to NOW() on every write)

ui_json is the authoritative record.  The linter-format JSON is derived from
ui_json at lint time and never stored.  No demo data is pre-seeded; the table
starts empty and is populated only by explicit user actions (create/edit/delete
via the Settings UI).

Required environment variables
-------------------------------
    SUPABASE_URL  – project URL, e.g. https://<ref>.supabase.co
    SUPABASE_KEY  – service-role secret key (or anon key with appropriate RLS)
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

from flask import Flask, jsonify, render_template, request, send_file
from supabase import create_client, Client

from brand_linter.executor import run as lint_run
from brand_linter.loader import load_template_from_dict
from brand_linter.models import Bucket, TextSubstitutionRule, TextProhibitionRule, StyleRule
from brand_linter.parser import parse_document
from brand_linter.writer import build_corrected_document

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = Flask(__name__, template_folder="web/templates", static_folder="web/static")

UPLOAD_DIR = Path("/tmp/brand_linter_sessions")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Supabase client  (lazy — initialised on first use, not at import time)
# ---------------------------------------------------------------------------

_supabase: Client | None = None
_TABLE = "user_templates"


def _get_supabase() -> Client:
    global _supabase
    if _supabase is None:
        url = os.environ.get("SUPABASE_URL", "")
        key = os.environ.get("SUPABASE_KEY", "")
        # --- TEMPORARY STARTUP DIAGNOSTICS (remove after confirming env vars) ---
        print("[DIAG] SUPABASE_URL present:", bool(url), flush=True)
        print("[DIAG] SUPABASE_KEY present:", bool(key), flush=True)
        print("[DIAG] SUPABASE_URL value:", url or "(not set)", flush=True)
        print("[DIAG] SUPABASE_KEY prefix:", (key[:8] + "...") if key else "(not set)", flush=True)
        # --- END DIAGNOSTICS ---
        if not url or not key:
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_KEY environment variables are not set. "
                "Add them in the Vercel project settings → Environment Variables."
            )
        _supabase = create_client(url, key)
    return _supabase

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


class TypographySection(TypedDict, total=False):
    """Typography settings for a single paragraph style (H1, Body, Captions).

    All fields are optional/nullable — None means "no rule enforced for this
    attribute".  This mirrors exactly what the Settings form sends.
    """
    font_family: str | None
    font_size: float | None
    font_color: str | None
    bold: bool | None
    italic: bool | None


class TemplateRecord(TypedDict):
    """A brand template as stored in the database.

    ui_json in the DB is the serialised form of:
        {
            "template_name": str,
            "typography": {
                "h1":       TypographySection,
                "body":     TypographySection,
                "captions": TypographySection,
            }
        }
    """
    id: str
    template_name: str
    typography: dict[str, TypographySection]
    updated_at: float


# ---------------------------------------------------------------------------
# Constants (linter rule generation)
# ---------------------------------------------------------------------------

# Default field values in the Settings form UI.  A field still at its default
# is treated as "no rule enforced" and omitted from the generated require block.
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
_sessions: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_template_list() -> list[dict[str, str]]:
    """Return [{slug, name}, …] for all saved templates, oldest first."""
    rows = (
        _get_supabase().table(_TABLE)
        .select("id, template_name")
        .order("updated_at")
        .execute()
        .data
    )
    return [{"slug": r["id"], "name": r["template_name"] or "(Untitled)"} for r in rows]


def _build_template_json(settings: dict) -> dict:
    """Convert UI settings dict → linter-compatible template JSON.

    Fields left null by the frontend are treated as "no rule set" and
    omitted from the generated require block so the linter never flags them.
    """
    template_name = (settings.get("template_name") or "Custom Rules").strip() or "Custom Rules"
    typography = settings.get("typography", {})

    always_rules = []
    for section_key, style_name in _STYLE_NAMES.items():
        section = typography.get(section_key) or {}
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
            always_rules.append({
                "type": "style",
                "id": f"custom-{section_key}-001",
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


def _load_template_for_lint(slug: str):
    """Read a template from Supabase and return (template_name, rules).

    Derives the linter-format JSON from ui_json at call time — nothing is
    written to disk.  Returns (None, None) if the slug is not found.
    """
    result = (
        _get_supabase().table(_TABLE)
        .select("template_name, ui_json")
        .eq("id", slug)
        .maybe_single()
        .execute()
    )
    if not result.data:
        return None, None
    row = result.data
    # ui_json is JSONB in Supabase — already parsed to a dict by the client
    ui = row["ui_json"] if isinstance(row["ui_json"], dict) else json.loads(row["ui_json"])
    settings = {
        "template_name": row["template_name"],
        "typography":    ui.get("typography", {}),
    }
    doc = _build_template_json(settings)
    template_name, rules = load_template_from_dict(doc)
    return template_name, rules


def _write_user_template(tid: str, settings: dict) -> None:
    """Persist a user template to Supabase (upsert).

    Only ui_json is stored — the linter-format JSON is derived on demand at
    lint time by _load_template_for_lint().
    """
    ui = {
        "template_name": settings.get("template_name", ""),
        "typography":    settings.get("typography", {}),
    }
    now = datetime.now(timezone.utc).isoformat()
    _get_supabase().table(_TABLE).upsert({
        "id":            tid,
        "template_name": settings.get("template_name", ""),
        "ui_json":       ui,
        "updated_at":    now,
    }).execute()


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
    template_name, rules = _load_template_for_lint(slug)
    if rules is None:
        return jsonify({"error": f"Unknown template: '{slug}'"}), 400

    # --- save upload -------------------------------------------------------
    session_id = str(uuid.uuid4())
    docx_path = UPLOAD_DIR / f"{session_id}.docx"
    uploaded.save(str(docx_path))

    # --- run linter --------------------------------------------------------
    try:
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

    changes_by_para: dict[int, list] = {}
    for c in report.changes:
        changes_by_para.setdefault(c.paragraph_index, []).append(c)

    violations_by_para: dict[int, list] = {}
    for v in report.violations:
        violations_by_para.setdefault(v.paragraph_index, []).append(v)

    para_payload = []
    for para in paragraphs:
        idx = para.index
        para_changes    = changes_by_para.get(idx, [])
        para_violations = violations_by_para.get(idx, [])

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

        para_payload.append({
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
        })

    return jsonify({
        "session_id":    session_id,
        "template_name": template_name,
        "summary":       report.summary(),
        "paragraphs":    para_payload,
    })


@app.post("/download")
def download():
    payload = request.get_json(silent=True) or {}
    session_id   = payload.get("session_id", "")
    accepted_ids: set[str] = set(payload.get("accepted_rule_ids", []))

    session = _sessions.get(session_id)
    if session is None:
        return jsonify({"error": "Session not found or expired"}), 404

    buf = build_corrected_document(
        session["docx_path"], session["rules"], accepted_ids
    )

    stem          = Path(session["original_filename"]).stem
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
# Settings / template routes
# ---------------------------------------------------------------------------


@app.get("/settings")
def settings_page():
    templates = _load_template_list()
    return render_template("settings.html", templates=templates)


@app.get("/api/settings")
def api_settings_get():
    """Return the most-recently saved template name for the header badge."""
    result = (
        _get_supabase().table(_TABLE)
        .select("template_name")
        .order("updated_at", desc=True)
        .limit(1)
        .execute()
    )
    if result.data and result.data[0].get("template_name"):
        return jsonify({"template_name": result.data[0]["template_name"]})
    return jsonify({})


# ── User template CRUD ────────────────────────────────────────────────


@app.get("/api/templates")
def api_templates_list():
    rows = (
        _get_supabase().table(_TABLE)
        .select("id, template_name")
        .order("updated_at")
        .execute()
        .data
    )
    return jsonify([{"id": r["id"], "template_name": r["template_name"]} for r in rows])


@app.post("/api/templates")
def api_templates_create():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Invalid body"}), 400
    tid = str(uuid.uuid4())
    try:
        _write_user_template(tid, payload)
    except Exception as exc:
        app.logger.exception("Failed to write template")
        return jsonify({"error": str(exc)}), 500
    return jsonify({"id": tid, "template_name": payload.get("template_name", "")}), 201


@app.get("/api/templates/<tid>")
def api_templates_get(tid: str):
    result = (
        _get_supabase().table(_TABLE)
        .select("template_name, ui_json")
        .eq("id", tid)
        .maybe_single()
        .execute()
    )
    if not result.data:
        return jsonify({"error": "Not found"}), 404
    row = result.data
    ui = row["ui_json"] if isinstance(row["ui_json"], dict) else json.loads(row["ui_json"])
    return jsonify({
        "id":            tid,
        "template_name": row["template_name"],
        "typography":    ui.get("typography", {}),
    })


@app.put("/api/templates/<tid>")
def api_templates_update(tid: str):
    exists = (
        _get_supabase().table(_TABLE)
        .select("id")
        .eq("id", tid)
        .maybe_single()
        .execute()
    )
    if not exists.data:
        return jsonify({"error": "Not found"}), 404
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Invalid body"}), 400
    _write_user_template(tid, payload)
    return jsonify({"ok": True})


@app.delete("/api/templates/<tid>")
def api_templates_delete(tid: str):
    _get_supabase().table(_TABLE).delete().eq("id", tid).execute()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True, port=5000)
