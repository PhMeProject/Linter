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
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
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
    with _db() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS user_templates (
                id           TEXT PRIMARY KEY,
                template_name TEXT NOT NULL DEFAULT '',
                doc_json     TEXT NOT NULL,
                ui_json      TEXT NOT NULL,
                updated_at   REAL NOT NULL
            )
        """)


def _migrate_json_templates(con: sqlite3.Connection) -> None:
    """One-time import: pull any user_templates/*.json files into SQLite.

    Also recovers templates from UPLOAD_DIR tpl_*.json cache files (written
    by _find_template_path when linting) in case the DB was ever cleared.
    Files written by the old file-based storage layer are inserted using
    INSERT OR IGNORE so already-migrated rows are never overwritten.
    """
    def _insert_json_file(p: Path, tid: str) -> None:
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return
        template_name = doc.get("template_name", "")
        ui = doc.get("_ui") or {"template_name": template_name, "typography": {}}
        con.execute(
            """
            INSERT OR IGNORE INTO user_templates
                (id, template_name, doc_json, ui_json, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                tid,
                template_name,
                json.dumps(doc, ensure_ascii=False),
                json.dumps(ui, ensure_ascii=False),
                p.stat().st_mtime,
            ),
        )

    # 1. Old file-based storage (user_templates/*.json)
    json_dir = Path(__file__).parent / "user_templates"
    if json_dir.is_dir():
        for p in sorted(json_dir.glob("*.json")):
            tid = p.stem
            if tid and not tid.startswith("."):
                _insert_json_file(p, tid)

    # 2. Lint-session cache (UPLOAD_DIR/tpl_*.json) — recovery fallback
    for p in sorted(UPLOAD_DIR.glob("tpl_*.json")):
        tid = p.stem[4:]  # strip "tpl_" prefix
        if tid and not tid.startswith("."):
            _insert_json_file(p, tid)

_init_db()

# Migrate templates saved by the old file-based storage into SQLite.
# Skipped when DATABASE_PATH is set (test environments use isolated temp DBs).
if not os.environ.get("DATABASE_PATH"):
    with _db() as _con:
        _migrate_json_templates(_con)

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
    """Return [{slug, name}, …] for user-created templates only."""
    with _db() as con:
        rows = con.execute(
            "SELECT id, template_name FROM user_templates ORDER BY updated_at"
        ).fetchall()
    return [{"slug": row["id"], "name": row["template_name"] or "(Untitled)"} for row in rows]


def _find_template_path(slug: str) -> Path | None:
    """Return a Path the loader can read.  User templates are written to a temp file."""
    with _db() as con:
        row = con.execute(
            "SELECT doc_json FROM user_templates WHERE id = ?", (slug,)
        ).fetchone()
    if not row:
        return None
    tmp = UPLOAD_DIR / f"tpl_{slug}.json"
    tmp.write_text(row["doc_json"], encoding="utf-8")
    return tmp


def _write_user_template(tid: str, settings: dict) -> None:
    """Persist a user template to SQLite."""
    doc = _build_template_json(settings)
    doc["id"] = tid
    ui = {"template_name": settings.get("template_name", ""),
          "typography":    settings.get("typography", {})}
    doc["_ui"] = ui
    with _db() as con:
        con.execute("""
            INSERT INTO user_templates (id, template_name, doc_json, ui_json, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                template_name = excluded.template_name,
                doc_json      = excluded.doc_json,
                ui_json       = excluded.ui_json,
                updated_at    = excluded.updated_at
        """, (
            tid,
            settings.get("template_name", ""),
            json.dumps(doc, ensure_ascii=False),
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
# Template helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Settings / template routes
# ---------------------------------------------------------------------------


@app.get("/settings")
def settings_page():
    templates = _load_template_list()
    return render_template("settings.html", templates=templates)


@app.get("/api/settings")
def api_settings_get():
    """Return the most-recently saved user template name for the header badge."""
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
