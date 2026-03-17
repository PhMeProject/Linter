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
Templates are stored in a single SQLite file (brand_linter.db) next to this
module.  There is exactly ONE source of truth:

    user_templates table
    ├── id            TEXT PRIMARY KEY   (UUID v4)
    ├── template_name TEXT               (display name)
    ├── ui_json       TEXT               (JSON — what the user typed in the form)
    └── updated_at    REAL               (Unix timestamp)

ui_json is the authoritative record.  The linter-format JSON is derived from
ui_json at lint time and never stored.  No demo data is pre-seeded; the table
starts empty and is populated only by explicit user actions (create/edit/delete
via the Settings UI).

Moving to a real database
-------------------------
Replace _get_db() with a connection to PostgreSQL or MySQL.  All queries use
standard ANSI SQL.  The one non-portable construct is the upsert:

    INSERT ... ON CONFLICT(id) DO UPDATE SET ...   ← SQLite / PostgreSQL
    INSERT ... ON DUPLICATE KEY UPDATE ...          ← MySQL equivalent

Everything else — the contextmanager, all routes, all helper functions —
stays identical.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import TypedDict

from flask import Flask, jsonify, render_template, request, send_file

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
# SQLite persistence
# ---------------------------------------------------------------------------
# Priority: DATABASE_PATH env var → project root → /tmp fallback.
# The /tmp fallback is ephemeral (data lost on restart); set DATABASE_PATH
# to a writable persistent path (e.g. a mounted volume) in production.

def _resolve_db_path() -> Path:
    env = os.environ.get("DATABASE_PATH", "").strip()
    if env:
        p = Path(env).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    # Always resolve to an absolute path so SQLite opens the same file
    # regardless of the process working directory at connection time.
    local = (Path(__file__).parent / "brand_linter.db").resolve()
    try:
        local.touch()
        return local
    except OSError:
        tmp = Path("/tmp/brand_linter_state/brand_linter.db").resolve()
        tmp.parent.mkdir(parents=True, exist_ok=True)
        return tmp


DB_PATH = _resolve_db_path()


def _get_db() -> sqlite3.Connection:
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    return con


@contextmanager
def _db():
    con = _get_db()
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def _init_db() -> None:
    """Create the user_templates table and drop any legacy columns."""
    with _db() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS user_templates (
                id            TEXT PRIMARY KEY,
                template_name TEXT NOT NULL DEFAULT '',
                ui_json       TEXT NOT NULL,
                updated_at    REAL NOT NULL
            )
        """)

    # Drop the legacy doc_json column if it exists from an older schema.
    # doc_json was a derived copy of ui_json; it is now computed on demand at
    # lint time so there is no reason to store it.
    _drop_legacy_column("doc_json")


def _drop_legacy_column(column: str) -> None:
    """Remove a column from user_templates if it exists (SQLite-safe)."""
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    try:
        cols = {row["name"] for row in con.execute("PRAGMA table_info(user_templates)")}
        if column not in cols:
            return
        con.execute("BEGIN")
        con.execute("""
            CREATE TABLE user_templates_new (
                id            TEXT PRIMARY KEY,
                template_name TEXT NOT NULL DEFAULT '',
                ui_json       TEXT NOT NULL,
                updated_at    REAL NOT NULL
            )
        """)
        con.execute(
            "INSERT INTO user_templates_new "
            "SELECT id, template_name, ui_json, updated_at FROM user_templates"
        )
        con.execute("DROP TABLE user_templates")
        con.execute("ALTER TABLE user_templates_new RENAME TO user_templates")
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


_init_db()

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
    with _db() as con:
        rows = con.execute(
            "SELECT id, template_name FROM user_templates ORDER BY updated_at"
        ).fetchall()
    return [{"slug": row["id"], "name": row["template_name"] or "(Untitled)"} for row in rows]


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
    """Read a template from the DB and return (template_name, rules).

    Derives the linter-format JSON from ui_json at call time — nothing is
    written to disk.  Returns (None, None) if the slug is not in the DB.
    """
    with _db() as con:
        row = con.execute(
            "SELECT template_name, ui_json FROM user_templates WHERE id = ?", (slug,)
        ).fetchone()
    if not row:
        return None, None
    ui = json.loads(row["ui_json"])
    settings = {
        "template_name": row["template_name"],
        "typography":    ui.get("typography", {}),
    }
    doc = _build_template_json(settings)
    template_name, rules = load_template_from_dict(doc)
    return template_name, rules


def _write_user_template(tid: str, settings: dict) -> None:
    """Persist a user template to SQLite.

    Only ui_json is stored — the linter doc_json is derived on demand at
    lint time by _load_template_for_lint().
    """
    ui = {
        "template_name": settings.get("template_name", ""),
        "typography":    settings.get("typography", {}),
    }
    with _db() as con:
        con.execute("""
            INSERT INTO user_templates (id, template_name, ui_json, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                template_name = excluded.template_name,
                ui_json       = excluded.ui_json,
                updated_at    = excluded.updated_at
        """, (
            tid,
            settings.get("template_name", ""),
            json.dumps(ui, ensure_ascii=False),
            time.time(),
        ))


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
    with _db() as con:
        row = con.execute(
            "SELECT template_name FROM user_templates ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
    if row and row["template_name"]:
        return jsonify({"template_name": row["template_name"]})
    return jsonify({})


# ── User template CRUD ────────────────────────────────────────────────


@app.get("/api/templates")
def api_templates_list():
    with _db() as con:
        rows = con.execute(
            "SELECT id, template_name FROM user_templates ORDER BY updated_at"
        ).fetchall()
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
    with _db() as con:
        row = con.execute(
            "SELECT template_name, ui_json FROM user_templates WHERE id = ?", (tid,)
        ).fetchone()
    if not row:
        return jsonify({"error": "Not found"}), 404
    ui = json.loads(row["ui_json"])
    return jsonify({
        "id":            tid,
        "template_name": row["template_name"],
        "typography":    ui.get("typography", {}),
    })


@app.put("/api/templates/<tid>")
def api_templates_update(tid: str):
    with _db() as con:
        exists = con.execute(
            "SELECT 1 FROM user_templates WHERE id = ?", (tid,)
        ).fetchone()
    if not exists:
        return jsonify({"error": "Not found"}), 404
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Invalid body"}), 400
    _write_user_template(tid, payload)
    return jsonify({"ok": True})


@app.delete("/api/templates/<tid>")
def api_templates_delete(tid: str):
    with _db() as con:
        con.execute("DELETE FROM user_templates WHERE id = ?", (tid,))
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True, port=5000)
