"""
Brand Linter – Flask web application.

Routes
------
GET  /                upload form (lists available templates)
POST /lint            upload .docx + template → JSON lint report + session id
POST /download        session id + accepted rule ids → corrected .docx
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file

from brand_linter.executor import run as lint_run
from brand_linter.loader import load_template
from brand_linter.models import TextSubstitutionRule
from brand_linter.parser import parse_document
from brand_linter.writer import build_corrected_document

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = Flask(__name__, template_folder="web/templates", static_folder="web/static")

TEMPLATES_DIR = Path("templates")
UPLOAD_DIR = Path("/tmp/brand_linter_sessions")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# In-memory session store.  Maps session_id → dict with docx path + rules.
# Sessions last for the lifetime of the process (sufficient for single-user
# desktop use; swap for Redis/filesystem for multi-user deployments).
_sessions: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_template_list() -> list[dict[str, str]]:
    """Return [{slug, name}, …] for every template in TEMPLATES_DIR."""
    result = []
    for p in sorted(TEMPLATES_DIR.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            name = data.get("template_name", p.stem)
        except Exception:
            name = p.stem
        result.append({"slug": p.stem, "name": name})
    return result


def _find_template_path(slug: str) -> Path | None:
    candidate = TEMPLATES_DIR / f"{slug}.json"
    return candidate if candidate.exists() else None


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
                "original": para.text,
                "corrected": report.corrected_paragraphs[idx],
                "has_changes": bool(para_changes),
                "has_violations": bool(para_violations),
                "changes": change_details,
                "violations": [
                    {
                        "rule_id": v.rule_id,
                        "description": v.rule_description,
                        "detail": v.detail,
                    }
                    for v in para_violations
                ],
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
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True, port=5000)
